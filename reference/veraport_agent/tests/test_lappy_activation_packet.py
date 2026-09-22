from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "tools"
    / "windows_activate_lappy_veramesh.ps1"
)


def test_activation_packet_pins_reviewed_subject_and_read_only_authority():
    canonical_lf = SCRIPT.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(canonical_lf).hexdigest() == (
        "fd90707c65a575b9a53906a4b911f365c98e5c9f0463cc02c7f12ef3073d5bdc"
    )
    text = canonical_lf.decode("utf-8")
    assert "83e178921772017bc4edbe26b5a2e4eee8da1632" in text
    assert "v0.0.11" in text
    assert "eb912c86c6ccde90cda805cb17009507176a656725cf86c36fabe1901a12e29b" in text
    assert "VeraPortAgent" in text
    assert "VeraMeshTunnelRuntime" in text
    assert "veraport-doctor" in text
    assert "process_execution = $false" in text
    assert "--enable-process" not in text
    assert "AllowNonLoopback" not in text
    assert "runtime_key_value_recorded = $false" in text


@pytest.mark.skipif(os.name != "nt", reason="PowerShell parser qualification")
def test_activation_packet_parses_in_windows_powershell():
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
