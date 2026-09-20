# VeraMesh Route Health and Convergence V1

Status: **SOURCE DESIGN ONLY / NOT RUNTIME ACTIVATED**

VeraMesh already separates process health, relay health, private transport reachability,
application authentication, delivery receipts, and authority. This contract adds the
missing routing rule: a route is not healthy merely because a peer, tunnel, transport,
or handshake is reachable.

`HANDSHAKE_SUCCESS != DATA_PLANE_HEALTH`

`LAST_KNOWN_GOOD != CURRENTLY_VERIFIED`

`ROUTE_SELECTED != EFFECT_AUTHORIZED`

## Route states

A route progresses through `DISCOVERED`, `CONTROL_PLANE_REACHABLE`, and
`DATA_PLANE_VERIFIED`. A failed or stale route may become `DEGRADED`; a route that
cannot satisfy the governing policy becomes `INELIGIBLE`.

Only a fresh, exact data-plane PASS can establish `DATA_PLANE_VERIFIED`.

A data-plane verification binds the route, exact source and destination node identities,
transport adapter/config digest, exact probe or message identity, relay custody receipt,
recipient receipt, observed result, observation time, and the governing freshness-policy
digest. The freshness window is policy-defined rather than hard-coded by this design.

## Convergence

After a route/link failure, the failed route becomes degraded. An alternate route does
not become verified merely because discovery, transport membership, or a handshake
succeeds. It must produce a fresh data-plane PASS under the same exact verification
contract.

If no alternate route passes, the result is `ROUTE_UNAVAILABLE_FAIL_CLOSED`.

A last-known-good route may be probed first as an optimization. Its historical success
never self-promotes into current health.

## Authority boundary

Route health and routing are connectivity claims. They do not grant Vera application
authorization, protected-effect permission, deployment authority, or provider authority.
Existing application-auth and effect-governance gates remain independent.

## External pattern provenance

This design adapts general research patterns from:

- `encodeous/nylon@c4a96c804f7aa08512721dec7994907eab100bc8`: decentralized/self-healing
  route convergence after link loss.
- `CluvexStudio/Aether@0e6f6a5218e65ed4cddc68d1a71d9b9633f89e3f`: validate a route with
  actual data-plane traffic and retain last-known-good only as a reconnect candidate.

No source code is imported. Aether is AGPL-3.0 and remains research-only here.

## Non-effects

This contract does not select a transport, modify Tailscale or another provider, activate
a route, send a network probe, install code, change credentials, grant authority, or
perform a protected effect.
