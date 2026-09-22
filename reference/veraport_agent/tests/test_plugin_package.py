from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "render_veramesh_plugin",
    ROOT / "tools/render_veramesh_plugin.py",
)
render = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(render)


def test_plugin_source_has_portable_manifest_skill_and_behavior_evals():
    source = ROOT / "plugin/veramesh"
    manifest = json.loads(
        (source / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["$schema"] == (
        "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    )
    assert manifest["name"] == "veramesh"
    assert (source / "skills/remote-computer/SKILL.md").is_file()

    evals = json.loads(
        (source / "evals.json").read_text(encoding="utf-8")
    )
    assert len(evals["positive"]) >= 5
    assert len(evals["negative"]) >= 3


def test_plugin_renderer_requires_real_https_mcp_and_does_not_overwrite(tmp_path):
    source = ROOT / "plugin/veramesh"

    with pytest.raises(render.PluginRenderError, match="HTTPS"):
        render.render_plugin(
            source,
            tmp_path / "bad-http",
            mcp_url="http://mesh.example/mcp",
        )
    with pytest.raises(render.PluginRenderError, match="exactly /mcp"):
        render.render_plugin(
            source,
            tmp_path / "bad-path",
            mcp_url="https://mesh.example/other",
        )

    output = render.render_plugin(
        source,
        tmp_path / "rendered",
        mcp_url="https://mesh.example/mcp",
    )
    mcp = json.loads((output / "mcp.json").read_text(encoding="utf-8"))
    assert mcp == {
        "mcpServers": {
            "veramesh": {
                "type": "http",
                "url": "https://mesh.example/mcp",
            }
        }
    }
    assert not (output / "mcp.template.json").exists()
    assert not (output / "evals.json").exists()
    assert (output / "plugin.json").is_file()
    assert (output / "skills/remote-computer/SKILL.md").is_file()

    with pytest.raises(render.PluginRenderError, match="overwrite"):
        render.render_plugin(
            source,
            output,
            mcp_url="https://mesh.example/mcp",
        )
