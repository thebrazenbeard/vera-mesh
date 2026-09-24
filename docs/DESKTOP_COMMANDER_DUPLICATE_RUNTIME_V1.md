# VeraMesh / Desktop Commander duplicate runtime

This source path makes the existing VeraMesh Secure MCP Tunnel launch the exact upstream DesktopCommanderMCP server built by WorkBridgeMCP, rather than translating Desktop Commander behavior into VeraPort operations.

Source binding:

- WorkBridge duplicate source pins `wonderwhy-er/DesktopCommanderMCP@550a0b3e31da18b7cf25e87ed840e3d953b6da42`.
- Desktop Commander package version at that subject is `0.2.51`.
- WorkBridge's installer builds that exact source and emits hashes for its private packaged `node.exe` and `dist/index.js`.
- VeraMesh tunnel runtime verifies both hashes before launch.

Runtime command shape:

```text
'<private node.exe>' '<DesktopCommanderMCP\\dist\\index.js>' '--no-onboarding'
```

The upstream Desktop Commander server therefore owns its native tools and semantics, including `start_process(command=...)` with unrestricted command strings, interactive process sessions, file/search/edit tools, process listing/control, local tool-call history, and its document convenience tools.

VeraMesh remains the authenticated remote transport. It does not rewrite the upstream Desktop Commander tool surface on this path.

The activation source is `tools/Enable-Lappy-DesktopCommander-Duplicate.ps1`. It is transactional: it verifies the exact WorkBridge manifest and runtime hashes, backs up the current tunnel configuration, swaps only the MCP executable/entrypoint/arguments, restarts the managed tunnel runtime, checks tunnel-client health, and restores the prior configuration if qualification fails.

VeraRelay remains part of the broader VeraMesh system, but the live MCP request/response stream on this duplicate path is the Secure MCP Tunnel. VeraRelay must not narrow or reinterpret Desktop Commander's tool semantics if it later carries discovery, custody, or fallback signaling for this path.

This repository change is source only. Running the activation packet is a separate workstation/runtime effect.
