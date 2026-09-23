from __future__ import annotations

import json
from pathlib import Path

import pytest

from veraport_agent.controller_capability_upgrade import (
    ControllerCapabilityUpgradeError,
    TARGET_CAPABILITIES,
    WRITE_OPERATIONS,
    upgrade_tunnel_controller_readwrite,
)
from veraport_agent.controller_config import ControllerConfig
from veraport_agent.existing_install_attach import prepare_existing_install_tunnel_attach
from veraport_agent.identity_store import load_identity
from veraport_agent.hot_session import principal_id
from veraport_agent.provision import provision_local_pair


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

    workstation = identity / "workstation.pem"
    workstation.write_bytes((identity / "workstation-key.pem").read_bytes())
    controllers = identity / "controllers.json"
    trust = json.loads((identity / "controller-trust.json").read_text(encoding="utf-8"))
    trust["controllers"][0]["capabilities"] = ["fs.read"]
    controllers.write_text(
        json.dumps(trust, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    service = root / "veraport.json"
    service.write_text(
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
            }
        ),
        encoding="utf-8",
    )

    controller_key = root / "controller" / "vera-controller-bootstrap.pem"
    controller_key.parent.mkdir()
    controller_key.write_bytes((identity / "controller-key.pem").read_bytes())

    tunnel_client = root / "bin" / "tunnel-client.exe"
    tunnel_client.parent.mkdir()
    tunnel_client.write_bytes(b"tunnel")
    runtime_key = root / "secrets" / "tunnel-runtime.key"
    runtime_key.parent.mkdir()
    runtime_key.write_text("x" * 48, encoding="utf-8")
    mcp = root / "runtime" / "Scripts" / "veraport-mcp-stdio.exe"
    mcp.parent.mkdir(parents=True)
    mcp.write_bytes(b"mcp")

    prepare_existing_install_tunnel_attach(
        service_config_path=service,
        controller_private_key_path=controller_key,
        tunnel_client_path=tunnel_client,
        tunnel_id="tunnel_0123456789abcdef0123456789abcdef",
        runtime_api_key_file=runtime_key,
        mcp_executable_path=mcp,
        harden_windows_acl=False,
    )
    return {
        "root": root,
        "identity": identity,
        "service": service,
        "controllers": controllers,
        "controller_config": root / "controller.json",
        "controller_key": controller_key,
        "principal": manifest["controller_principal"],
        "allowed": allowed,
    }


def test_upgrade_exact_readonly_controller_to_exact_readwrite(tmp_path):
    fx = fixture(tmp_path)
    before_service = fx["service"].read_bytes()
    before_allowed = fx["allowed"].resolve()

    result = upgrade_tunnel_controller_readwrite(
        service_config_path=fx["service"],
        controller_config_path=fx["controller_config"],
    )

    assert result["capabilities_before"] == ["fs.read"]
    assert result["capabilities_after"] == ["fs.read", "fs.write"]
    assert result["process_execution_enabled"] is False
    assert result["old_controller_entries_preserved"] is True
    assert Path(result["trust_backup"]).is_file()
    assert Path(result["controller_config_backup"]).is_file()

    service_value = json.loads(fx["service"].read_text(encoding="utf-8"))
    assert fx["service"].read_bytes() == before_service
    assert Path(service_value["allowed_roots"][0]).resolve() == before_allowed
    assert service_value["allow_process_exec"] is False

    config = ControllerConfig.load(fx["controller_config"])
    assert config.requested_capabilities == TARGET_CAPABILITIES
    assert set(WRITE_OPERATIONS).issubset(config.gateway_operations)
    assert not any(op.startswith("process.") for op in config.gateway_operations)

    private = __import__(
        "cryptography.hazmat.primitives.serialization",
        fromlist=["load_pem_private_key"],
    ).load_pem_private_key(fx["controller_key"].read_bytes(), password=None)
    principal = principal_id(private.public_key(), "controller")
    identity = load_identity(
        workstation_key_path=Path(service_value["workstation_key"]),
        controller_trust_path=fx["controllers"],
    )
    assert identity.capability_policy[principal] == TARGET_CAPABILITIES


def test_upgrade_is_idempotent(tmp_path):
    fx = fixture(tmp_path)
    first = upgrade_tunnel_controller_readwrite(
        service_config_path=fx["service"],
        controller_config_path=fx["controller_config"],
    )
    trust_after = fx["controllers"].read_bytes()
    controller_after = fx["controller_config"].read_bytes()

    second = upgrade_tunnel_controller_readwrite(
        service_config_path=fx["service"],
        controller_config_path=fx["controller_config"],
    )
    assert second["capabilities_before"] == ["fs.read", "fs.write"]
    assert fx["controllers"].read_bytes() == trust_after
    assert fx["controller_config"].read_bytes() == controller_after
    assert first["controller_principal"] == second["controller_principal"]


def test_upgrade_refuses_process_bearing_or_broader_controller(tmp_path):
    fx = fixture(tmp_path)
    trust = json.loads(fx["controllers"].read_text(encoding="utf-8"))
    trust["controllers"][0]["capabilities"] = [
        "fs.read",
        "fs.write",
        "process.exec",
    ]
    fx["controllers"].write_text(
        json.dumps(trust, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ControllerCapabilityUpgradeError,
        match="must be exactly fs.read",
    ):
        upgrade_tunnel_controller_readwrite(
            service_config_path=fx["service"],
            controller_config_path=fx["controller_config"],
        )


def test_upgrade_refuses_when_process_policy_enabled(tmp_path):
    fx = fixture(tmp_path)
    service = json.loads(fx["service"].read_text(encoding="utf-8"))
    service["allow_process_exec"] = True
    fx["service"].write_text(json.dumps(service), encoding="utf-8")
    with pytest.raises(
        ControllerCapabilityUpgradeError,
        match="process execution is enabled",
    ):
        upgrade_tunnel_controller_readwrite(
            service_config_path=fx["service"],
            controller_config_path=fx["controller_config"],
        )
