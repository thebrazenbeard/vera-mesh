from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from .lappy_host import start_host
from .service_config import WindowsServiceConfig
from .windows_acl import validate_service_materials


DEFAULT_CONFIG_PATH = Path(r"C:\ProgramData\VeraMesh\veraport.json")

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


class WindowsServiceUnavailable(RuntimeError):
    code = "WINDOWS_SERVICE_UNAVAILABLE"


async def run_until_stop(config_path: str | Path, stop_event: threading.Event) -> None:
    config_path = Path(config_path)
    config = WindowsServiceConfig.load(config_path)
    validate_service_materials(config_path, config)
    prepared, server = await start_host(config)
    try:
        async with server:
            while not stop_event.is_set():
                await asyncio.sleep(0.25)
    finally:
        server.close()
        try:
            await server.wait_closed()
        finally:
            prepared.state_store.close()


def run_console(config_path: str | Path) -> None:
    stop = threading.Event()
    try:
        asyncio.run(run_until_stop(config_path, stop))
    except KeyboardInterrupt:
        stop.set()


if win32serviceutil is not None:
    class VeraPortWindowsService(win32serviceutil.ServiceFramework):
        _svc_name_ = "VeraPortAgent"
        _svc_display_name_ = "VeraMesh VeraPort Agent"
        _svc_description_ = "Persistent authenticated VeraMesh workstation bridge."

        def __init__(self, args):
            super().__init__(args)
            self._stop_event = threading.Event()
            self._service_stop = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._stop_event.set()
            win32event.SetEvent(self._service_stop)

        def SvcDoRun(self):
            servicemanager.LogInfoMsg(
                f"VeraPortAgent starting with fixed config {DEFAULT_CONFIG_PATH}"
            )
            try:
                asyncio.run(run_until_stop(DEFAULT_CONFIG_PATH, self._stop_event))
            except Exception as exc:
                servicemanager.LogErrorMsg(f"VeraPortAgent failed: {exc}")
                raise
            finally:
                servicemanager.LogInfoMsg("VeraPortAgent stopped")
else:
    class VeraPortWindowsService:
        def __init__(self, *args, **kwargs):
            raise WindowsServiceUnavailable("pywin32 is required for Windows Service mode")


def service_cli() -> None:
    if win32serviceutil is None:
        raise WindowsServiceUnavailable("pywin32 is required for Windows Service mode")
    win32serviceutil.HandleCommandLine(VeraPortWindowsService)
