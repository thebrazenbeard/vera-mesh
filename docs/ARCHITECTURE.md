# VeraMesh Architecture

## Scope

VeraMesh is the umbrella architecture for connecting Vera-capable nodes. VeraRelay is a current implementation/role inside that architecture, not a mandatory permanent hop and not a competing transport stack or identity system.

VeraMesh may absorb or replace the separately named VeraRelay component if the replacement preserves or strengthens its tested durability, replay, receipt, audit, enrollment, health, and recovery invariants.

## Separation of concerns

### Identity
A VeraMesh device has a stable cryptographic identity. The current Android implementation uses an Android Keystore P-256 keypair. Device ID is derived as SHA-256 over the SPKI-encoded public key. The private key remains device-local.

### Enrollment / pairing
Enrollment is explicit. A relay/edge may create a short-lived one-time token. The joining device presents its public key and signs a token-bound canonical pairing proof. The VeraMesh verifier confirms that the claimed device ID matches the supplied public key and verifies proof of possession before persisting an active device record.

### Transport
Transport is replaceable. Private carriers such as Tailscale are connectivity mechanisms, not Vera application authorization.

For interactive VeraPort/RDC work, the architecture is direct-first and persistent-session-first. The preferred live transport candidate is QUIC: one encrypted connection can carry many logical streams, so parallel VeraPorts do not require parallel public sockets or repeated handshakes.

### Edge / relay
The edge/relay capability set includes durable directional queues, application authentication, replay defense, acknowledgements, audit chaining, health/capability advertisement, cleanup, and lifecycle supervision.

These mechanisms may remain in VeraRelay or become modules of a general VeraMesh edge daemon. The architecture does not require live traffic to be written through durable relay queues when a current authenticated stream exists.

### Routing
Routing semantics belong to VeraMesh, not to Tailscale, Synology packaging, or a specific relay implementation. Nodes advertise role, capabilities, health, supported paths, protocol version, and relevant degradation state without turning transport reachability into authority.

## Interactive path model

VeraPort defines three semantic path classes:

```text
1. DIRECT_STREAM
   controller/gateway ========================= Lappy

2. EDGE_STREAM
   controller/gateway ===== VeraMesh Edge ===== Lappy

3. DURABLE_RELAY
   controller -> durable queue -> recipient/reconciliation
```

Selection prefers a current authenticated hot path over store-and-forward fallback. RTT is only a tie-breaker inside a path class.

The Synology can remain valuable as an always-on edge, rendezvous point, audit/durability surface, and fallback relay without becoming a mandatory latency hop.

## Controller / ChatGPT boundary

The ChatGPT-facing MCP/plugin adapter is not itself the permanent Lappy connection. It should be a thin request surface over VeraMesh.

A typical interactive flow is:

```text
ChatGPT tool call
  -> VeraMesh controller/MCP adapter
  -> already-hot authenticated VeraMesh session
  -> VeraPort lane on Lappy
  -> streamed result
```

The Lappy session should remain alive across many tool calls and many logical lanes. Reconnecting the MCP caller must not unnecessarily tear down the underlying VeraMesh session.

## Durable/fallback message flow

When a hot stream is unavailable:

```text
Controller / node
  -> private/public ingress adapter
  -> VeraMesh edge/relay
  -> durable directional queue
  -> recipient node
```

Durable messages carry stable message IDs, ciphertext payloads, sequence information, timestamps/expiry information, and acknowledgement progression. Persistence provides eventual-delivery behavior across ordinary process restarts.

## Acknowledgement model

The relay/edge distinguishes receipt by the infrastructure from later recipient outcomes. Existing states include `relay_received`, `recipient_delivered`, `recipient_processed`, and `rejected`. Delivery state must not be promoted beyond what has actually been observed.

Live-stream acceptance likewise must not be confused with Lappy operation completion. VeraPort operation results and durable receipts remain separate evidence classes.

## Health and capabilities

A VeraMesh node should distinguish at least:
- process alive
- edge/relay core healthy
- audit integrity valid
- listener boundary correct
- application authentication enabled
- hot-stream path present/absent
- durable fallback present/absent
- pairing enabled/disabled
- queue/degradation state
- VeraPort capability ceiling
- current path class and freshness

`Running` in a package manager is not sufficient evidence of end-to-end health.

## Persistence boundaries

Durable state should survive upgrades. Current VeraRelay/Synology categories include config, devices, auth/replay state, queues, audit history, runtime state, and diagnostics. Upgrade logic must preserve valid predecessor data and explicitly handle schema or format migrations rather than silently resetting state.

VeraPort additionally persists fencing/idempotency evidence when configured. Process-local live lanes are not currently claimed to survive restart.

## VeraMesh growth path

Node roles may include phones, workstations, local Vera runtime hosts, edge/relay nodes, observers, or additional trusted appliances.

The architecture therefore keeps identity, transport, routing, delivery, state, controller adaptation, RDC capability, and authority independently evolvable.

See `protocol/veraport/v1/synchrony-profile.md` for the direct-first interactive profile.
