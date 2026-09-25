from pathlib import Path
import pytest

import veraport_agent.windows_service as ws


def test_windows_service_uses_fixed_programdata_config():
    assert ws.DEFAULT_CONFIG_PATH == Path(r"C:\ProgramData\VeraMesh\veraport.json")


def test_non_windows_service_mode_fails_explicitly():
    if ws.win32serviceutil is not None:
        pytest.skip("pywin32 available in this environment")
    with pytest.raises(ws.WindowsServiceUnavailable):
        ws.VeraPortWindowsService()


def test_service_cli_fails_explicitly_without_pywin32():
    if ws.win32serviceutil is not None:
        pytest.skip("pywin32 available in this environment")
    with pytest.raises(ws.WindowsServiceUnavailable):
        ws.service_cli()
