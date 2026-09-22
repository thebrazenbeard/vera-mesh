from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

from veraport_agent.doctor import doctor, offline_doctor
from veraport_agent.lappy_host import start_host
from veraport_agent.provision import provision_bundle
from veraport_agent.service_config import WindowsServiceConfig


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def states(result):
    return {item["name"]: item["state"] for item in result["checks"]}


@pytest.mark.asyncio
async def test_doctor_reports_cross_bound_offline_bundle(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    bundle = provision_bundle(
        output_dir=tmp_path / "bundle",
        allowed_roots=[allowed],
        agent_port=free_port(),
        apply_windows_acl=False,
    )

    result = await doctor(
        service_config_path=bundle.service_config,
        controller_config_path=bundle.controller_config,
        check_windows_acl=False,
        live=False,
    )

    assert result["ok"] is True
    assert states(result) == {
        "service_config": "PASS",
        "controller_config": "PASS",
        "windows_acl": "SKIP",
        "application_identity": "PASS",
        "process_policy": "PASS",
        "live_paths": "SKIP",
    }


@pytest.mark.asyncio
async def test_doctor_probes_authenticated_live_path(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    bundle = provision_bundle(
        output_dir=tmp_path / "bundle",
        allowed_roots=[allowed],
        agent_port=free_port(),
        apply_windows_acl=False,
    )
    config = WindowsServiceConfig.load(bundle.service_config)
    prepared, server = await start_host(config)
    try:
        result = await doctor(
            service_config_path=bundle.service_config,
            controller_config_path=bundle.controller_config,
            check_windows_acl=False,
            live=True,
        )
        assert result["ok"] is True
        assert states(result)["live_paths"] == "PASS"
        assert (
            result["machine_info"]["selected_path_id"]
            == "direct-loopback"
        )
    finally:
        server.close()
        await server.wait_closed()
        prepared.state_store.close()


@pytest.mark.asyncio
async def test_doctor_detects_workstation_pin_mismatch(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    bundle = provision_bundle(
        output_dir=tmp_path / "bundle",
        allowed_roots=[allowed],
        agent_port=free_port(),
        apply_windows_acl=False,
    )

    controller = json.loads(
        bundle.controller_config.read_text(encoding="utf-8")
    )
    bad_key = ec.generate_private_key(ec.SECP256R1()).public_key()
    pin_path = Path(controller["workstation_public_key"])
    pin_path.write_bytes(
        bad_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    result = await doctor(
        service_config_path=bundle.service_config,
        controller_config_path=bundle.controller_config,
        check_windows_acl=False,
        live=False,
    )

    assert result["ok"] is False
    assert states(result)["application_identity"] == "FAIL"


def test_offline_doctor_detects_process_policy_mismatch(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    bundle = provision_bundle(
        output_dir=tmp_path / "bundle",
        allowed_roots=[allowed],
        agent_port=free_port(),
        allow_process_exec=True,
        apply_windows_acl=False,
    )

    service = json.loads(
        bundle.service_config.read_text(encoding="utf-8")
    )
    service["allow_process_exec"] = False
    bundle.service_config.write_text(
        json.dumps(service),
        encoding="utf-8",
    )

    checks, _, _ = offline_doctor(
        service_config_path=bundle.service_config,
        controller_config_path=bundle.controller_config,
        check_windows_acl=False,
    )
    assert {item.name: item.state for item in checks}[
        "process_policy"
    ] == "FAIL"
