# VeraPort V1 controller routing and failover profile

Status: implementation candidate.

The controller-facing adapter should route a tool request into an already-authenticated VeraPort session rather than create a new workstation connection per tool call.

## Selection

For one workstation, the controller maintains current authenticated session endpoints. Each endpoint combines:
- a VeraPort session binding;
- one path observation;
- one request channel;
- whether the target agent has durable request-idempotency protection.

Selection uses the synchrony profile: direct stream, then edge stream, then durable relay.

Expired application sessions are ineligible even if their underlying socket still exists.

## Failover

Read-only requests may be reissued over another current path after transport loss.

Mutation retry is stricter. If a stream disappears after the request may have reached Lappy, the controller MUST NOT retry that mutation over another path unless the exact target agent has durable request-idempotency/reconciliation state and the controller resends the exact same request ID and request content.

Without that protection, the result is `AMBIGUOUS_DELIVERY`.

This converts direct-to-edge failover from "hope it did not run twice" into an explicit contract with the Lappy agent's durable idempotency ledger.

## MCP projection

The MCP/plugin surface should provide tool arguments and receive the VeraPort result, but the hot-session pool owns path selection. MCP reconnection does not itself recreate the Lappy application session.

## Current mutation classification

The reference controller treats these as mutating:
- `lane.open`
- `lane.renew`
- `lane.close`
- `fs.write_text`
- `process.exec`

New operations must be explicitly classified before automatic failover is permitted.
