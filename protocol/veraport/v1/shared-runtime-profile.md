# VeraPort V1 shared workstation runtime profile

Status: implementation candidate.

A permanent Lappy agent may serve multiple authenticated controller/application sessions at once. Those sessions must not create separate, conflicting views of workstation resource ownership.

## Shared agent rule

Every authenticated session is bound to one shared VeraPort agent/runtime instance for the workstation.

This preserves:
- one global resource collision domain;
- one global fencing allocator;
- one durable request ledger;
- one local process-execution policy.

Creating a new connection must not create a new collision universe.

## Lane isolation

Externally visible lane IDs are session-local names.

Before dispatch to the shared agent, the runtime namespaces a lane ID with the application session ID. Therefore two sessions may each use external `lane-1` without gaining access to one another's lane.

Resource claims are **not** namespaced. `fs:/repo` means the same Lappy resource regardless of controller/session, so conflicting writers still collide globally.

`lane.list` is filtered back to the calling session and internal lane prefixes are stripped before returning results.

## Durable request namespace

Durable request IDs are namespaced by controller principal, not by transport/application session ID.

This is intentional: when one controller reconnects or fails from direct to edge transport, resending the exact same external request ID maps to the same durable idempotency key on Lappy.

Different controllers may use the same external request ID without colliding with one another.

The controller/MCP gateway is responsible for generating request IDs that are unique over time for that controller.

## Capability ceiling

`lane.open` requested capabilities are checked against the authenticated `SessionBinding` before the shared agent receives the request.

The shared agent still applies its own local workstation policy, so application-session authorization can narrow local authority but never widen it.

## Expiry

An expired application session is rejected before dispatch to the shared agent even when the underlying transport socket remains open.
