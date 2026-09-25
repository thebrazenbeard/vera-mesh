# Persistent Lappy -> ChatGPT Connection V1

Status: **SOURCE INTEGRATION CANDIDATE / NOT INSTALLED / NOT CONNECTED**

This branch restacks the useful persistence/diagnostic pieces from stale VeraMesh PR #27 onto the current PR #25 RDC-replacement head.

It does **not** make stale PR #27 the runtime foundation.

## Actual shortest secure path

The current source supports:

`ChatGPT -> OpenAI secure MCP tunnel -> tunnel-client -> veraport-mcp-stdio -> ControllerRuntime -> VeraPortAgent -> Lappy`

Persistence on Windows is intended to be:

- `VeraPortAgent` — workstation authority and data plane;
- `VeraMeshTunnelRuntime` — Windows SCM service supervising the managed tunnel runtime and stdio MCP child.

The separately restacked `VeraPortMCP` Windows service provides a persistent **loopback Streamable HTTP** MCP controller for local diagnostics/clients. It is useful, but it is **not required** for the secure-tunnel path because the tunnel runtime launches `veraport-mcp-stdio` directly.

Remote Desktop Commander is not a dependency of either path.

## Restacked pieces

This branch carries forward, against current PR #25:

- controller/MCP private-material ACL hardening and validation;
- persistent loopback `VeraPortMCP` Windows service;
- explicit `uvicorn` runtime dependency for that service;
- `veraport-doctor` read-only connection diagnostics.

It deliberately does **not** copy stale PR #27's old provisioning implementation because current #25 already has newer:

- `provision_local_pair`;
- `prepare_local_bootstrap`;
- `prepare_workstation_service`;
- secure tunnel runtime;
- stdio MCP entry point;
- current filesystem/process surface.

## Doctor

Offline:

```text
veraport-doctor
```

checks:

- VeraPort service configuration/runtime files;
- controller configuration/runtime files;
- tunnel runtime configuration/runtime files;
- controller workstation-key pin versus VeraPort workstation identity;
- controller TLS anchor versus VeraPort certificate;
- tunnel -> controller config lineage;
- process authority agreement;
- Windows ACLs when running on Windows.

Optional live read-only checks:

```text
veraport-doctor --live --tunnel-status
```

add:

- authenticated, data-plane-verified VeraPort route;
- managed tunnel runtime `process_running` / `healthy` status.

The doctor does not install/start services, create credentials, create a tunnel, enroll a controller, or register a ChatGPT app.

## Protected runtime frontier

After exact-head source qualification/review, the remaining actions are runtime effects and require explicit Patrick authorization for the exact effect:

1. materialize the qualified source/runtime on Lappy;
2. generate or reconcile controller/workstation identity material;
3. install/start `VeraPortAgent` as a Windows service;
4. provision an existing/new supported OpenAI tunnel/runtime credential as applicable;
5. install/start `VeraMeshTunnelRuntime`;
6. run `veraport-doctor --live --tunnel-status`;
7. connect/register the resulting MCP surface with ChatGPT;
8. perform an end-to-end read-only acceptance call from ChatGPT to Lappy;
9. only after that consider write/process capabilities or retiring RDC.

Each stage remains separately evidenced. A service being RUNNING is not the same as a current authenticated data plane, and a healthy tunnel is not the same as a successful ChatGPT-to-Lappy tool call.
