# Lappy VeraMesh Activation Packet V1

Status: **AUTHORIZED DEPLOYMENT PREP / LOCAL EXECUTION REQUIRED**

This packet is deliberately separate from frozen VeraMesh PR #29. It installs the exact qualified VeraMesh source head:

`95e1c8a3d60e8b1ac9165f46ab317b50e58c57f9`

It also pins the official OpenAI `tunnel-client v0.0.11` Windows amd64 release archive to SHA-256:

`eb912c86c6ccde90cda805cb17009507176a656725cf86c36fabe1901a12e29b`

## Intended first connection

`ChatGPT -> Secure MCP Tunnel -> tunnel-client -> veraport-mcp-stdio -> VeraPortAgent -> Lappy`

The packet installs two automatic Windows services:

- `VeraPortAgent`
- `VeraMeshTunnelRuntime`

It does not install the optional `VeraPortMCP` HTTP service because that service is not required for the secure-tunnel path.

## Authority ceiling

The first activation is intentionally filesystem-only.

Process execution is not enabled.

VeraPort remains loopback-only; the packet creates no inbound firewall rule and no non-loopback listener.

The allowed filesystem root defaults to the interactive user's profile directory and can be narrowed or replaced with `-AllowedRoot`.

## Provider material

The tunnel ID and runtime API key are not stored in GitHub.

When run with `-Apply`, the script opens OpenAI's Tunnels and organization API-key settings if a tunnel ID was not supplied. The operator should:

1. create or select a tunnel scoped to the intended ChatGPT workspace;
2. create a restricted runtime API key with **Tunnels: Read + Use**;
3. paste the tunnel ID into the prompt;
4. paste the runtime key into the hidden secure prompt.

The key is written only to protected local storage beneath `C:\ProgramData\VeraMesh\secrets`. Its value is not written to the activation receipt.

## Safety behavior

The packet refuses to overwrite:

- an existing `VeraPortAgent` service;
- an existing `VeraMeshTunnelRuntime` service;
- an existing `VeraPortMCP` service;
- non-empty existing VeraMesh ProgramData state.

If any of those exist, stop and reconcile them rather than replacing them.

Failure preserves generated state and writes a local failure receipt so identity or service state is not silently destroyed.

## Success gate

Success requires `veraport-doctor --live --tunnel-status` to pass, which verifies both an authenticated VeraPort data plane and a running/healthy tunnel runtime.

That still does not prove ChatGPT end-to-end access. The script then opens ChatGPT connector settings. The final gate is selecting the tunnel in ChatGPT and making a read-only tool call to Lappy.
