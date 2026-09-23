from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import veraport_agent.tunnel_runtime_reseal as reseal


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_fixture(tmp_path: Path):
    root = tmp_path / "VeraMesh"
    runtime = root / "tunnel-runtime"
    scripts = runtime / "Scripts"
    scripts.mkdir(parents=True)

    tunnel_client = root / "tunnel-bin" / "tunnel-client.exe"
    tunnel_client.parent.mkdir()
    tunnel_client.write_bytes(b"tunnel-client")

    mcp = scripts / "veraport-mcp-stdio.exe"
    mcp.write_bytes(b"fresh-mcp")

    key = root / "secrets" / "runtime.key"
    key.parent.mkdir()
    key.write_text("secret", encoding="utf-8")

    controller_key = root / "controller-key.pem"
    tls_ca = root / "tls-ca.pem"
    workstation_public = root / "workstation-public.pem"
    for item in (controller_key, tls_ca, workstation_public):
        item.write_text("placeholder", encoding="utf-8")

    controller = root / "controller.json"
    controller.write_text(json.dumps({
        "schema": "VERAPORT_CONTROLLER_MCP_CONFIG_V1",
        "controller_key": str(controller_key),
        "tls_ca": str(tls_ca),
        "workstation_public_key": str(workstation_public),
        "requested_capabilities": ["fs.read", "fs.write"],
        "gateway_operations": ["lane.list"],
        "endpoints": [{
            "endpoint_id": "direct",
            "mode": "DIRECT_STREAM",
            "host": "127.0.0.1",
            "port": 17444,
            "server_hostname": "localhost",
            "durable_idempotency": True,
        }],
    }), encoding="utf-8")

    profiles = root / "profiles"
    state = root / "state"
    profiles.mkdir()
    state.mkdir()

    config = root / "tunnel-runtime.json"
    config.write_text(json.dumps({
        "schema": "VERAMESH_TUNNEL_RUNTIME_SERVICE_V1",
        "tunnel_client": str(tunnel_client),
        "tunnel_client_sha256": digest(tunnel_client),
        "alias": "veramesh-lappy",
        "tunnel_id": "tunnel_0123456789abcdef0123456789abcdef",
        "runtime_api_key_file": str(key),
        "controller_config": str(controller),
        "mcp_executable": str(mcp),
        "mcp_executable_sha256": "0" * 64,
        "profile_dir": str(profiles),
        "state_dir": str(state),
        "status_interval_s": 5.0,
        "command_timeout_s": 60.0,
    }), encoding="utf-8")

    return root, runtime, tunnel_client, mcp, config


def test_reseal_updates_only_mcp_digest_and_validates(tmp_path, monkeypatch):
    root, runtime, tunnel_client, mcp, config = make_fixture(tmp_path)
    calls = []

    monkeypatch.setattr(
        reseal,
        "harden_service_materials",
        lambda path, cfg: calls.append(("harden", str(path))),
    )
    monkeypatch.setattr(
        reseal,
        "validate_service_materials",
        lambda path, cfg: calls.append(("validate", str(path))),
    )

    result = reseal.reseal_tunnel_runtime(
        config_path=config,
        expected_runtime_root=runtime,
    )

    assert result["schema"] == "VERAMESH_TUNNEL_RUNTIME_RESEAL_V1"
    assert result["mcp_sha256_before"] == "0" * 64
    assert result["mcp_sha256_after"] == digest(mcp)
    assert result["tunnel_client_sha256_unchanged"] is True
    assert result["acl_resealed"] is True
    assert Path(result["backup"]).is_file()

    updated = json.loads(config.read_text(encoding="utf-8"))
    assert updated["mcp_executable_sha256"] == digest(mcp)
    assert updated["tunnel_client_sha256"] == digest(tunnel_client)
    assert calls == [("harden", str(config.resolve())), ("validate", str(config.resolve()))]


def test_reseal_refuses_tunnel_client_drift(tmp_path, monkeypatch):
    root, runtime, tunnel_client, mcp, config = make_fixture(tmp_path)
    tunnel_client.write_bytes(b"tampered")

    monkeypatch.setattr(reseal, "harden_service_materials", lambda *args: None)
    monkeypatch.setattr(reseal, "validate_service_materials", lambda *args: None)

    with pytest.raises(
        reseal.TunnelRuntimeResealError,
        match="tunnel-client hash also changed",
    ):
        reseal.reseal_tunnel_runtime(
            config_path=config,
            expected_runtime_root=runtime,
        )


def test_reseal_refuses_mcp_outside_isolated_runtime(tmp_path, monkeypatch):
    root, runtime, tunnel_client, mcp, config = make_fixture(tmp_path)
    outside = tmp_path / "outside.exe"
    outside.write_bytes(mcp.read_bytes())
    value = json.loads(config.read_text(encoding="utf-8"))
    value["mcp_executable"] = str(outside)
    config.write_text(json.dumps(value), encoding="utf-8")

    monkeypatch.setattr(reseal, "harden_service_materials", lambda *args: None)
    monkeypatch.setattr(reseal, "validate_service_materials", lambda *args: None)

    with pytest.raises(
        reseal.TunnelRuntimeResealError,
        match="escaped the expected isolated runtime root",
    ):
        reseal.reseal_tunnel_runtime(
            config_path=config,
            expected_runtime_root=runtime,
        )
