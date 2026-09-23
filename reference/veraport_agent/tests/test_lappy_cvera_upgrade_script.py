from pathlib import Path


def test_c_vera_workbridge_read_activation_script_is_narrow_and_pinned():
    root = Path(__file__).resolve().parents[3]
    script = (root / "tools" / "Enable-Lappy-CVera-WorkBridgeRead.ps1").read_text(encoding="utf-8")
    assert '6c38e45d59f4cbdcc827effe905d04574b92b6db' in script
    assert 'C:\\Vera' in script
    assert '"http://127.0.0.1:8765/mcp"' in script
    assert 'mcp==1.27.2' in script
    assert 'workbridge_local_qualification' in script
    assert 'delegated_c_vera_access = "READ_ONLY"' in script
    assert 'WorkBridge write roots changed' in script
    assert 'VeraPort allowed_roots changed' in script
    assert 'VeraPort process execution became enabled' in script
    assert 'firewall_changed = $false' in script
    assert 'tailscale_changed = $false' in script
