from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "tools"
    / "windows_attach_existing_lappy_veramesh.ps1"
)


def test_existing_attach_packet_is_identity_preserving_and_read_only():
    text = SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "72d18d8267365f7f10a8068b232b43fd1c8f48a2" in text
    assert "v0.0.14" in text
    assert "784ab8da7b5a88f0109f1fd8aaf0a1c86067430b896dddf307ef7e3cc49fa1a5" in text
    assert "vera-controller-bootstrap.pem" in text
    assert "chatgpt-readonly-controller.pem" in text
    assert "veraport_agent.controller_recovery" in text
    assert "may_append_readonly_controller = $true" in text
    assert "retires_existing_controller = $false" in text
    assert 'Restart-Service -Name "VeraPortAgent" -Force' in text
    assert 'requested_capabilities = @("fs.read")' in text
    assert "replaces_veraport_service = $false" in text
    assert "rotates_workstation_identity = $false" in text
    assert "changes_veraport_roots = $false" in text
    assert "changes_veraport_process_policy = $false" in text
    assert "changes_tailscale = $false" in text
    assert "changes_firewall = $false" in text
    assert "--enable-process" not in text
    assert "veraport_agent.existing_install_attach" in text
    assert "VeraMeshTunnelRuntime already exists" in text


@pytest.mark.skipif(os.name != "nt", reason="PowerShell parser qualification")
def test_existing_attach_packet_parses_in_windows_powershell():
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
