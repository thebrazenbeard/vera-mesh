from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from veraport_agent.controller_config import ControllerConfig
from veraport_agent.doctor import offline_doctor
from veraport_agent.existing_install_attach import (
    ExistingInstallAttachError,
    READ_ONLY_GATEWAY_OPERATIONS,
    prepare_existing_install_tunnel_attach,
)
from veraport_agent.provision import provision_local_pair
from veraport_agent.tunnel_runtime_service import TunnelRuntimeServiceConfig


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path: Path):
    root = tmp_path / "VeraMesh"
    identity = root / "identity"
    identity.mkdir(parents=True)
    manifest = provision_local_pair(
        identity,
        capabilities={"fs.read", "fs.write"},
        harden_windows_acl=False,
    )
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    state = root / "state"
    state.mkdir()

    # Model the older Lappy naming through the config, not through current
    # helper assumptions.
    workstation = identity / "workstation.pem"
    workstation.write_bytes((identity / "workstation-key.pem").read_bytes())
    controllers = identity / "controllers.json"
    controllers.write_bytes((identity / "controller-trust.json").read_bytes())

    service_path = root / "veraport.json"
    service_path.write_text(
        json.dumps(
            {
                "bind_host": "127.0.0.1",
                "bind_port": 17444,
                "allowed_roots": [str(allowed)],
                "state_db": str(state / "agent.sqlite3"),
                "tls_cert": str(identity / "tls-cert.pem"),
                "tls_key": str(identity / "tls-key.pem"),
                "workstation_key": str(workstation),
                "controller_trust": str(controllers),
                "allow_process_exec": False,
                "allow_non_loopback_listener": False,
                "max_lanes": 32,
                "max_inflight": 64,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    controller_key = root / "controller" / "vera-controller-bootstrap.pem"
    controller_key.parent.mkdir()
    controller_key.write_bytes((identity / "controller-key.pem").read_bytes())

    tunnel_client = root / "bin" / "tunnel-client.exe"
    tunnel_client.parent.mkdir()
    tunnel_client.write_bytes(b"tunnel-client-fixture")
    runtime_key = root / "secrets" / "tunnel-runtime.key"
    runtime_key.parent.mkdir()
    runtime_key.write_text("runtime-secret", encoding="utf-8")
    mcp = root / "runtime" / "Scripts" / "veraport-mcp-stdio.exe"
    mcp.parent.mkdir(parents=True)
    mcp.write_bytes(b"mcp-fixture")

    return {
        "root": root,
        "service": service_path,
        "identity": identity,
        "controller_key": controller_key,
        "tunnel": tunnel_client,
        "runtime_key": runtime_key,
        "mcp": mcp,
        "principal": manifest["controller_principal"],
    }


def test_existing_attach_preserves_veraport_and_builds_read_only_tunnel(tmp_path):
    fx = fixture(tmp_path)
    protected = [
        fx["service"],
        fx["identity"] / "workstation.pem",
        fx["identity"] / "controllers.json",
        fx["identity"] / "tls-cert.pem",
        fx["identity"] / "tls-key.pem",
    ]
    before = {str(path): sha(path) for path in protected}

    result = prepare_existing_install_tunnel_attach(
        service_config_path=fx["service"],
        controller_private_key_path=fx["controller_key"],
        tunnel_client_path=fx["tunnel"],
        tunnel_id="tunnel_0123456789abcdef0123456789abcdef",
        runtime_api_key_file=fx["runtime_key"],
        mcp_executable_path=fx["mcp"],
        harden_windows_acl=False,
    )

    after = {str(path): sha(path) for path in protected}
    assert before == after
    assert result["existing_veraport"]["controller_principal"] == fx["principal"]
    assert result["existing_veraport"]["process_execution_enabled"] is False
    assert result["tunnel_controller"]["requested_capabilities"] == ["fs.read"]
    assert set(result["tunnel_controller"]["gateway_operations"]) == set(
        READ_ONLY_GATEWAY_OPERATIONS
    )
    assert result["effects"] == {
        "existing_veraport_service_modified": False,
        "existing_veraport_identity_rotated": False,
        "existing_veraport_state_rewritten": False,
        "existing_allowed_roots_changed": False,
        "existing_process_policy_changed": False,
        "tunnel_service_installed": False,
        "tunnel_service_started": False,
    }

    controller = ControllerConfig.load(fx["root"] / "controller.json")
    assert controller.requested_capabilities == frozenset({"fs.read"})
    assert "fs.write_text" not in controller.gateway_operations
    tunnel = TunnelRuntimeServiceConfig.load(
        fx["root"] / "tunnel-runtime.json"
    )
    assert tunnel.controller_config == (fx["root"] / "controller.json").resolve()

    checks, _, _, _ = offline_doctor(
        service_config_path=fx["service"],
        controller_config_path=fx["root"] / "controller.json",
        tunnel_config_path=fx["root"] / "tunnel-runtime.json",
        check_windows_acl=False,
    )
    states = {item.name: item.state for item in checks}
    assert states["service_config"] == "PASS"
    assert states["controller_config"] == "PASS"
    assert states["tunnel_config"] == "PASS"
    assert states["cross_binding"] == "PASS"
    assert states["process_policy"] == "PASS"


def test_existing_attach_reuses_exact_material_without_overwrite(tmp_path):
    fx = fixture(tmp_path)
    kwargs = dict(
        service_config_path=fx["service"],
        controller_private_key_path=fx["controller_key"],
        tunnel_client_path=fx["tunnel"],
        tunnel_id="tunnel_0123456789abcdef0123456789abcdef",
        runtime_api_key_file=fx["runtime_key"],
        mcp_executable_path=fx["mcp"],
        harden_windows_acl=False,
    )
    prepare_existing_install_tunnel_attach(**kwargs)
    controller_sha = sha(fx["root"] / "controller.json")
    second = prepare_existing_install_tunnel_attach(**kwargs)
    assert sha(fx["root"] / "controller.json") == controller_sha
    assert second["write_actions"][str(fx["root"] / "controller.json")] == (
        "reused_exact"
    )


def test_existing_attach_rejects_unenrolled_controller(tmp_path):
    fx = fixture(tmp_path)
    rogue = ec.generate_private_key(ec.SECP256R1())
    from cryptography.hazmat.primitives import serialization

    rogue_path = fx["root"] / "controller" / "rogue.pem"
    rogue_path.write_bytes(
        rogue.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    with pytest.raises(
        ExistingInstallAttachError,
        match="not enrolled",
    ):
        prepare_existing_install_tunnel_attach(
            service_config_path=fx["service"],
            controller_private_key_path=rogue_path,
            tunnel_client_path=fx["tunnel"],
            tunnel_id="tunnel_0123456789abcdef0123456789abcdef",
            runtime_api_key_file=fx["runtime_key"],
            mcp_executable_path=fx["mcp"],
            harden_windows_acl=False,
        )


def test_existing_attach_refuses_divergent_existing_controller_config(tmp_path):
    fx = fixture(tmp_path)
    (fx["root"] / "controller.json").write_text(
        '{"divergent":true}\n',
        encoding="utf-8",
    )
    with pytest.raises(
        ExistingInstallAttachError,
        match="diverges",
    ):
        prepare_existing_install_tunnel_attach(
            service_config_path=fx["service"],
            controller_private_key_path=fx["controller_key"],
            tunnel_client_path=fx["tunnel"],
            tunnel_id="tunnel_0123456789abcdef0123456789abcdef",
            runtime_api_key_file=fx["runtime_key"],
            mcp_executable_path=fx["mcp"],
            harden_windows_acl=False,
        )
