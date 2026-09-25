# VeraMesh VeraPort / VeraRDC V1 design

Date: 2026-09-19
Status: IMPLEMENTATION CANDIDATE / LOCAL CORE BUILT / DIRECT-FIRST SYNCHRONY PROFILE ADDED / NETWORK BRIDGE NOT DEPLOYED

## Purpose

VeraPort extends VeraMesh toward a durable, user-owned bridge between a Vera controller surface and Patrick's workstation ("Lappy"). It is a persistent execution fabric in which one workstation connection can expose many independent logical execution lanes in parallel.

A VeraPort is a logical lane, not a literal TCP/UDP port. Each lane has a task, narrowed capabilities, resource claims, an expiring lease, a fencing token, execution context, correlated results, and an audit identity.

## Corrected synchrony target

VeraRelay is not required to remain the center of the live path.

The preferred topology is:

```text
HOT PATH A — DIRECT_STREAM
controller/gateway ================= Lappy

HOT PATH B — EDGE_STREAM
controller/gateway ===== VeraMesh edge ===== Lappy

FALLBACK — DURABLE_RELAY
controller -> edge/relay durable queue -> Lappy/reconciliation
```

The first current authenticated healthy hot path wins. Durable relay is fallback/recovery, not a mandatory serialization point.

A Synology node can remain an always-on edge for rendezvous, gateway, discovery, audit, offline queueing, and fallback. If a direct stream is viable, Synology does not need to sit in the interactive data path.

VeraRelay may be retained, absorbed into a broader VeraMesh edge daemon, split into modules, or replaced. Its name and process topology are not invariants; its useful tested guarantees are.

## Controller/MCP boundary

The ChatGPT-facing MCP/plugin adapter is intentionally thin. It should not own workstation execution state or require a new Lappy handshake for every tool call.

The persistent VeraMesh session lives behind the adapter. MCP calls are projected into that existing session and then into one or more VeraPort lanes.

This separates:
- ChatGPT/MCP request lifecycle;
- VeraMesh session lifecycle;
- VeraPort lane lifecycle;
- Lappy process/resource lifecycle.

A reconnect at one layer must not silently imply restart or currentness at another.

## Live transport candidate

QUIC is the preferred implementation candidate for the live VeraMesh data plane because one secured connection can expose many independent streams and support connection migration.

Candidate mapping:
- reliable bidirectional streams: command/result, terminal, ordered file/control transfer;
- unidirectional streams: telemetry/event fanout where useful;
- datagrams later: screen/telemetry deltas where dropping stale data is preferable to waiting for retransmission.

QUIC transport encryption is not Vera authorization. The VeraMesh session still binds authenticated principals, capability ceilings, freshness/replay state, and revocation.

## Roles

- `vera-controller`: requests workstation work within an authenticated session ceiling.
- `workstation-agent`: executes only operations allowed by local Lappy policy.
- `mesh-edge`: optional live proxy/rendezvous/durable fallback role.
- `durable-relay`: store-and-forward fallback capability; may be implemented by the mesh edge.

Existing VeraMesh V1 phone/host roles remain compatible until a later explicit integration cut updates normative role topology.

## Capability and lane model

The session establishes a capability ceiling. A lane may only request a subset of that ceiling.

Initial implemented reference capabilities:
- `fs.read`
- `fs.write`
- `process.exec` (broad host authority, disabled by default local policy)

Planned: process inspection/signaling, screen capture, UI observation, UI control.

Each lane contains stable `lane_id` and `task_id`, explicit resource claims, a monotonic fencing token, lease expiry, and request/result correlation.

Initial resource namespaces:
- `fs:<canonical-path>`
- `cwd:<canonical-path>`

Read/read overlap is allowed. Overlapping claims with a writer fail closed.

Every operation carries the current fencing token. Expired/closed/reopened lanes reject stale tokens.

## Durable restart and idempotency

The local reference may use a SQLite state database configured by `--state-db`. It stores a monotonic fence counter and request-idempotency ledger with `PENDING` / `COMPLETED` state.

Fence allocation uses SQLite `BEGIN IMMEDIATE` transactions so distinct store connections cannot allocate the same fencing token.

A stable request ID is bound to a SHA-256 digest of the canonical request object. Reusing the ID with different content is `IDEMPOTENCY_CONFLICT`.

A completed duplicate request is not executed again. Its stored response is historical evidence with current-state nonimplication.

A request left `PENDING` by process death remains `REQUEST_OUTCOME_UNKNOWN`; it is not blindly rerun.

Active lanes remain process-local in this reference cut. Restart invalidates the live lane set while durable fencing continues advancing.

## Execution primitives

Filesystem access is bounded by local allowed roots. Text writes use temporary-file plus atomic replacement.

`process.exec` uses an argv vector and `create_subprocess_exec`, never implicit `shell=True`. It is disabled by default and requires explicit local `--allow-process-exec`.

An allowed `cwd` is not an OS sandbox. If broad process execution is enabled, the child program has whatever authority the Lappy OS account grants unless a real sandbox/profile is added.

## Synchrony rules

1. Keep a current authenticated hot session open when possible.
2. Opening a logical lane must not require opening a new network connection.
3. Prefer `DIRECT_STREAM`, then `EDGE_STREAM`, then `DURABLE_RELAY`.
4. Never select stale, unhealthy, or unauthenticated paths to save latency.
5. Re-evaluate immediately on path failure rather than waiting for slow polling.
6. Do not retry ambiguous mutations merely because a connection dropped.
7. Preserve fencing/idempotency across transport reconnection.
8. Measure bridge-added latency separately from model/tool-call latency.

The reference `synchrony.py` selector implements this semantic ordering and is independently focused-tested.

## Network acceptance requirements

Before a network adapter is accepted it must demonstrate:
1. cryptographic controller and workstation identity;
2. replay-resistant session establishment;
3. session-bound capability grants;
4. no trust based solely on VLAN/LAN/tailnet/IP/TCP source;
5. revocation and a local kill switch;
6. bounded lane/process/output resources;
7. reconnect and ambiguous-outcome reconciliation;
8. reviewed transport confidentiality/integrity;
9. no edge/relay possession of endpoint OS credentials;
10. default-deny or real sandboxing for broad `process.exec`;
11. durable request identity integrated with transport/session replay controls;
12. direct/edge/relay path failover does not duplicate mutation;
13. hostile end-to-end testing before Lappy installation.

A VLAN can reduce lateral-movement blast radius; it is containment, not identity or authorization.

## Reference implementation

`reference/veraport_agent` currently implements capability narrowing, collision claims, expiring leases, durable fencing/idempotency, root-bounded filesystem operations, atomic writes, default-deny argv process execution, concurrent JSONL dispatch, and deterministic direct/edge/relay path selection.

Existing local core evidence remains 13/13 passing on its exact source blobs. The new synchrony selector is separately 6/6 focused-pass locally. No claim is made that a combined network runtime has been qualified.

## Acceptance stages

`SOURCE_CREATED` -> `LOCAL_CORE_TEST_PASS` -> `DIRECT_NETWORK_ADAPTER_BUILT` -> `DIRECT_NETWORK_HOSTILE_PASS` -> `LAPPY_AGENT_INSTALLED` -> `CONTROLLER_ADAPTER_CONNECTED` -> `END_TO_END_ACCEPTED`.

Durable-relay compatibility/replacement can proceed in parallel, but no earlier state implies a later state.

## Next frontier

Implement the hot direct session adapter first, then edge-proxy/fallback integration. Do not optimize the store-and-forward relay before the interactive hot path exists.
