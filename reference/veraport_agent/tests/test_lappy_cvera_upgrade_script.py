from pathlib import Path


def test_c_vera_workbridge_read_activation_script_is_narrow_and_pinned():
    root = Path(__file__).resolve().parents[3]
    script = (root / "tools" / "Enable-Lappy-CVera-WorkBridgeRead.ps1").read_text(encoding="utf-8")
    assert '6c38e45d59f4cbdcc827effe905d04574b92b6db' in script
    assert 'C:\\Vera' in script
    assert '"http://127.0.0.1:8765/mcp"' in script
    assert '"http://127.0.0.1:8765/mcp/healthz"' in script
    assert '"http://127.0.0.1:8765/health"' not in script
    assert 'mcp==1.27.2' in script
    assert 'workbridge_local_qualification' in script
    assert 'delegated_c_vera_access = "READ_ONLY"' in script
    assert 'WorkBridge write roots changed' in script
    assert 'VeraPort allowed_roots changed' in script
    assert 'VeraPort process execution became enabled' in script
    assert 'firewall_changed = $false' in script
    assert 'tailscale_changed = $false' in script


def test_c_vera_activation_runtime_upgrade_is_transactionally_staged():
    root = Path(__file__).resolve().parents[3]
    script = (root / "tools" / "Enable-Lappy-CVera-WorkBridgeRead.ps1").read_text(encoding="utf-8")

    required = [
        '$RuntimeDir = Join-Path $Root "runtime"',
        'runtime.pre-cvera-',
        'Move-Item -LiteralPath $RuntimeDir -Destination $runtimeBackup',
        'Copy-RuntimePreservingAcl -Source $runtimeBackup -Destination $RuntimeDir',
        'robocopy.exe $Source $Destination /MIR /COPYALL /DCOPY:DAT /XJ',
        'Restore-RuntimeDirectory -Backup $runtimeBackup -Destination $RuntimeDir',
        '$runtimeBackupReady = $true',
        '$runtimeCommitted = $true',
        '$runtimeRollbackError',
        'throw $activationError',
    ]
    for marker in required:
        assert marker in script

    stage = script.index('Move-Item -LiteralPath $RuntimeDir -Destination $runtimeBackup')
    first_pip = script.index('$RuntimePython -m pip install')
    assert stage < first_pip

    catch_start = script.index('catch {\n    $activationError = $_')
    restore = script.index('Restore-RuntimeDirectory -Backup $runtimeBackup -Destination $RuntimeDir', catch_start)
    restart = script.index('Start-Service -Name "VeraPortAgent"', catch_start)
    assert restore < restart
