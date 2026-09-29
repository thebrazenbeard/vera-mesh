from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools" / "windows_grant_mediaphile_library_root.ps1"


def test_mediaphile_library_root_repair_is_fail_closed_and_narrow():
    text = SCRIPT.read_text(encoding="utf-8")
    lower = text.lower()

    assert 'targetmappedpath = "z:\\library"' in lower
    assert 'nt authority\\system' in lower
    assert 'registry::hkey_users' in lower
    assert '\\network\\$drive' in lower
    assert 'remotepath' in lower
    assert 'resolved target is not a unc path' in lower
    assert 'not accessible to localsystem' in lower
    assert '$config.allowed_roots = @($newroots)' in lower
    assert 'pre-mediaphile-library-' in lower
    assert 'backup restored' in lower
    assert 'restart_required' in lower


def test_mediaphile_library_root_repair_does_not_add_credentials_or_broaden_network_policy():
    text = SCRIPT.read_text(encoding="utf-8").lower()

    forbidden = [
        "cmdkey",
        "net use",
        "new-smbmapping",
        "set-smbclientconfiguration",
        "new-netfirewallrule",
        "set-netfirewallrule",
        "tailscale.exe",
        "tailscale set",
        "obj= localsystem",
        "sc.exe config",
        "set-service -credential",
        "password",
    ]
    for token in forbidden:
        assert token not in text

    assert 'allowed_roots = @("*")' not in text
    assert 'allowed_roots = @("c:\\")' not in text
    assert 'allowed_roots = @("d:\\")' not in text
