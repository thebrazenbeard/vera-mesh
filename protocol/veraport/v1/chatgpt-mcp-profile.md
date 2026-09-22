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
- `fs_read_bytes`
- `fs_stat`
- `fs_list_dir`
- `fs_search`
- `fs_write_text`
- `process_exec`
- `process_start`
- `process_list`
- `process_status`
- `process_output`
- `process_terminate`

Process capabilities remain additionally gated by Lappy local policy. Tool discovery does
not enable them. Managed process handles are opaque and bound to the owning lane/fence;
lane close and application-session disconnect reap owned children.

The current source also includes a transparent VeraRelay/VeraMesh live edge carrier.
The edge forwards opaque bytes and does not terminate VeraPort TLS, parse VeraPort frames,
or receive workstation credentials.

## ChatGPT product boundary

The server uses MCP Streamable HTTP and defaults to loopback `127.0.0.1:17446/mcp`.

ChatGPT does not directly connect to a local MCP listener. Remote exposure through a reviewed
deployment or supported private MCP tunnel is a separate install/route step.

Repository source does not establish MCP server runtime, plugin registration, current route,
or live VeraPort operation.


## R2 hostile repairs

- Workstation text reads are bounded by local max_read_bytes. If JSON serialization
  still pushes a successful response beyond the stream frame ceiling, the stream layer
  returns a correlated FRAME_TOO_LARGE error when that error fits, otherwise it closes
  the stream so pending callers fail rather than hang.
- Wire-safe ranged reads use fs.read_bytes with byte offsets, bounded chunks,
  base64 transport, EOF/size metadata, and a file-version token. Every chunk remains
  subject to the same lane, fencing, root, and fs.read authorization.
- The initial MCP listener is source-enforced loopback-only. Remote ChatGPT exposure must
  use a separately reviewed secure tunnel or HTTPS adapter that forwards to loopback.
- Read-only filesystem lanes are controller-logical lanes. Each VeraPort application
  session receives its own read-only mirror and fencing token. A direct to edge read
  failover therefore materializes authority inside the edge application session instead
  of reusing a fence from another session.
- Write, process, and other session-mutating operations do not acquire cross-session
  logical mirroring.
