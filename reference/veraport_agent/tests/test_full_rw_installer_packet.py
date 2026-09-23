from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "tools"
    / "Install-Lappy-WorkBridge-VeraMesh-FullRW.ps1"
)


def test_full_rw_installer_uses_explicit_driver_not_pythonpath():
    text = SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "1e23849eeff9903c8419d5dabf3cebce96962e51" in text
    assert "invoke-filesystem-only-reconcile.py" in text
    assert "sys.path.insert(0, str(source_root))" in text
    assert "from veraport_agent.filesystem_only_reconcile import main" in text
    assert "$env:PYTHONPATH" not in text
    assert "fs.read,fs.write" in text
    assert "process_enabled = $false" in text
    assert "roots_broadened = $false" in text
    assert "veraport-doctor --live --tunnel-status" in text


def test_embedded_upgrade_driver_imports_explicit_source_under_isolation(tmp_path):
    text = SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = re.search(
        r"\$upgradeDriverSource = @'\n(?P<driver>.*?)\n'@",
        text,
        flags=re.DOTALL,
    )
    assert match is not None

    driver = tmp_path / "invoke-filesystem-only-reconcile.py"
    driver.write_text(match.group("driver") + "\n", encoding="utf-8")

    source_root = tmp_path / "source"
    package = source_root / "veraport_agent"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "controller_capability_upgrade.py").write_text(
        "def main():\n"
        "    import json\n"
        "    print(json.dumps({'driver_import': 'PASS'}))\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path / "must-not-be-used")
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(driver),
            str(source_root),
            "--service-config",
            "ignored",
            "--controller-config",
            "ignored",
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert '"driver_import": "PASS"' in completed.stdout


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
