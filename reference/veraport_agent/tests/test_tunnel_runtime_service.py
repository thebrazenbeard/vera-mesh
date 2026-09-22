from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

import veraport_agent.tunnel_runtime_service as trs


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config(tmp_path: Path) -> trs.TunnelRuntimeServiceConfig:
    tunnel = tmp_path / "tunnel-client.exe"
    mcp = tmp_path / "veraport-mcp-stdio.exe"
    key = tmp_path / "runtime.key"
    controller = tmp_path / "controller.json"
    profiles = tmp_path / "profiles"
    state = tmp_path / "state"
    tunnel.write_text("binary-placeholder", encoding="utf-8")
    mcp.write_text("mcp-placeholder", encoding="utf-8")
    key.write_text("sk-secret-that-must-not-leak\n", encoding="utf-8")
    controller.write_text("{}", encoding="utf-8")
    profiles.mkdir()
    state.mkdir()
    return trs.TunnelRuntimeServiceConfig.from_dict({
        "schema": "VERAMESH_TUNNEL_RUNTIME_SERVICE_V1",
        "tunnel_client": str(tunnel),
        "tunnel_client_sha256": digest(tunnel),
        "alias": "veramesh-lappy",
        "tunnel_id": "tunnel_0123456789abcdef0123456789abcdef",
        "runtime_api_key_file": str(key),
        "controller_config": str(controller),
        "mcp_executable": str(mcp),
        "mcp_executable_sha256": digest(mcp),
        "profile_dir": str(profiles),
        "state_dir": str(state),
        "status_interval_s": 5,
        "command_timeout_s": 30,
    })


def completed(args, *, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        args=args,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_connect_uses_file_secret_locator_not_secret_contents(tmp_path):
    cfg = config(tmp_path)
    args = trs.connect_args(cfg)
    rendered = "\n".join(args)
    assert "file:" + str(cfg.runtime_api_key_file) in args
    assert "sk-secret-that-must-not-leak" not in rendered
    assert "--mcp-command" in args
    command = args[args.index("--mcp-command") + 1]
    assert command == "'" + str(cfg.mcp_executable) + "'"


def test_runtime_environment_strips_ambient_api_keys(tmp_path):
    cfg = config(tmp_path)
    env = trs.runtime_environment(cfg, {
        "PATH": "safe",
        "CONTROL_PLANE_API_KEY": "must-go",
        "OPENAI_API_KEY": "must-also-go",
    })
    assert env["PATH"] == "safe"
    assert "CONTROL_PLANE_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert env["VERAPORT_CONTROLLER_CONFIG"] == str(cfg.controller_config)
    assert env["VERAPORT_MCP_TRANSPORT"] == "stdio"
    assert env["TUNNEL_CLIENT_PROFILE_DIR"] == str(cfg.profile_dir)
    assert env["TUNNEL_CLIENT_STATE_DIR"] == str(cfg.state_dir)


def test_status_requires_exact_running_health_booleans(tmp_path):
    cfg = config(tmp_path)

    def runner(args, **kwargs):
        return completed(args, stdout=json.dumps({
            "process_running": True,
            "healthy": True,
            "ready": False,
        }))

    status = trs.read_status(cfg, runner=runner)
    assert status.usable is True
    assert status.ready is False

    def bad_runner(args, **kwargs):
        return completed(args, stdout=json.dumps({
            "process_running": "true",
            "healthy": True,
            "ready": False,
        }))

    with pytest.raises(
        trs.TunnelRuntimeServiceError,
        match="process_running",
    ):
        trs.read_status(cfg, runner=bad_runner)


def test_run_until_stop_connects_verifies_and_stops(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    config_path = tmp_path / "tunnel-runtime.json"
    config_path.write_text(json.dumps({
        "schema": "VERAMESH_TUNNEL_RUNTIME_SERVICE_V1",
        "tunnel_client": str(cfg.tunnel_client),
        "tunnel_client_sha256": cfg.tunnel_client_sha256,
        "alias": cfg.alias,
        "tunnel_id": cfg.tunnel_id,
        "runtime_api_key_file": str(cfg.runtime_api_key_file),
        "controller_config": str(cfg.controller_config),
        "mcp_executable": str(cfg.mcp_executable),
        "mcp_executable_sha256": cfg.mcp_executable_sha256,
        "profile_dir": str(cfg.profile_dir),
        "state_dir": str(cfg.state_dir),
        "status_interval_s": cfg.status_interval_s,
        "command_timeout_s": cfg.command_timeout_s,
    }), encoding="utf-8")

    calls = []

    status_calls = 0

    def runner(args, **kwargs):
        nonlocal status_calls
        calls.append(list(args))
        if "status" in args:
            status_calls += 1
            if status_calls == 1:
                return completed(args, stderr="alias absent", returncode=1)
            return completed(args, stdout=json.dumps({
                "process_running": True,
                "healthy": True,
                "ready": False,
            }))
        return completed(args, stdout="{}")

    stop = threading.Event()
    stop.set()
    trs.run_until_stop(
        config_path,
        stop,
        runner=runner,
        validate_acl=False,
    )

    assert calls[0][1:3] == ["runtimes", "status"]
    assert calls[1][1:3] == ["runtimes", "connect"]
    assert calls[2][1:3] == ["runtimes", "status"]
    assert calls[-1][1:3] == ["runtimes", "stop"]


def test_command_failure_is_bounded_and_explicit(tmp_path):
    cfg = config(tmp_path)

    def runner(args, **kwargs):
        return completed(
            args,
            stderr="failure-" + ("x" * 20_000),
            returncode=7,
        )

    with pytest.raises(
        trs.TunnelRuntimeServiceError,
        match="rc=7",
    ) as caught:
        trs.connect_runtime(cfg, runner=runner)
    assert str(caught.value) == "tunnel-client command failed rc=7"
    assert "failure-" not in str(caught.value)


def test_non_windows_service_mode_fails_explicitly():
    if trs.win32serviceutil is not None:
        pytest.skip("pywin32 available in this environment")
    with pytest.raises(trs.TunnelRuntimeWindowsServiceUnavailable):
        trs.VeraMeshTunnelRuntimeService()
    with pytest.raises(trs.TunnelRuntimeWindowsServiceUnavailable):
        trs.service_cli()



@pytest.mark.skipif(os.name != "nt", reason="Windows ACL integration test")
def test_tunnel_service_materials_harden_and_validate_on_windows(tmp_path):
    controller_key = tmp_path / "controller-key.pem"
    tls_ca = tmp_path / "tls-ca.pem"
    workstation_public = tmp_path / "workstation-public.pem"
    for target in (controller_key, tls_ca, workstation_public):
        target.write_text("test-material", encoding="utf-8")

    controller_config = tmp_path / "controller-valid.json"
    controller_config.write_text(json.dumps({
        "schema": "VERAPORT_CONTROLLER_MCP_CONFIG_V1",
        "controller_key": str(controller_key),
        "tls_ca": str(tls_ca),
        "workstation_public_key": str(workstation_public),
        "requested_capabilities": ["fs.read"],
        "gateway_operations": ["lane.list"],
        "endpoints": [{
            "endpoint_id": "direct",
            "mode": "DIRECT_STREAM",
            "host": "127.0.0.1",
            "port": 17444,
            "server_hostname": "localhost",
            "durable_idempotency": True
        }]
    }), encoding="utf-8")

    tunnel = tmp_path / "tunnel-client.exe"
    mcp = tmp_path / "veraport-mcp-stdio.exe"
    tunnel.write_text("binary-placeholder", encoding="utf-8")
    mcp.write_text("mcp-placeholder", encoding="utf-8")
    runtime_key = tmp_path / "runtime.key"
    runtime_key.write_text("runtime-secret", encoding="utf-8")
    profiles = tmp_path / "profiles"
    state = tmp_path / "state"
    profiles.mkdir()
    state.mkdir()

    service_config_path = tmp_path / "tunnel-runtime.json"
    cfg = trs.TunnelRuntimeServiceConfig.from_dict({
        "schema": "VERAMESH_TUNNEL_RUNTIME_SERVICE_V1",
        "tunnel_client": str(tunnel),
        "tunnel_client_sha256": digest(tunnel),
        "alias": "veramesh-lappy",
        "tunnel_id": "tunnel_0123456789abcdef0123456789abcdef",
        "runtime_api_key_file": str(runtime_key),
        "controller_config": str(controller_config),
        "mcp_executable": str(mcp),
        "mcp_executable_sha256": digest(mcp),
        "profile_dir": str(profiles),
        "state_dir": str(state)
    })
    service_config_path.write_text("{}", encoding="utf-8")

    trs.harden_service_materials(service_config_path, cfg)
    trs.validate_service_materials(service_config_path, cfg)



@pytest.mark.skipif(
    trs.win32serviceutil is None,
    reason="pywin32 service class unavailable",
)
def test_tunnel_windows_service_depends_on_veraport_agent():
    assert trs.VeraMeshTunnelRuntimeService._svc_deps_ == [
        "VeraPortAgent"
    ]



def test_runtime_file_hash_mismatch_fails_closed(tmp_path):
    cfg = config(tmp_path)
    cfg.tunnel_client.write_text("tampered", encoding="utf-8")
    with pytest.raises(
        trs.TunnelRuntimeServiceError,
        match="tunnel-client executable SHA-256 mismatch",
    ):
        cfg.validate_runtime_files()


def test_mcp_command_uses_single_quotes_for_windows_backslashes(tmp_path):
    cfg = config(tmp_path)
    command = trs.connect_args(cfg)[-1]
    assert command.startswith("'") and command.endswith("'")
    assert str(cfg.mcp_executable) in command
