from __future__ import annotations

import json
from pathlib import Path

import pytest

from veraport_agent.controller_config import ControllerConfig
from veraport_agent.controller_full_control_upgrade import (
    PROCESS_OPERATIONS,
    TARGET_CAPABILITIES,
    WRITE_OPERATIONS,
    upgrade_tunnel_controller_full_control,
)
from veraport_agent.existing_install_attach import prepare_existing_install_tunnel_attach
from veraport_agent.hot_session import principal_id
from veraport_agent.identity_store import load_identity
from veraport_agent.full_control_qualification import _result
from veraport_agent.provision import provision_local_pair


def fixture(tmp_path: Path):
    root = tmp_path / "VeraMesh"
    identity = root / "identity"
    identity.mkdir(parents=True)
    provision_local_pair(
        identity,
        capabilities={"fs.read", "fs.write"},
        harden_windows_acl=False,
    )
    trust_path = identity / "controller-trust.json"
    trust = json.loads(trust_path.read_text(encoding="utf-8"))
    trust["controllers"][0]["capabilities"] = ["fs.read", "fs.write"]
    trust_path.write_text(json.dumps(trust, indent=2, sort_keys=True) + "\n")

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    state = root / "state"
    state.mkdir()
    workstation = identity / "workstation.pem"
    workstation.write_bytes((identity / "workstation-key.pem").read_bytes())
    controllers = identity / "controllers.json"
    controllers.write_bytes(trust_path.read_bytes())

    service = root / "veraport.json"
    service.write_text(json.dumps({
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
    }))

    controller_key = root / "controller" / "vera-controller-bootstrap.pem"
    controller_key.parent.mkdir()
    controller_key.write_bytes((identity / "controller-key.pem").read_bytes())
    tunnel_client = root / "bin" / "tunnel-client.exe"
    tunnel_client.parent.mkdir()
    tunnel_client.write_bytes(b"tunnel")
    runtime_key = root / "secrets" / "tunnel-runtime.key"
    runtime_key.parent.mkdir()
    runtime_key.write_text("x" * 48)
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

    # Bring the fixture to the already-qualified read/write predecessor state.
    controller = json.loads((root / "controller.json").read_text())
    controller["requested_capabilities"] = ["fs.read", "fs.write"]
    for op in WRITE_OPERATIONS:
        if op not in controller["gateway_operations"]:
            controller["gateway_operations"].append(op)
    (root / "controller.json").write_text(
        json.dumps(controller, indent=2, sort_keys=True) + "\n"
    )
    return root, service, controllers, root / "controller.json", controller_key


def test_full_control_upgrade_enables_exact_process_surface(tmp_path):
    root, service, controllers, controller_config, controller_key = fixture(tmp_path)
    result = upgrade_tunnel_controller_full_control(
        service_config_path=service,
        controller_config_path=controller_config,
    )
    assert result["process_execution_enabled"] is True
    assert result["capabilities_after"] == sorted(TARGET_CAPABILITIES)

    service_doc = json.loads(service.read_text())
    assert service_doc["allow_process_exec"] is True
    config = ControllerConfig.load(controller_config)
    assert config.requested_capabilities == TARGET_CAPABILITIES
    assert set(WRITE_OPERATIONS).issubset(config.gateway_operations)
    assert set(PROCESS_OPERATIONS).issubset(config.gateway_operations)

    private = __import__(
        "cryptography.hazmat.primitives.serialization",
        fromlist=["load_pem_private_key"],
    ).load_pem_private_key(controller_key.read_bytes(), password=None)
    principal = principal_id(private.public_key(), "controller")
    identity = load_identity(
        workstation_key_path=Path(service_doc["workstation_key"]),
        controller_trust_path=controllers,
    )
    assert identity.capability_policy[principal] == TARGET_CAPABILITIES
    assert Path(result["service_config_backup"]).is_file()


def test_full_control_upgrade_is_idempotent(tmp_path):
    _, service, _, controller_config, _ = fixture(tmp_path)
    first = upgrade_tunnel_controller_full_control(
        service_config_path=service,
        controller_config_path=controller_config,
    )
    service_after = service.read_bytes()
    controller_after = controller_config.read_bytes()
    second = upgrade_tunnel_controller_full_control(
        service_config_path=service,
        controller_config_path=controller_config,
    )
    assert second["capabilities_before"] == sorted(TARGET_CAPABILITIES)
    assert service.read_bytes() == service_after
    assert controller_config.read_bytes() == controller_after
    assert first["controller_principal"] == second["controller_principal"]


def test_full_control_qualification_unwraps_protocol_response():
    payload = {"ok": True, "result": {"content": "sentinel"}}
    assert _result(payload, "fs.read_text") == {"content": "sentinel"}
    with pytest.raises(RuntimeError, match="failed"):
        _result({"ok": False, "error": {"code": "DENIED"}}, "fs.read_text")
    with pytest.raises(RuntimeError, match="no result object"):
        _result({"ok": True}, "fs.read_text")
