from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools" / "Enable-Lappy-ChatGPT-RDC-Control.ps1"


def test_rdc_control_activation_packet_is_pinned_and_bounded():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "720a1f446b433825e59f1119405bdfc57bccec8e" in text
    assert "controller_full_control_upgrade" in text
    assert "full_control_qualification" in text
    assert "VeraPortAgent" in text
    assert "VeraMeshTunnelRuntime" in text
    assert "allow_process_exec" in text
    assert "allowed roots changed unexpectedly" in text
    assert "Restore-PreviousState" in text
    assert "Resolve-ServicePackage" in text
    assert "Resolve-TunnelPackage" in text
    assert "Replace-PackageTree" in text
    assert "Could not derive VeraPort service Python" not in text
    assert "SELECT_SECURE_MCP_TUNNEL_IN_CHATGPT_AND_RUN_TOOL_CALL" in text


def test_rdc_control_packet_does_not_expand_roots_or_mutate_network_policy():
    text = SCRIPT.read_text(encoding="utf-8").lower()
    assert "allowed_roots =" not in text
    assert "new-netfirewallrule" not in text
    assert "set-netfirewallrule" not in text
    assert "tailscale.exe" not in text
    assert "tailscale set" not in text
