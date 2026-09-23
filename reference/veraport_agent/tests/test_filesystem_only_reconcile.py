from __future__ import annotations

import json
from pathlib import Path

from veraport_agent.controller_config import ControllerConfig
from veraport_agent.filesystem_only_reconcile import (
    TARGET_CAPABILITIES,
    TARGET_OPERATIONS,
    reconcile_filesystem_only,
)
from veraport_agent.identity_store import load_identity
from veraport_agent.provision import provision_local_pair
from veraport_agent.service_config import WindowsServiceConfig


PROCESS_CAPABILITIES = {
    "process.exec",
    "process.inspect",
    "process.interact",
    "process.control",
}

PROCESS_OPERATIONS = [
    "process.exec",
    "process.start",
    "process.list",
    "process.status",
    "process.output",
    "process.input",
    "process.terminate",
]


def _write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _fixture(tmp_path: Path):
    root = tmp_path / "VeraMesh"
    identity = root / "identity"
    identity.mkdir(parents=True)

    primary_manifest = provision_local_pair(
        identity,
        capabilities={"fs.read", "fs.write", *PROCESS_CAPABILITIES},
        harden_windows_acl=False,
    )

    second_identity = root / "second-identity"
    second_manifest = provision_local_pair(
        second_identity,
        capabilities={"fs.read", "fs.write"},
        harden_windows_acl=False,
    )

    trust_path = identity / "controller-trust.json"
    trust = json.loads(trust_path.read_text(encoding="utf-8"))
    second_trust = json.loads(
        (second_identity / "controller-trust.json").read_text(encoding="utf-8")
    )
    unrelated_entry = second_trust["controllers"][0]
    trust["controllers"].append(unrelated_entry)
    _write_json(trust_path, trust)

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    state = root / "state"
    state.mkdir()

    service_path = root / "veraport.json"
    _write_json(
        service_path,
        {
            "bind_host": "127.0.0.1",
            "bind_port": 17444,
            "allowed_roots": [str(allowed)],
            "state_db": str(state / "agent.sqlite3"),
            "tls_cert": str(identity / "tls-cert.pem"),
            "tls_key": str(identity / "tls-key.pem"),
            "workstation_key": str(identity / "workstation-key.pem"),
            "controller_trust": str(trust_path),
            "allow_process_exec": True,
            "allow_non_loopback_listener": False,
        },
    )

    controller_path = root / "controller.json"
    _write_json(
        controller_path,
        {
            "schema": "VERAPORT_CONTROLLER_MCP_CONFIG_V1",
            "controller_key": str(identity / "controller-key.pem"),
            "tls_ca": str(identity / "tls-ca.pem"),
            "workstation_public_key": str(identity / "workstation-public.pem"),
            "requested_capabilities": sorted(
                {"fs.read", "fs.write", *PROCESS_CAPABILITIES}
            ),
            "gateway_operations": list(TARGET_OPERATIONS) + PROCESS_OPERATIONS,
            "endpoints": [
                {
                    "endpoint_id": "lappy-existing-loopback",
                    "mode": "DIRECT_STREAM",
                    "host": "127.0.0.1",
                    "port": 17444,
                    "server_hostname": "localhost",
                    "durable_idempotency": True,
                }
            ],
            "max_path_age_ms": 5000,
            "connect_timeout_s": 5.0,
            "request_timeout_s": 5.0,
        },
    )

    return {
        "root": root,
        "identity": identity,
        "allowed": allowed,
        "service": service_path,
        "controller": controller_path,
        "trust": trust_path,
        "primary_principal": primary_manifest["controller_principal"],
        "unrelated_principal": second_manifest["controller_principal"],
        "unrelated_entry": unrelated_entry,
    }


def test_reconcile_process_enabled_state_to_exact_filesystem_only(tmp_path):
    fx = _fixture(tmp_path)
    service_before = json.loads(fx["service"].read_text(encoding="utf-8"))
    trust_before = json.loads(fx["trust"].read_text(encoding="utf-8"))

    result = reconcile_filesystem_only(
        service_config_path=fx["service"],
        controller_config_path=fx["controller"],
    )

    assert result["process_enabled_before"] is True
    assert result["process_enabled_after"] is False
    assert result["controller_capabilities_after"] == ["fs.read", "fs.write"]
    assert result["allowed_roots_unchanged"] is True
    assert result["identity_bindings_unchanged"] is True
    assert result["bind_unchanged"] is True
    assert Path(result["backups"]["service"]).is_file()
    assert Path(result["backups"]["trust"]).is_file()
    assert Path(result["backups"]["controller"]).is_file()

    service = WindowsServiceConfig.load(fx["service"])
    assert service.allow_process_exec is False
    assert service.allowed_roots == (fx["allowed"].resolve(),)
    assert service.bind_host == service_before["bind_host"]
    assert service.bind_port == service_before["bind_port"]

    controller = ControllerConfig.load(fx["controller"])
    assert controller.requested_capabilities == TARGET_CAPABILITIES
    assert controller.gateway_operations == frozenset(TARGET_OPERATIONS)
    assert not any(op.startswith("process.") for op in controller.gateway_operations)

    identity = load_identity(
        workstation_key_path=service.workstation_key,
        controller_trust_path=service.controller_trust,
    )
    assert identity.capability_policy[fx["primary_principal"]] == TARGET_CAPABILITIES
    assert identity.capability_policy[fx["unrelated_principal"]] == frozenset(
        fx["unrelated_entry"]["capabilities"]
    )

    trust_after = json.loads(fx["trust"].read_text(encoding="utf-8"))
    before_other = next(
        entry for entry in trust_before["controllers"]
        if entry["principal"] == fx["unrelated_principal"]
    )
    after_other = next(
        entry for entry in trust_after["controllers"]
        if entry["principal"] == fx["unrelated_principal"]
    )
    assert after_other == before_other


def test_reconcile_is_idempotent_on_exact_filesystem_only_state(tmp_path):
    fx = _fixture(tmp_path)
    first = reconcile_filesystem_only(
        service_config_path=fx["service"],
        controller_config_path=fx["controller"],
    )
    second = reconcile_filesystem_only(
        service_config_path=fx["service"],
        controller_config_path=fx["controller"],
    )

    assert first["process_enabled_after"] is False
    assert second["process_enabled_before"] is False
    assert second["process_enabled_after"] is False
    assert second["service_changed"] is False
    assert second["trust_changed"] is False
    assert second["controller_changed"] is False
