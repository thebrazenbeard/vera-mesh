from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.render_veramesh_plugin import PluginRenderError, render_plugin, validate_mcp_url


ROOT = Path(__file__).resolve().parents[1]


class PluginRenderTests(unittest.TestCase):
    def test_renders_portable_streamable_http_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "veramesh"
            render_plugin(
                ROOT / "plugin" / "veramesh",
                output,
                mcp_url="https://mesh.example/mcp",
            )
            mcp = json.loads((output / "mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(
                mcp["$schema"],
                "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
            )
            server = mcp["mcpServers"]["veramesh"]
            self.assertEqual(server["type"], "streamable-http")
            self.assertEqual(server["url"], "https://mesh.example/mcp")
            self.assertTrue((output / "plugin.json").is_file())
            self.assertTrue((output / "skills" / "remote-computer" / "SKILL.md").is_file())
            self.assertFalse((output / "mcp.template.json").exists())
            self.assertFalse((output / "evals.json").exists())

    def test_url_policy_rejects_non_https_and_wrong_path(self) -> None:
        for value in (
            "http://mesh.example/mcp",
            "https://mesh.example/",
            "https://mesh.example/mcp/other",
            "https://user@mesh.example/mcp",
            "https://mesh.example/mcp?x=1",
        ):
            with self.subTest(value=value):
                with self.assertRaises(PluginRenderError):
                    validate_mcp_url(value)


if __name__ == "__main__":
    unittest.main()
