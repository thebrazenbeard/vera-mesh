from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtensionOID

from veraport_agent.identity_store import load_identity
from veraport_agent.provision import (
    FILESYSTEM_CAPABILITIES,
    PROCESS_CAPABILITIES,
    ProvisionError,
    provision_local_pair,
)


def test_provision_local_pair_is_filesystem_only_by_default(tmp_path: Path):
    now = dt.datetime(2026, 9, 22, tzinfo=dt.timezone.utc)
    manifest = provision_local_pair(tmp_path / "identity", now=now)
    root = tmp_path / "identity"

    assert manifest["capabilities"] == sorted(FILESYSTEM_CAPABILITIES)
    trust = json.loads(
        (root / "controller-trust.json").read_text(encoding="utf-8")
    )
    assert trust["controllers"][0]["capabilities"] == sorted(
        FILESYSTEM_CAPABILITIES
    )

    loaded = load_identity(
        workstation_key_path=root / "workstation-key.pem",
        controller_trust_path=root / "controller-trust.json",
    )
    assert set(loaded.capability_policy.values()) == {
        frozenset(FILESYSTEM_CAPABILITIES)
    }

    controller_private = serialization.load_pem_private_key(
        (root / "controller-key.pem").read_bytes(),
        password=None,
    )
    assert (
        controller_private.public_key().public_numbers()
        == serialization.load_pem_public_key(
            (root / "controller-public.pem").read_bytes()
        ).public_numbers()
    )

    cert = x509.load_pem_x509_certificate(
        (root / "tls-cert.pem").read_bytes()
    )
    san = cert.extensions.get_extension_for_oid(
        ExtensionOID.SUBJECT_ALTERNATIVE_NAME
    ).value
    assert "localhost" in san.get_values_for_type(x509.DNSName)
    ips = {str(value) for value in san.get_values_for_type(x509.IPAddress)}
    assert ips == {"127.0.0.1", "::1"}
    assert (root / "tls-ca.pem").read_bytes() == (
        root / "tls-cert.pem"
    ).read_bytes()


def test_process_capabilities_require_explicit_provision_choice(tmp_path: Path):
    caps = set(FILESYSTEM_CAPABILITIES | PROCESS_CAPABILITIES)
    manifest = provision_local_pair(
        tmp_path / "identity",
        capabilities=caps,
    )
    assert manifest["capabilities"] == sorted(caps)


def test_provision_refuses_unknown_capability_and_overwrite(tmp_path: Path):
    root = tmp_path / "identity"
    with pytest.raises(ProvisionError, match="unknown capabilities"):
        provision_local_pair(
            root,
            capabilities={"fs.read", "fs.write", "host.root"},
        )

    provision_local_pair(root)
    original = (root / "controller-key.pem").read_bytes()
    with pytest.raises(ProvisionError, match="refusing to provision"):
        provision_local_pair(root)
    assert (root / "controller-key.pem").read_bytes() == original


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode assertion")
def test_private_material_is_created_mode_0600_on_posix(tmp_path: Path):
    root = tmp_path / "identity"
    provision_local_pair(root)
    for name in (
        "workstation-key.pem",
        "controller-key.pem",
        "tls-key.pem",
    ):
        assert (root / name).stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL assertion")
def test_provisioned_windows_material_matches_service_acl(tmp_path: Path):
    from veraport_agent.windows_acl import (
        validate_private_directory,
        validate_private_file,
    )

    root = tmp_path / "identity"
    provision_local_pair(root)
    validate_private_directory(root)
    for path in root.iterdir():
        if path.is_file():
            validate_private_file(path)



@pytest.mark.asyncio
async def test_provisioned_material_completes_real_tls_application_handshake(tmp_path):
    from veraport_agent.hot_session import WorkstationAuthenticator
    from veraport_agent.tls_transport import (
        make_client_context,
        make_server_context,
        open_tls_session,
        serve_tls_connection,
    )

    root = tmp_path / "identity"
    provision_local_pair(root)
    identity = load_identity(
        workstation_key_path=root / "workstation-key.pem",
        controller_trust_path=root / "controller-trust.json",
    )
    controller_private = serialization.load_pem_private_key(
        (root / "controller-key.pem").read_bytes(),
        password=None,
    )
    workstation_public = serialization.load_pem_public_key(
        (root / "workstation-public.pem").read_bytes()
    )
    assert isinstance(controller_private, ec.EllipticCurvePrivateKey)
    assert isinstance(workstation_public, ec.EllipticCurvePublicKey)

    authenticator = WorkstationAuthenticator(
        workstation_private_key=identity.workstation_private_key,
        allowed_controllers=identity.allowed_controllers,
        capability_policy=identity.capability_policy,
    )

    async def handler(request):
        return {
            "protocol_version": "veraport-v1",
            "request_id": request["request_id"],
            "ok": True,
            "result": {"provisioned": True},
        }

    server = await asyncio.start_server(
        lambda reader, writer: serve_tls_connection(
            reader,
            writer,
            authenticator=authenticator,
            handler_factory=lambda binding: handler,
        ),
        "127.0.0.1",
        0,
        ssl=make_server_context(
            certfile=root / "tls-cert.pem",
            keyfile=root / "tls-key.pem",
        ),
    )
    port = server.sockets[0].getsockname()[1]
    client, binding = await open_tls_session(
        host="127.0.0.1",
        port=port,
        ssl_context=make_client_context(cafile=root / "tls-ca.pem"),
        server_hostname="localhost",
        controller_private_key=controller_private,
        workstation_public_key=workstation_public,
        requested_capabilities={"fs.read", "fs.write"},
    )
    try:
        assert binding.granted_capabilities == frozenset(
            {"fs.read", "fs.write"}
        )
        result = await client.request({
            "request_id": "provisioned-round-trip",
        })
        assert result["ok"] is True
        assert result["result"]["provisioned"] is True
    finally:
        await client.close()
        server.close()
        await server.wait_closed()
