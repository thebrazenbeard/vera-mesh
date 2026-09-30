from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit


class LappyPluginRenderError(RuntimeError):
    pass


def validate_mcp_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise LappyPluginRenderError("MCP URL is required")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise LappyPluginRenderError("MCP URL must be absolute HTTPS")
    if not parsed.path or not parsed.path.endswith("/mcp"):
        raise LappyPluginRenderError("MCP URL path must end with /mcp")
    if parsed.path.endswith("//mcp"):
        raise LappyPluginRenderError("MCP URL path is malformed")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise LappyPluginRenderError("MCP URL contains unsupported URL components")
    return value.rstrip("/")


def render_plugin(source_dir: str | Path, output_dir: str | Path, *, mcp_url: str) -> Path:
    source = Path(source_dir).resolve()
    output = Path(output_dir).resolve()
    mcp_url = validate_mcp_url(mcp_url)
    if output.exists():
        raise LappyPluginRenderError(f"refusing to overwrite existing plugin output: {output}")
    for required in (
        source / "plugin.json",
        source / ".codex-plugin" / "plugin.json",
        source / "skills" / "lappy-desktop-commander-v3" / "SKILL.md",
    ):
        if not required.is_file():
            raise LappyPluginRenderError(f"required source missing: {required}")

    shutil.copytree(source, output, ignore=shutil.ignore_patterns(
        "mcp.template.json", "__pycache__"
    ))
    portable = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {"lappy_veraport_direct": {"type": "streamable-http", "url": mcp_url}},
    }
    legacy = {
        "mcpServers": {"lappy_veraport_direct": {"type": "streamable-http", "url": mcp_url, "headers": {}}},
    }
    (output / "mcp.json").write_text(json.dumps(portable, indent=2) + "\n", encoding="utf-8")
    (output / ".mcp.json").write_text(json.dumps(legacy, indent=2) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the secret-bearing Lappy Desktop Commander V3 plugin.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mcp-url", required=True)
    args = parser.parse_args()
    output = render_plugin(args.source, args.output, mcp_url=args.mcp_url)
    print(output)


if __name__ == "__main__":
    main()
