# Lappy VeraMesh Activation Packet V1

Status: **SOURCE QUALIFIED CANDIDATE / LAPPY APPLY AUTHORIZED / CHATGPT E2E NOT YET PROVEN**

## Current Lappy condition

The current Lappy is not a blank workstation. Live read-only inspection on 2026-09-22 established an existing automatic `VeraPortAgent` service using:

- `C:\ProgramData\VeraMesh\veraport.json`;
- loopback bind `127.0.0.1:17444`;
- allowed roots `C:\Users\patri` and `C:\Temp`;
- process execution disabled;
- existing workstation/controller/TLS identity material;
- a previously enrolled controller private-key candidate at `C:\ProgramData\VeraMesh\controller\vera-controller-bootstrap.pem`.

The activation packet therefore has two modes.

### Existing-install mode — current Lappy

`tools/windows_activate_lappy_veramesh.ps1` detects an existing VeraPort service/config and dispatches to:

`tools/windows_attach_existing_lappy_veramesh.ps1`

That path does **not** replace VeraPort. It preserves the current service, workstation key, controller trust list, TLS identity, state DB, allowed roots, and process policy.

It first proves that the selected existing P-256 controller private key is already enrolled in the workstation's current `controllers.json`. It then derives only the controller-side public pin and TLS CA required by the new stdio tunnel controller.

The first Secure MCP Tunnel route requests only `fs.read` and exposes only read/lane operations. It does not silently promote the existing workstation's broader trust ceiling into the ChatGPT tunnel.

### Fresh-install mode

If neither an existing `VeraPortAgent` service nor `C:\ProgramData\VeraMesh\veraport.json` exists, the original fresh-install packet remains available. It creates a new filesystem-only VeraPort identity and installs both VeraPort and the tunnel runtime.

## Secure MCP Tunnel runtime

Current existing-install packet source pin:

`09cd149a924c5d428df840d8b7be036fc2d070a8`

Current OpenAI `tunnel-client` pin for existing-install attach:

- release: `v0.0.14`
- Windows amd64 archive SHA-256: `784ab8da7b5a88f0109f1fd8aaf0a1c86067430b896dddf307ef7e3cc49fa1a5`

Intended route:

`ChatGPT -> OpenAI Secure MCP Tunnel -> tunnel-client -> veraport-mcp-stdio -> ControllerRuntime -> existing VeraPortAgent -> Lappy`

RDC is not a dependency.

## Provider material

The tunnel ID and restricted runtime API key are not stored in GitHub.

The operator supplies:

1. an existing or newly selected Secure MCP Tunnel ID;
2. a runtime API key restricted to Tunnels **Read + Use**.

The key is written only beneath protected local ProgramData storage. Its value is not written into receipts.

## Existing-install write/effect boundary

The attach path may create:

- an isolated tunnel-side portable Python runtime;
- the pinned `tunnel-client` binary;
- controller-side public pin / TLS CA copies;
- `controller.json` and `tunnel-runtime.json`;
- the protected runtime-key file;
- the `VeraMeshTunnelRuntime` Windows service;
- local activation/diagnostic receipts.

It does not:

- stop, reinstall, replace, or reconfigure `VeraPortAgent`;
- rotate workstation/controller trust or TLS identity;
- change existing allowed roots;
- enable process execution;
- change firewall or Tailscale configuration;
- merge a repository;
- register/select the tunnel inside ChatGPT.

Any pre-existing tunnel/controller sidecar material causes fail-closed reconciliation rather than overwrite.

## Mandatory success order

Local apply is not enough. Acceptance remains:

1. `veraport-doctor --live --tunnel-status`;
2. authenticated VeraPort data plane PASS;
3. managed tunnel runtime running + healthy;
4. select/associate the Secure MCP Tunnel in ChatGPT;
5. perform a read-only Lappy tool call;
6. only then call the ChatGPT/Lappy route current.

Source/CI/install/service/tunnel/ChatGPT/effect remain separate states.
