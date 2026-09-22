from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from veraport_agent.controller_config import ControllerConfig
from veraport_agent.identity_store import load_identity
from veraport_agent.lappy_host import start_host
from veraport_agent.provision import (
    ProvisioningError,
    provision_bundle,
)
from veraport_agent.controller_runtime import ControllerRuntime
from veraport_agent.service_config import WindowsServiceConfig


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_provision_bundle_generates_cross_bound_identity_and_configs(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    bundle = provision_bundle(
        output_dir=tmp_path / "bundle",
        allowed_roots=[allowed],
        agent_port=free_port(),
        apply_windows_acl=False,
    )

    service = WindowsServiceConfig.load(bundle.service_config)
    controller = ControllerConfig.load(bundle.controller_config)
    identity = load_identity(
        workstation_key_path=service.workstation_key,
        controller_trust_path=service.controller_trust,
    )

    assert bundle.process_enabled is False
    assert service.allow_process_exec is False
    assert service.allowed_roots == (allowed.resolve(),)
    assert controller.requested_capabilities == frozenset(
        {"fs.read", "fs.write"}
    )
    assert "process.exec" not in controller.gateway_operations
    assert (
        bundle.controller_principal
        in identity.allowed_controllers
    )

    manifest = json.loads(
        (bundle.root / "bundle-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["workstation_principal"] == bundle.workstation_principal
    assert manifest["controller_principal"] == bundle.controller_principal


def test_provision_bundle_process_opt_in_and_verarelay_edge(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    bundle = provision_bundle(
        output_dir=tmp_path / "bundle",
        allowed_roots=[allowed],
        agent_port=free_port(),
        edge_host="edge.example.internal",
        edge_port=18445,
        allow_process_exec=True,
        apply_windows_acl=False,
    )
    controller = ControllerConfig.load(bundle.controller_config)
    service = WindowsServiceConfig.load(bundle.service_config)

    assert bundle.process_enabled is True
    assert bundle.edge_enabled is True
    assert service.allow_process_exec is True
    assert {
        "process.exec",
        "process.inspect",
        "process.interact",
        "process.control",
    } <= controller.requested_capabilities
    assert {
        "process.start",
        "process.status",
        "process.output",
        "process.input",
        "process.terminate",
    } <= controller.gateway_operations
    assert [item.mode.name for item in controller.endpoints] == [
        "DIRECT_STREAM",
        "EDGE_STREAM",
    ]
    assert controller.endpoints[1].host == "edge.example.internal"
    assert controller.endpoints[1].server_hostname == "localhost"


def test_provision_bundle_refuses_identity_overwrite(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    output = tmp_path / "bundle"
    first = provision_bundle(
        output_dir=output,
        allowed_roots=[allowed],
        agent_port=free_port(),
        apply_windows_acl=False,
    )
    key_before = (output / "identity" / "workstation-key.pem").read_bytes()

    with pytest.raises(ProvisioningError, match="refusing to overwrite"):
        provision_bundle(
            output_dir=output,
            allowed_roots=[allowed],
            agent_port=free_port(),
            apply_windows_acl=False,
        )

    assert (output / "identity" / "workstation-key.pem").read_bytes() == key_before
    assert first.root == output.resolve()


@pytest.mark.asyncio
async def test_generated_bundle_runs_authenticated_direct_round_trip(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    port = free_port()
    bundle = provision_bundle(
        output_dir=tmp_path / "bundle",
        allowed_roots=[allowed],
        agent_port=port,
        apply_windows_acl=False,
    )
    service = WindowsServiceConfig.load(bundle.service_config)
    controller_config = ControllerConfig.load(bundle.controller_config)

    prepared, server = await start_host(service)
    runtime = ControllerRuntime(controller_config)
    try:
        await runtime.ensure_started()
        info = await runtime.machine_info()
        assert info["workstation_principal"] == bundle.workstation_principal
        assert info["controller_principal"] == bundle.controller_principal
        assert info["selected_path_id"] == "direct-loopback"

        opened = await runtime.open_lane(
            lane_id="bootstrap-round-trip",
            task_id="bootstrap-test",
            capabilities=["fs.read", "fs.write"],
            claims=[
                {
                    "key": "fs:" + allowed.resolve().as_posix(),
                    "mode": "write",
                }
            ],
        )
        assert opened["ok"] is True
        fence = opened["result"]["fencing_token"]

        target = allowed / "bootstrap.txt"
        written = await runtime.write_text(
            lane_id="bootstrap-round-trip",
            fencing_token=fence,
            path=str(target),
            content="bootstrap-ok",
        )
        assert written["ok"] is True

        readback = await runtime.read_text(
            lane_id="bootstrap-round-trip",
            fencing_token=fence,
            path=str(target),
        )
        assert readback["ok"] is True
        assert readback["result"]["content"] == "bootstrap-ok"

        closed = await runtime.close_lane(
            lane_id="bootstrap-round-trip",
            fencing_token=fence,
        )
        assert closed["ok"] is True
    finally:
        await runtime.close()
        server.close()
        await server.wait_closed()
        prepared.state_store.close()
