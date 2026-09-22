from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit


class PluginRenderError(RuntimeError):
    pass


def validate_mcp_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise PluginRenderError("MCP URL is required")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise PluginRenderError("MCP URL must be absolute HTTPS")
    if parsed.path.rstrip("/") != "/mcp":
        raise PluginRenderError("MCP URL path must be exactly /mcp")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise PluginRenderError("MCP URL contains unsupported URL components")
    return value.rstrip("/")


def render_plugin(
    source_dir: str | Path,
    output_dir: str | Path,
    *,
    mcp_url: str,
) -> Path:
    source = Path(source_dir).resolve()
    output = Path(output_dir).resolve()
    mcp_url = validate_mcp_url(mcp_url)
    if output.exists():
        raise PluginRenderError(
            f"refusing to overwrite existing plugin output: {output}"
        )
    if not (source / "plugin.json").is_file():
        raise PluginRenderError("plugin.json missing from source")
    if not (source / "skills").is_dir():
        raise PluginRenderError("skills directory missing from source")

    shutil.copytree(
        source,
        output,
        ignore=shutil.ignore_patterns(
            "mcp.template.json",
            "evals.json",
            "__pycache__",
        ),
    )
    mcp = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "veramesh": {
                "type": "streamable-http",
                "url": mcp_url,
            }
        },
    }
    (output / "mcp.json").write_text(
        json.dumps(mcp, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render an installable VeraMesh plugin package."
    )
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mcp-url", required=True)
    args = parser.parse_args()
    output = render_plugin(
        args.source,
        args.output,
        mcp_url=args.mcp_url,
    )
    print(output)


if __name__ == "__main__":
    main()
