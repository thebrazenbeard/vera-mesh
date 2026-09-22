from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from veraport_agent.bootstrap import prepare_local_bootstrap
from veraport_agent.doctor import doctor, offline_doctor
from veraport_agent.lappy_host import start_host
from veraport_agent.service_config import WindowsServiceConfig


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_bundle(tmp_path: Path, *, enable_process: bool = False):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    tunnel = tmp_path / "tunnel-client.exe"
    mcp = tmp_path / "veraport-mcp-stdio.exe"
    key = tmp_path / "runtime.key"
    tunnel.write_bytes(b"tunnel-client-fixture")
    mcp.write_bytes(b"mcp-fixture")
    key.write_text("runtime-secret", encoding="utf-8")
    root = tmp_path / "VeraMesh"
    prepare_local_bootstrap(
        root,
        allowed_roots=[allowed],
        tunnel_client=tunnel,
        tunnel_id="tunnel_fixture",
        runtime_api_key_file=key,
        mcp_executable=mcp,
        enable_process=enable_process,
        bind_port=free_port(),
        harden_windows_acl=False,
    )
    return root


def states(result):
    return {item["name"]: item["state"] for item in result["checks"]}


@pytest.mark.asyncio
async def test_doctor_accepts_current_bootstrap_offline(tmp_path: Path):
    root = make_bundle(tmp_path)
    result = await doctor(
        service_config_path=root / "veraport.json",
        controller_config_path=root / "controller.json",
        tunnel_config_path=root / "tunnel-runtime.json",
        check_windows_acl=False,
        live=False,
        tunnel_status=False,
    )

    assert result["ok"] is True
    assert states(result) == {
        "service_config": "PASS",
        "controller_config": "PASS",
        "tunnel_config": "PASS",
        "cross_binding": "PASS",
        "process_policy": "PASS",
        "windows_acl": "SKIP",
        "live_veraport": "SKIP",
        "tunnel_runtime": "SKIP",
    }


@pytest.mark.asyncio
async def test_doctor_probes_authenticated_current_veraport_path(
    tmp_path: Path,
):
    root = make_bundle(tmp_path)
    config = WindowsServiceConfig.load(root / "veraport.json")
    prepared, server = await start_host(config)
    try:
        result = await doctor(
            service_config_path=root / "veraport.json",
            controller_config_path=root / "controller.json",
            tunnel_config_path=root / "tunnel-runtime.json",
            check_windows_acl=False,
            live=True,
            tunnel_status=False,
        )
        assert result["ok"] is True
        assert states(result)["live_veraport"] == "PASS"
        assert (
            result["machine_info"]["selected_path_id"]
            == "lappy-direct"
        )
    finally:
        server.close()
        await server.wait_closed()
        prepared.state_store.close()


def test_offline_doctor_detects_workstation_pin_mismatch(tmp_path: Path):
    root = make_bundle(tmp_path)
    bad = ec.generate_private_key(ec.SECP256R1()).public_key()
    pin = root / "identity" / "workstation-public.pem"
    pin.write_bytes(
        bad.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    checks, _, _, _ = offline_doctor(
        service_config_path=root / "veraport.json",
        controller_config_path=root / "controller.json",
        tunnel_config_path=root / "tunnel-runtime.json",
        check_windows_acl=False,
    )

    assert {item.name: item.state for item in checks}[
        "cross_binding"
    ] == "FAIL"


def test_offline_doctor_detects_process_policy_mismatch(tmp_path: Path):
    root = make_bundle(tmp_path, enable_process=True)
    service_path = root / "veraport.json"
    service = json.loads(service_path.read_text(encoding="utf-8"))
    service["allow_process_exec"] = False
    service_path.write_text(
        json.dumps(service),
        encoding="utf-8",
    )

    checks, _, _, _ = offline_doctor(
        service_config_path=service_path,
        controller_config_path=root / "controller.json",
        tunnel_config_path=root / "tunnel-runtime.json",
        check_windows_acl=False,
    )

    assert {item.name: item.state for item in checks}[
        "process_policy"
    ] == "FAIL"


def test_offline_doctor_detects_tunnel_controller_lineage_mismatch(
    tmp_path: Path,
):
    root = make_bundle(tmp_path)
    other = root / "other-controller.json"
    other.write_bytes((root / "controller.json").read_bytes())
    tunnel_path = root / "tunnel-runtime.json"
    tunnel = json.loads(tunnel_path.read_text(encoding="utf-8"))
    tunnel["controller_config"] = str(other)
    tunnel_path.write_text(json.dumps(tunnel), encoding="utf-8")

    checks, _, _, _ = offline_doctor(
        service_config_path=root / "veraport.json",
        controller_config_path=root / "controller.json",
        tunnel_config_path=tunnel_path,
        check_windows_acl=False,
    )

    assert {item.name: item.state for item in checks}[
        "cross_binding"
    ] == "FAIL"
