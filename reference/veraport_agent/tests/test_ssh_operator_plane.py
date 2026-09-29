import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WIN = ROOT / "tools" / "windows_configure_ssh_operator.ps1"
DSM = ROOT / "tools" / "synology_install_ssh_authorized_key.sh"

class SSHOperatorPlaneTests(unittest.TestCase):
    def test_windows_ssh_operator_is_keyed_narrow_and_validates_config(self):
        text = WIN.read_text(encoding="utf-8").lower()
        self.assertIn("openssh.server", text)
        self.assertIn("administrators_authorized_keys", text)
        self.assertIn("s-1-5-18", text)
        self.assertIn("s-1-5-32-544", text)
        self.assertIn("pubkeyauthentication yes", text)
        self.assertIn("allowusers", text)
        self.assertIn("& $sshd -t -f $configpath", text)
        self.assertIn("100.64.0.0/10", text)
        self.assertIn("get-nettcpconnection", text)
        self.assertIn("disablepasswordauthentication", text)
        self.assertNotIn("add-localgroupmember", text)
        self.assertNotIn("new-localuser", text)

    def test_dsm_script_preserves_synology_ssh_authority(self):
        text = DSM.read_text(encoding="utf-8").lower()
        self.assertIn("authorized_keys", text)
        self.assertIn("administrators", text)
        self.assertIn("chmod 700", text)
        self.assertIn("chmod 600", text)
        self.assertIn("chown -r", text)
        self.assertNotIn("synopkg", text)
        self.assertNotIn("synosystemctl start", text)
        self.assertNotIn("permitrootlogin", text)
        self.assertNotIn("passwordauthentication", text)
        self.assertNotIn("sshd_config", text)
        self.assertIn("--root-key", text)
        self.assertIn("/root/.ssh", text)
        self.assertIn("root:root", text)
        self.assertNotIn("sudoers", text)

    def test_no_private_key_material_is_embedded(self):
        joined = WIN.read_text(encoding="utf-8") + DSM.read_text(encoding="utf-8")
        self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", joined)
        self.assertNotIn("BEGIN RSA PRIVATE KEY", joined)

if __name__ == "__main__":
    unittest.main()
