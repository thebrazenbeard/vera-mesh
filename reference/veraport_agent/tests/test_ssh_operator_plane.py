from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WIN = ROOT / "tools" / "windows_configure_ssh_operator.ps1"
DSM = ROOT / "tools" / "synology_install_ssh_authorized_key.sh"

def test_windows_ssh_operator_is_keyed_narrow_and_validates_config():
    text = WIN.read_text(encoding="utf-8").lower()
    assert "openssh.server" in text
    assert "administrators_authorized_keys" in text
    assert "s-1-5-18" in text and "s-1-5-32-544" in text
    assert "pubkeyauthentication yes" in text and "allowusers" in text
    assert "& $sshd -t -f $configpath" in text
    assert "100.64.0.0/10" in text and "get-nettcpconnection" in text
    assert "disablepasswordauthentication" in text
    assert "add-localgroupmember" not in text and "new-localuser" not in text

def test_dsm_script_preserves_synology_ssh_authority():
    text = DSM.read_text(encoding="utf-8").lower()
    assert "authorized_keys" in text and "administrators" in text
    assert "chmod 700" in text and "chmod 600" in text and "chown -r" in text
    assert "synopkg" not in text and "synosystemctl start" not in text
    assert "permitrootlogin" not in text and "passwordauthentication" not in text
    assert "sshd_config" not in text
    assert "--root-key" in text
    assert "/root/.ssh" in text
    assert "root:root" in text
    assert "sudoers" not in text

def test_no_private_key_material_is_embedded():
    joined = WIN.read_text(encoding="utf-8") + DSM.read_text(encoding="utf-8")
    assert "BEGIN OPENSSH PRIVATE KEY" not in joined
    assert "BEGIN RSA PRIVATE KEY" not in joined
