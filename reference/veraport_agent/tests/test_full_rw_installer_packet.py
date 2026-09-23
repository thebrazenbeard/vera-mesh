from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "tools"
    / "Install-Lappy-WorkBridge-VeraMesh-FullRW.ps1"
)


def test_full_rw_installer_uses_explicit_driver_not_pythonpath():
    text = SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "aa56672e0d0d79927a895add673bbb8b2bb78f8d" in text
    assert "invoke-controller-capability-upgrade.py" in text
    assert "sys.path.insert(0, str(source_root))" in text
    assert "from veraport_agent.controller_capability_upgrade import main" in text
    assert "$env:PYTHONPATH" not in text
    assert "fs.read,fs.write" in text
    assert "process_enabled = $false" in text
    assert "roots_broadened = $false" in text
    assert "veraport-doctor --live --tunnel-status" in text


@pytest.mark.skipif(os.name != "nt", reason="PowerShell parser qualification")
def test_full_rw_installer_parses_in_windows_powershell():
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
