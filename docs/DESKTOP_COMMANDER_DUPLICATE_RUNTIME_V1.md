# VeraMesh / VeraRelay / Desktop Commander duplicate runtime

This source path runs the exact upstream DesktopCommanderMCP server built by WorkBridgeMCP. It does not translate Desktop Commander behavior into VeraPort operations.

Source binding:

- WorkBridge duplicate source pins `wonderwhy-er/DesktopCommanderMCP@550a0b3e31da18b7cf25e87ed840e3d953b6da42`.
- Desktop Commander package version at that subject is `0.2.51`.
- WorkBridge's installer builds that exact source and emits hashes for its private packaged `node.exe` and `dist/index.js`.
- VeraMesh tunnel runtime verifies both hashes before launch.

## Primary Secure MCP Tunnel path

```text
ChatGPT -> VeraMesh Secure MCP Tunnel -> private node.exe -> exact DesktopCommanderMCP dist/index.js -> workstation
```

The managed tunnel command is:

```text
'<private node.exe>' '<DesktopCommanderMCP\\dist\\index.js>' '--no-onboarding'
```

The upstream Desktop Commander server owns its native tools and semantics, including unrestricted `start_process(command=...)`, interactive process sessions, filesystem/search/edit tools, process listing/control, recent tool-call history, document convenience tools, and configuration tools present at the pinned source subject.

## VeraRelay live path

```text
MCP stream -> verarelay-desktop-commander -> veramesh-desktop-commander-host -> exact DesktopCommanderMCP stdio server
```

`veramesh-desktop-commander-host` turns the exact upstream stdio process into an opaque loopback byte stream without parsing MCP. `verarelay-desktop-commander` forwards that stream bidirectionally without inspecting or rewriting tool names, arguments, command strings, responses, notifications, or errors.

The exact-head E2E workflow builds the pinned WorkBridge/DesktopCommander source and sends a real `start_process` arbitrary command through this entire relay chain on Windows and Linux.

## Activation

The activation source is `tools/Enable-Lappy-DesktopCommander-Duplicate.ps1`. It verifies the exact WorkBridge manifest and runtime hashes, backs up the current tunnel configuration, swaps only the MCP executable/entrypoint/arguments, restarts the managed tunnel runtime, checks tunnel-client health, and restores the prior configuration if qualification fails.

Repository source and CI do not themselves prove Lappy installation or live ChatGPT consumption. Those are separate runtime effects.
