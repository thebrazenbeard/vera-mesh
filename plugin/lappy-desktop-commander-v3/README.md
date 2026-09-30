# Lappy Desktop Commander V3 source

This directory is the source-controlled, secret-free form of the personal Lappy Desktop Commander V3 plugin.

The live plugin release is rendered from this tree plus one runtime-only secret: the deployed VeraMesh/VeraPort MCP HTTPS URL. That URL is a capability-bearing endpoint and must not be committed.

The V3 contract intentionally has no `.app.json` dependency. The portable direct MCP server is authoritative so a refreshed install receives the server's current `tools/list`, including process tools when the live controller/session/workstation policy grants them.

Render an installable directory with:

```text
python tools/render_lappy_desktop_commander_v3.py \
  --source plugin/lappy-desktop-commander-v3 \
  --output <private-output-directory> \
  --mcp-url <runtime-secret-https-url>
```

The renderer writes both `mcp.json` and `.mcp.json`, does not print the endpoint, and refuses to overwrite an existing output directory.

Source, rendered package, installed plugin release, live MCP session, and effective tool authority are separate evidence classes.
