from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.render_lappy_desktop_commander_v3 import LappyPluginRenderError, render_plugin, validate_mcp_url


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin" / "lappy-desktop-commander-v3"


class LappyPluginRenderTests(unittest.TestCase):
    def test_source_is_direct_mcp_and_secret_free(self) -> None:
        manifest = json.loads((SOURCE / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual("1.2.0", manifest["version"])
        self.assertNotIn("apps", manifest["extensions"]["com.openai"])
        overlay = json.loads((SOURCE / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertNotIn("apps", overlay)
        self.assertEqual("./.mcp.json", overlay["mcpServers"])
        source_text = "\n".join(path.read_text(encoding="utf-8") for path in (
            SOURCE / "plugin.json",
            SOURCE / ".codex-plugin" / "plugin.json",
            SOURCE / "mcp.template.json",
            SOURCE / "skills" / "lappy-desktop-commander-v3" / "SKILL.md",
        ))
        self.assertIn("__LAPPY_VERAPORT_MCP_URL__", source_text)
        self.assertNotIn("trycloudflare.com", source_text)

    def test_renders_portable_and_legacy_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "plugin"
            url = "https://lappy.example.test/capability-token/mcp"
            render_plugin(SOURCE, output, mcp_url=url)
            portable = json.loads((output / "mcp.json").read_text(encoding="utf-8"))
            legacy = json.loads((output / ".mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(url, portable["mcpServers"]["lappy_veraport_direct"]["url"])
            self.assertEqual(url, legacy["mcpServers"]["lappy_veraport_direct"]["url"])
            self.assertFalse((output / "mcp.template.json").exists())
            manifest = json.loads((output / "plugin.json").read_text(encoding="utf-8"))
            self.assertNotIn("apps", manifest["extensions"]["com.openai"])

    def test_url_policy(self) -> None:
        self.assertEqual("https://mesh.example/token/mcp", validate_mcp_url("https://mesh.example/token/mcp"))
        for value in (
            "http://mesh.example/token/mcp",
            "https://mesh.example/",
            "https://mesh.example/token",
            "https://user@mesh.example/token/mcp",
            "https://mesh.example/token/mcp?x=1",
            "https://mesh.example:99999/token/mcp",
            "https://mesh.example:invalid/token/mcp",
            "https://mesh.example:0/token/mcp",
            "https://[::1/token/mcp",
            "https://[::1]]/token/mcp",
            "https://mesh.example/path\\n/mcp",
            "https://mesh.example\\\\attacker/token/mcp",
        ):
            with self.subTest(value=value):
                with self.assertRaises(LappyPluginRenderError):
                    validate_mcp_url(value)


if __name__ == "__main__":
    unittest.main()
