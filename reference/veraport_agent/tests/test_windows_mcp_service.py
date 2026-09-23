from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import veraport_agent.windows_mcp_service as service


def test_mcp_service_uses_fixed_programdata_controller_config():
    assert service.DEFAULT_CONTROLLER_CONFIG_PATH == Path(
        r"C:\ProgramData\VeraMesh\controller.json"
    )
    assert service.DEFAULT_MCP_HOST == "127.0.0.1"
    assert service.DEFAULT_MCP_PORT == 17446


def test_non_windows_mcp_service_mode_fails_explicitly():
    if service.win32serviceutil is not None:
        pytest.skip("pywin32 available in this environment")
    with pytest.raises(service.WindowsMCPServiceUnavailable):
        service.VeraPortMCPWindowsService()


@pytest.mark.asyncio
async def test_mcp_service_runs_loopback_asgi_until_stop(tmp_path: Path):
    controller_key = tmp_path / "controller.pem"
    tls_ca = tmp_path / "ca.pem"
    workstation_public = tmp_path / "workstation.pem"
    for path in (controller_key, tls_ca, workstation_public):
        path.write_text("fixture", encoding="utf-8")

    config = SimpleNamespace(
        controller_key=controller_key,
        tls_ca=tls_ca,
        workstation_public_key=workstation_public,
    )
    observed = {}
    stopped = threading.Event()

    class Runtime:
        def __init__(self, value):
            observed["runtime_config"] = value

        async def close(self):
            observed["runtime_closed"] = True

    class MCP:
        def streamable_http_app(self):
            observed["app_built"] = True
            return object()

    def build(runtime, **kwargs):
        observed["build_kwargs"] = kwargs
        return MCP()

    class UvicornConfig:
        def __init__(self, app, **kwargs):
            observed["uvicorn_config"] = kwargs
            self.app = app

    class UvicornServer:
        def __init__(self, config):
            self.config = config
            self.should_exit = False

        async def serve(self):
            observed["server_started"] = True
            while not self.should_exit:
                await asyncio.sleep(0.01)
            observed["server_stopped"] = True

    deps = service.MCPServiceDependencies(
        config_loader=lambda path: config,
        runtime_cls=Runtime,
        mcp_builder=build,
        validate_materials=lambda path, cfg: None,
        uvicorn_config_cls=UvicornConfig,
        uvicorn_server_cls=UvicornServer,
    )

    task = asyncio.create_task(
        service.run_mcp_until_stop(
            tmp_path / "controller.json",
            stopped,
            host="127.0.0.1",
            port=20446,
            deps=deps,
        )
    )
    for _ in range(100):
        if observed.get("server_started"):
            break
        await asyncio.sleep(0.01)
    assert observed["server_started"] is True

    stopped.set()
    await asyncio.wait_for(task, timeout=2)

    assert observed["build_kwargs"] == {
        "host": "127.0.0.1",
        "port": 20446,
        "stateless_http": False,
    }
    assert observed["uvicorn_config"] == {
        "host": "127.0.0.1",
        "port": 20446,
        "log_level": "info",
        "access_log": False,
    }
    assert observed["app_built"] is True
    assert observed["server_stopped"] is True
    assert observed["runtime_closed"] is True


@pytest.mark.asyncio
async def test_mcp_service_rejects_non_loopback_before_loading_config(
    tmp_path: Path,
):
    calls = []
    deps = service.MCPServiceDependencies(
        config_loader=lambda path: calls.append(path),
        runtime_cls=lambda config: object(),
        mcp_builder=lambda runtime, **kwargs: object(),
        validate_materials=lambda path, cfg: None,
        uvicorn_config_cls=lambda *args, **kwargs: object(),
        uvicorn_server_cls=lambda config: object(),
    )
    with pytest.raises(ValueError, match="loopback"):
        await service.run_mcp_until_stop(
            tmp_path / "controller.json",
            threading.Event(),
            host="0.0.0.0",
            deps=deps,
        )
    assert calls == []
