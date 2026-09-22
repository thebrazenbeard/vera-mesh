from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .controller_config import ControllerConfig
from .controller_runtime import ControllerRuntime
from .mcp_server import build_mcp_server, require_loopback_mcp_host
from .windows_acl import validate_controller_materials


DEFAULT_CONTROLLER_CONFIG_PATH = Path(
    r"C:\ProgramData\VeraMesh\controller.json"
)
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 17446

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
except ImportError:
    servicemanager = None
    win32event = None
    win32service = None
    win32serviceutil = None


class WindowsMCPServiceUnavailable(RuntimeError):
    code = "WINDOWS_MCP_SERVICE_UNAVAILABLE"


@dataclass(frozen=True)
class MCPServiceDependencies:
    config_loader: Callable[[str | Path], ControllerConfig]
    runtime_cls: Callable[[ControllerConfig], Any]
    mcp_builder: Callable[[Any], Any]
    validate_materials: Callable[[str | Path, ControllerConfig], None]
    uvicorn_config_cls: Callable[..., Any]
    uvicorn_server_cls: Callable[..., Any]


def default_dependencies() -> MCPServiceDependencies:
    try:
        import uvicorn
    except ImportError as exc:
        raise WindowsMCPServiceUnavailable(
            "uvicorn is required for VeraPort MCP service mode; "
            "install the project with the mcp extra"
        ) from exc

    return MCPServiceDependencies(
        config_loader=ControllerConfig.load,
        runtime_cls=ControllerRuntime,
        mcp_builder=build_mcp_server,
        validate_materials=validate_controller_materials,
        uvicorn_config_cls=uvicorn.Config,
        uvicorn_server_cls=uvicorn.Server,
    )


def _validate_controller_files(config: ControllerConfig) -> None:
    required = (
        config.controller_key,
        config.tls_ca,
        config.workstation_public_key,
    )
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise WindowsMCPServiceUnavailable(
            "required controller material missing: "
            + ", ".join(str(path) for path in missing)
        )


async def run_mcp_until_stop(
    config_path: str | Path,
    stop_event: threading.Event,
    *,
    host: str = DEFAULT_MCP_HOST,
    port: int = DEFAULT_MCP_PORT,
    deps: MCPServiceDependencies | None = None,
) -> None:
    host = require_loopback_mcp_host(host)
    if type(port) is not int or not 1 <= port <= 65_535:
        raise ValueError("MCP port must be in 1..65535")
    deps = deps or default_dependencies()

    config_path = Path(config_path)
    config = deps.config_loader(config_path)
    _validate_controller_files(config)
    if os.name == "nt":
        deps.validate_materials(config_path, config)

    runtime = deps.runtime_cls(config)
    server = None
    stop_task: asyncio.Task[Any] | None = None
    server_task: asyncio.Task[Any] | None = None
    try:
        mcp = deps.mcp_builder(runtime)
        app = mcp.streamable_http_app(stateless_http=False)
        uvicorn_config = deps.uvicorn_config_cls(
            app,
            host=host,
            port=port,
            log_level="info",
            access_log=False,
        )
        server = deps.uvicorn_server_cls(uvicorn_config)

        async def watch_stop() -> None:
            while not stop_event.is_set():
                await asyncio.sleep(0.25)
            server.should_exit = True

        server_task = asyncio.create_task(server.serve())
        stop_task = asyncio.create_task(watch_stop())
        done, _ = await asyncio.wait(
            {server_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if server_task in done:
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
            await server_task
        else:
            await server_task
    finally:
        if stop_task is not None and not stop_task.done():
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
        if server_task is not None and not server_task.done():
            if server is not None:
                server.should_exit = True
            await asyncio.gather(server_task, return_exceptions=True)
        close = getattr(runtime, "close", None)
        if callable(close):
            result = close()
            if asyncio.iscoroutine(result):
                await result


def _run_service_loop(
    config_path: str | Path,
    stop_event: threading.Event,
) -> None:
    if os.name != "nt":
        asyncio.run(run_mcp_until_stop(config_path, stop_event))
        return

    loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            run_mcp_until_stop(config_path, stop_event)
        )
        loop.run_until_complete(loop.shutdown_asyncgens())
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def run_console(config_path: str | Path) -> None:
    stop = threading.Event()
    try:
        asyncio.run(run_mcp_until_stop(config_path, stop))
    except KeyboardInterrupt:
        stop.set()


if win32serviceutil is not None:
    class VeraPortMCPWindowsService(win32serviceutil.ServiceFramework):
        _svc_name_ = "VeraPortMCP"
        _svc_display_name_ = "VeraMesh VeraPort MCP"
        _svc_description_ = (
            "Persistent loopback MCP controller for VeraMesh VeraPort."
        )

        def __init__(self, args):
            super().__init__(args)
            self._stop_event = threading.Event()
            self._service_stop = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self):
            self.ReportServiceStatus(
                win32service.SERVICE_STOP_PENDING
            )
            self._stop_event.set()
            win32event.SetEvent(self._service_stop)

        def SvcDoRun(self):
            servicemanager.LogInfoMsg(
                "VeraPortMCP starting with fixed config "
                f"{DEFAULT_CONTROLLER_CONFIG_PATH}"
            )
            try:
                _run_service_loop(
                    DEFAULT_CONTROLLER_CONFIG_PATH,
                    self._stop_event,
                )
            except Exception as exc:
                servicemanager.LogErrorMsg(
                    f"VeraPortMCP failed: {exc}"
                )
                raise
            finally:
                servicemanager.LogInfoMsg("VeraPortMCP stopped")
else:
    class VeraPortMCPWindowsService:
        def __init__(self, *args, **kwargs):
            raise WindowsMCPServiceUnavailable(
                "pywin32 is required for Windows Service mode"
            )


def service_cli() -> None:
    if win32serviceutil is None:
        raise WindowsMCPServiceUnavailable(
            "pywin32 is required for Windows Service mode"
        )
    win32serviceutil.HandleCommandLine(VeraPortMCPWindowsService)
