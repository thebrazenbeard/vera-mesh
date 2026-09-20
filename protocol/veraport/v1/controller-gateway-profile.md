# VeraPort V1 controller gateway profile

Status: implementation candidate.

The controller gateway is a thin tool facade over the persistent `HotSessionPool`.

It does **not** own:
- Lappy's network connection;
- application-session identity;
- lane state;
- filesystem/process authority;
- durable idempotency state.

Those remain in the VeraMesh/VeraPort session and Lappy runtime.

## Responsibilities

The gateway:
- exposes stable tool-shaped methods;
- validates its configured operation allowlist;
- generates controller-scoped request IDs;
- projects tool arguments into VeraPort V1 requests;
- submits those requests to the already-running hot-session pool.

A tool being discoverable does not imply it is enabled. Mutating operations require explicit gateway-policy inclusion in addition to all downstream session and workstation gates.

## Adapter independence

This gateway can be wrapped by MCP, an OpenAI Apps SDK server, another agent/tool protocol, or a local controller without changing the workstation protocol.

The adapter layer should remain disposable. Product/account-specific connector limitations must not alter VeraPort's Lappy runtime or network/session design.

## Current tool map

- `list_lanes` -> `lane.list`
- `open_lane` -> `lane.open`
- `renew_lane` -> `lane.renew`
- `close_lane` -> `lane.close`
- `read_text` -> `fs.read_text`
- `write_text` -> `fs.write_text`
- `run_process` -> `process.exec`

`process.exec` still requires the separate local Lappy grant even when the gateway operation allowlist includes it.
