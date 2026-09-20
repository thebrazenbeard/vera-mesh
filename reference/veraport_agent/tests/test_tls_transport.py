from __future__ import annotations

import asyncio
import datetime
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from veraport_agent.hot_session import WorkstationAuthenticator, principal_id
from veraport_agent.tls_transport import (
    SessionRejected,
    make_client_context,
    make_server_context,
    open_tls_session,
    serve_tls_connection,
)


def cert_files(tmp_path: Path):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@pytest.mark.asyncio
async def test_tls13_hot_session_mutual_auth_and_parallel_requests(tmp_path):
    cert, key = cert_files(tmp_path)
    workstation_key = ec.generate_private_key(ec.SECP256R1())
    controller_key = ec.generate_private_key(ec.SECP256R1())
    controller_principal = principal_id(controller_key.public_key(), "controller")
    authenticator = WorkstationAuthenticator(
        workstation_private_key=workstation_key,
        allowed_controllers={controller_principal: controller_key.public_key()},
        capability_policy={controller_principal: frozenset({"fs.read"})},
    )

    active = 0
    max_active = 0

    async def handler(request):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(request["delay"])
        active -= 1
        return {
            "protocol_version": "veraport-v1",
            "request_id": request["request_id"],
            "ok": True,
            "result": {"id": request["request_id"]},
        }

    def factory(binding):
        assert binding.granted_capabilities == frozenset({"fs.read"})
        return handler

    server = await asyncio.start_server(
        lambda reader, writer: serve_tls_connection(
            reader,
            writer,
            authenticator=authenticator,
            handler_factory=factory,
            now_ms=lambda: 1000,
        ),
        "127.0.0.1",
        0,
        ssl=make_server_context(certfile=cert, keyfile=key),
    )
    port = server.sockets[0].getsockname()[1]

    client, binding = await open_tls_session(
        host="127.0.0.1",
        port=port,
        ssl_context=make_client_context(cafile=cert),
        server_hostname="localhost",
        controller_private_key=controller_key,
        workstation_public_key=workstation_key.public_key(),
        requested_capabilities={"fs.read"},
        now_ms=lambda: 1001,
    )
    try:
        slow, fast = await asyncio.gather(
            client.request({"request_id": "slow", "delay": 0.06}),
            client.request({"request_id": "fast", "delay": 0.01}),
        )
        assert max_active >= 2
        assert binding.granted_capabilities == frozenset({"fs.read"})
        assert {slow["request_id"], fast["request_id"]} == {"slow", "fast"}
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_unauthorized_controller_rejected_over_tls(tmp_path):
    cert, key = cert_files(tmp_path)
    workstation_key = ec.generate_private_key(ec.SECP256R1())
    enrolled_key = ec.generate_private_key(ec.SECP256R1())
    rogue_key = ec.generate_private_key(ec.SECP256R1())
    enrolled_principal = principal_id(enrolled_key.public_key(), "controller")
    authenticator = WorkstationAuthenticator(
        workstation_private_key=workstation_key,
        allowed_controllers={enrolled_principal: enrolled_key.public_key()},
        capability_policy={enrolled_principal: frozenset({"fs.read"})},
    )

    async def handler(request):
        return {"request_id": request["request_id"], "ok": True, "result": {}}

    server = await asyncio.start_server(
        lambda reader, writer: serve_tls_connection(
            reader,
            writer,
            authenticator=authenticator,
            handler_factory=lambda binding: handler,
            now_ms=lambda: 1000,
        ),
        "127.0.0.1",
        0,
        ssl=make_server_context(certfile=cert, keyfile=key),
    )
    port = server.sockets[0].getsockname()[1]

    try:
        with pytest.raises(SessionRejected):
            await open_tls_session(
                host="127.0.0.1",
                port=port,
                ssl_context=make_client_context(cafile=cert),
                server_hostname="localhost",
                controller_private_key=rogue_key,
                workstation_public_key=workstation_key.public_key(),
                requested_capabilities={"fs.read"},
                now_ms=lambda: 1001,
            )
    finally:
        server.close()
        await server.wait_closed()
