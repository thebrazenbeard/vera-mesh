from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "tools"
    / "windows_repair_lappy_service_and_tunnel_runtime.ps1"
)


def test_repair_packet_rebinds_services_and_reseals_without_identity_rotation():
    text = SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "VERAMESH_LAPPY_SERVICE_TUNNEL_REPAIR_PLAN_V1" in text
    assert "VERAMESH_LAPPY_SERVICE_TUNNEL_REPAIR_RECEIPT_V1" in text
    assert "12f80d79474ba0a3f4d78eff4d6a05f8c97f7454" in text
    assert "tunnel_runtime_reseal" in text
    assert '& $ServiceCli --startup auto update' in text
    assert '& $TunnelServiceCli --startup auto update' in text
    assert "Get-NetTCPConnection -State Listen -LocalPort 17444" in text
    assert "127.0.0.1" in text
    assert "--no-index" in text
    assert "--no-deps" in text
    assert "--force-reinstall" in text
    assert "pip","wheel" in text
    assert "rotates_identity = $false" in text
    assert "changes_allowed_roots = $false" in text
    assert "changes_process_policy = $false" in text
    assert "changes_tunnel_id = $false" in text
    assert "changes_runtime_api_key = $false" in text
    assert "process disabled" in text
    assert "fs.read,fs.write" in text
    assert "CREATE_OR_RESCAN_CHATGPT_TUNNEL_CONNECTOR" in text
    assert "sc.exe delete" not in text
    assert "Remove-Service" not in text


@pytest.mark.skipif(os.name != "nt", reason="PowerShell parser qualification")
def test_repair_packet_parses_in_windows_powershell():
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
