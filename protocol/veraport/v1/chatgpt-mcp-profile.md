# VeraPort ChatGPT MCP Profile V1

Status: **SOURCE IMPLEMENTATION CANDIDATE / NOT INSTALLED / NOT CURRENT ROUTE**

The MCP layer is deliberately thin. It does not own workstation authority, durable execution
state, or VeraPort application identity.

Path:

`ChatGPT -> remote MCP -> ControllerRuntime -> VeraPortGateway -> HotSessionPool ->
DIRECT_STREAM / EDGE_STREAM -> VeraPortAgent`

## Required separations

- `TOOL_DISCOVERABLE != TOOL_AUTHORIZED`
- `MCP_CONNECTED != VERAPORT_SESSION_RECREATED`
- `TLS_HANDSHAKE_PASS != DATA_PLANE_CURRENT`
- `EDGE_REACHABLE != APPLICATION_AUTHORIZED`
- `SOURCE_CREATED != MCP_SERVER_RUNNING != PLUGIN_REGISTERED != CURRENT_ROUTE`
- `READ_PASS != WRITE_PASS != PROCESS_EXEC_PASS`

The controller authenticates every candidate endpoint and then performs a real
`lane.list` application request before the path becomes healthy/current. Direct is preferred
over edge only among fresh authenticated data-plane-verified paths.

The controller private key, TLS trust anchor, and independently pinned VeraPort workstation
application public key are external runtime configuration. TLS identity is not reused as
proof of VeraPort application identity. These materials are never repository secrets.

MCP reconnects reuse the controller process's existing VeraPort sessions. Session recreation
occurs only when an application session is absent or expired, not because an MCP client
reconnected.

Mutation failover retains the existing VeraPort rule: after ambiguous transport loss, a
mutation may only retry on a durable path bound to the same application session. Direct and
edge sessions are not assumed equivalent merely because they reach the same workstation.

## Initial tool surface

Always registered:
- `machine_info`
- `lane_list`
- `lane_open`
- `lane_renew`
- `lane_close`
- `fs_read_text`

Conditionally registered only when controller policy explicitly includes the operation:
- `fs_write_text`

The current Lappy production policy observed on 2026-09-20 has
`allow_process_exec=false`. Process execution is therefore not exposed by this source cut.

Process list/status/output/termination are not yet VeraPort V1 primitives. RDC-equivalence
must remain FAIL/PARTIAL for those rows until explicit process-lifecycle protocol/runtime
extensions exist and are independently reviewed.

## ChatGPT product boundary

The server uses MCP Streamable HTTP and defaults to loopback `127.0.0.1:17446/mcp`.

ChatGPT does not directly connect to a local MCP listener. Remote exposure through a reviewed
deployment or supported private MCP tunnel is a separate install/route step.

Repository source does not establish MCP server runtime, plugin registration, current route,
or live VeraPort operation.
