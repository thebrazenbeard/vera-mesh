from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "tools"
    / "Enable-Lappy-DesktopCommander-Duplicate.ps1"
)


def test_desktop_commander_activation_polls_for_full_runtime_readiness():
    text = SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "function Wait-TunnelRuntimeReady" in text
    assert "process_running -eq $true" in text
    assert "healthy -eq $true" in text
    assert "ready -eq $true" in text
    assert "Start-Sleep -Milliseconds 500" in text
    assert "runtime_status = if ($null -ne $runtimeStatus)" in text
    assert "remote tunnel metadata lookup reported" in text
    assert "CALL_DESKTOP_COMMANDER_TOOL_FROM_CHATGPT_THROUGH_SELECTED_SECURE_MCP_TUNNEL" in text


@pytest.mark.skipif(os.name != "nt", reason="PowerShell parser qualification")
def test_desktop_commander_activation_parses_in_windows_powershell():
    path = str(SCRIPT).replace("'", "''")
    command = (
        "$e=$null;$t=$null;"
        f"[System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$t,[ref]$e)|Out-Null;"
        "if($e.Count){$e|ForEach-Object{Write-Error $_};exit 1}"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
