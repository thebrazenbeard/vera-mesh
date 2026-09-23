from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "tools"
    / "windows_resume_existing_lappy_tunnel.ps1"
)


def test_resume_packet_is_module_only_and_preservation_bounded():
    text = SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "56a6334565352aee46cd20789b8a40282f7da8a9" in text
    assert "f7210d2703552b280a73a2df4137553f1971d2f6" in text
    assert "VERAMESH_EXISTING_LAPPY_TUNNEL_RESUME_PLAN_V1" in text
    assert "VERAMESH_EXISTING_LAPPY_TUNNEL_RESUME_RECEIPT_V1" in text
    assert 'requested_capabilities' in text
    assert '"fs.read"' in text
    assert "Stop-Service -Name \"VeraMeshTunnelRuntime\"" in text
    assert "Start-Service -Name \"VeraMeshTunnelRuntime\"" in text
    assert "veraport-doctor.exe" in text
    assert '$RuntimePython -I -c "import veraport_agent.tunnel_runtime_service as m; print(m.__file__)"' in text
    assert "Resolve-Path -LiteralPath $RuntimeDir" in text
    assert "Resolve-Path -LiteralPath $InstalledModule" in text
    assert "VeraPortAgent restarted during tunnel resume" in text
    assert "runtime_key_unchanged = $true" in text
    assert "controller_trust_changed = $false" in text
    assert "credentials_changed = $false" in text
    assert "firewall_changed = $false" in text
    assert "tailscale_changed = $false" in text
    assert "chatgpt_connector_registered = $false" in text
    assert "Read-Host" not in text
    assert "controller_recovery" not in text
    assert "existing_install_attach" not in text
    assert 'Restart-Service -Name "VeraPortAgent"' not in text


@pytest.mark.skipif(os.name != "nt", reason="PowerShell parser qualification")
def test_resume_packet_parses_in_windows_powershell():
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
