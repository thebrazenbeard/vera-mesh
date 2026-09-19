# VeraMesh VeraPort / VeraRDC V1 design

Date: 2026-09-19
Status: IMPLEMENTATION CANDIDATE / LOCAL REFERENCE SLICE BUILT / NETWORK BRIDGE NOT DEPLOYED

## Purpose

VeraPort extends VeraMesh toward a durable, user-owned bridge between a Vera controller surface and Patrick's workstation ("Lappy"). It is not primarily a remote-desktop clone. It is a persistent execution fabric in which one workstation connection can expose many independent logical execution lanes in parallel.

A VeraPort is a logical lane, not a literal TCP/UDP port. Each lane has a task, narrowed capabilities, resource claims, an expiring lease, a fencing token, execution context, correlated results, and an audit identity.

## Two-plane architecture

```text
CONTROL PLANE
controller <-> VeraRelay <-> Lappy
identity, bootstrap, durable jobs, reconciliation, receipts, revocation

LIVE DATA PLANE
controller ================= Lappy
one authenticated multiplexed connection carrying many VeraPort lanes
```

VeraRelay remains a blind durable courier. It MUST NOT become a shell daemon, hold Lappy OS credentials, or infer execution authority from transport membership.

A future live data plane is for high-volume terminal/file/screen traffic. If it disappears, the durable control plane retains enough state to reconcile accepted work rather than blindly retrying.

## Roles

VeraPort introduces application-level extension roles without silently rewriting current VeraMesh V1 phone/host authorization:

- `vera-controller`: requests workstation work within an authenticated session ceiling.
- `workstation-agent`: executes only operations allowed by local Lappy policy.
- `relay`: transports sealed control material and receipts, with no workstation authority.

Existing VeraMesh V1 `phone-client`, `vera-host`, and `relay-admin` semantics remain unchanged until an explicit integration cut updates the normative role topology.

## Capability and lane model

The session establishes a capability ceiling. A lane may only request a subset of that ceiling.

Initial implemented reference capabilities:
- `fs.read`
- `fs.write`
- `process.exec` (broad host authority, disabled by default local policy)

Planned but not implemented: process inspection/signaling, screen capture, UI observation, UI control.

Each lane contains:
- stable `lane_id` and `task_id`;
- explicit resource claims;
- monotonic fencing token;
- lease expiry;
- request/result correlation.

Initial resource namespaces:
- `fs:<canonical-path>`
- `cwd:<canonical-path>`

Read/read overlap is allowed. If overlapping claims include a writer, the later lane fails closed. A future Git adapter should add semantic claims such as `git:<repo>#<branch>`.

Every operation carries the current fencing token. Expired/closed/reopened lanes reject stale tokens so a disconnected old worker cannot resume mutations after ownership has changed.

## Execution primitives

Filesystem access is bounded by local allowed roots. Remote capability claims cannot widen those roots.

Text writes use temporary-file plus atomic replacement in the reference implementation.

`process.exec` accepts an argv vector and explicit working directory and uses `create_subprocess_exec`; it never silently invokes `shell=True`. It is disabled by default. The workstation operator must explicitly start the reference agent with `--allow-process-exec` before the session ceiling can contain that capability.

Critically, an allowed `cwd` is not an OS sandbox. Once arbitrary process execution is explicitly enabled, the child program may possess whatever filesystem/process/network authority the Lappy OS account gives it. Future network acceptance must either preserve that as an explicit broad-host grant or introduce a real OS sandbox/command profile; it must not pretend path claims contain arbitrary child effects.

Execution has time and output bounds.

## Multiplexing

One physical connection may carry many lane IDs and responses may finish out of order. The reference JSONL adapter demonstrates this by creating an async task per request while serializing only response writes.

A production network adapter should use a mature multiplexed transport rather than opening one public network port per VeraPort.

## Network acceptance requirements

No network listener is exposed in this source cut. Before a network adapter is accepted it must demonstrate:

1. cryptographic controller and workstation identity;
2. replay-resistant session establishment;
3. capability grants bound to the exact session and workstation;
4. no trust based solely on VLAN/LAN/tailnet/IP/TCP source;
5. revocation and a local kill switch;
6. bounded lane/process/output resources;
7. reconnect and ambiguous-outcome reconciliation;
8. standard reviewed transport confidentiality/integrity;
9. no relay possession of endpoint OS credentials;
10. `process.exec` remains absent unless local workstation policy explicitly enables broad host execution or a reviewed narrower sandbox/profile;
11. hostile end-to-end testing before Lappy installation.

A VLAN may reduce lateral-movement blast radius, but it is optional containment, not VeraPort identity or authorization.

## ChatGPT/MCP boundary

VeraPort is intentionally independent of one controller vendor. An MCP/app adapter can expose VeraPort to ChatGPT, but changing that adapter must not require redesigning the Lappy agent. This is the layer that removes dependence on the current third-party RDC quota.

## Reference implementation

`reference/veraport_agent` implements:
- capability narrowing;
- max-lane limit;
- hierarchical read/write collision claims;
- expiring leases;
- monotonic fencing;
- stale-fence rejection;
- filesystem root containment;
- atomic text replacement;
- argv-only subprocess execution;
- default-deny local gate for broad `process.exec`;
- timeout/output bounds;
- concurrent dispatch over one JSONL stdio stream.

The stdio adapter is deliberately local-only. It is a reference multiplexing surface to be wrapped by the future authenticated bridge, not an Internet-facing shell.

Fresh local construction test result: **8 passed / 0 failed** on Python 3.13.5 / pytest 9.0.2.

No Lappy process, network listener, relay deployment, credential, MCP registration, port exposure, merge, or OS mutation occurred.

## Acceptance stages

`SOURCE_CREATED` -> `LOCAL_TEST_PASS` -> `NETWORK_ADAPTER_BUILT` -> `NETWORK_HOSTILE_PASS` -> `LAPPY_AGENT_INSTALLED` -> `CONTROLLER_ADAPTER_CONNECTED` -> `END_TO_END_ACCEPTED`.

No earlier state implies a later state.

## Next frontier

Build the authenticated network/session adapter and controller gateway around the existing lane engine, with a real least-privilege decision for process execution, without widening authority.
