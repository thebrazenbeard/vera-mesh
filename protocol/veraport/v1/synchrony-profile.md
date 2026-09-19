# VeraPort V1 synchrony profile

## Goal

Minimize time between controller intent and observable Lappy effect without weakening authentication, authority, currentness, collision control, or durable recovery semantics.

"Synchronous" here means event-driven, already-connected, multiplexed interaction wherever technically possible. It does not mean pretending a remote operation is local or claiming zero latency.

## Path classes

VeraPort defines three path classes in priority order:

1. `DIRECT_STREAM` — an authenticated persistent controller/gateway-to-Lappy data path whose application payload is not store-and-forwarded through VeraRelay.
2. `EDGE_STREAM` — an authenticated persistent stream through a VeraMesh edge node, such as Synology, which proxies live traffic without queueing every frame.
3. `DURABLE_RELAY` — store-and-forward control/recovery path used when no current hot stream is available or when durability is more important than interactive latency.

Path selection is fail-closed. An unauthenticated, unhealthy, or stale direct path does not outrank a current authenticated edge path.

RTT is a tie-breaker only inside the same path class. A nominally faster durable-relay observation does not displace a viable hot stream.

## Hot-session rule

Opening a VeraPort lane MUST NOT require creating a fresh network connection when a current authenticated hot session already exists.

The live connection should remain established across many logical lanes and requests. Lane identity, capability ceilings, fencing, and request idempotency remain application-layer state and do not depend on the underlying stream number.

## Transport target

The preferred live data-plane candidate is QUIC because it natively provides multiple independent streams within one encrypted connection and supports connection migration. VeraPort may use reliable streams for commands, terminal/file transfer, and ordered control events; a later screen/telemetry profile may use unreliable datagrams where loss is preferable to delay.

QUIC is a candidate transport, not VeraPort identity or authorization. The VeraMesh authentication/session layer remains required even when transport encryption is present.

## Controller / MCP boundary

The ChatGPT-facing adapter is not the permanent workstation connection.

The adapter receives a controller request and forwards it into an already-established VeraMesh session whenever one exists. The current MCP Streamable HTTP surface may return direct HTTP results or streamed events, but VeraPort persistence, lane identity, and reconnection state live behind that adapter.

A controller-visible `mesh_session_handle` may identify application session state. It is not an authority token by itself.

## VeraRelay disposition

VeraRelay is not required to remain a separately named component.

Its useful mechanisms — durable queueing, replay defense, receipts, audit integrity, enrollment, health, and fallback delivery — may be:

- retained as the `DURABLE_RELAY` role;
- absorbed into a general VeraMesh edge daemon;
- split into reusable VeraMesh modules;
- replaced if another implementation preserves the same or stronger tested invariants.

The hot path should not route through durable relay storage merely because the relay exists.

## Synology role

A Synology node can serve as an always-on VeraMesh edge:

- rendezvous/bootstrap;
- public/controller-facing gateway where needed;
- durable fallback queue;
- audit/receipt storage;
- health and node discovery;
- live proxy to Lappy when a direct controller path cannot be established.

When Lappy is directly reachable through an authenticated hot path, Synology should not add a mandatory store-and-forward hop.

## Freshness and liveness

Path observations must be timestamped and expire quickly. The reference selector defaults to a five-second observation horizon; production values are deployment policy, not a protocol constant.

Liveness should be event/heartbeat driven, not based on slow polling. A dropped hot path causes immediate path reevaluation.

## Reconnection

After reconnect:

- allocate new/current transport/session state;
- preserve durable fencing monotonicity;
- never infer that process-local live lanes survived;
- reconcile `REQUEST_OUTCOME_UNKNOWN` operations before retry;
- reuse completed idempotent evidence only as historical evidence unless currentness is separately re-established.

## Measurable synchrony

Implementations should separately measure:

- controller ingress -> VeraPort dispatch;
- VeraPort dispatch -> Lappy operation start;
- Lappy completion -> controller-visible result;
- reconnect detection and hot-path restoration.

The optimization target is minimum added bridge latency while preserving the security/currentness invariants above. Absolute latency acceptance thresholds require measurement on the actual Lappy/Synology/controller path before freezing.
