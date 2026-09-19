# VeraPort V1

VeraPort is a VeraMesh application protocol for persistent, parallel, capability-bounded workstation access. A "port" is a logical execution lane, not a TCP/UDP port.

One authenticated VeraMesh session may own many concurrent VeraPort lanes. Each lane binds a task, narrowed capabilities, resource claims, an expiring lease, and a fencing token. Independent lanes may execute concurrently. Overlapping claims reject when either side is a writer.

## Interactive path order

VeraPort is direct-first:

1. `DIRECT_STREAM` — current authenticated hot path to Lappy;
2. `EDGE_STREAM` — current authenticated hot path through a VeraMesh edge;
3. `DURABLE_RELAY` — store-and-forward fallback/recovery.

See `synchrony-profile.md` and `path.schema.json`.

VeraRelay is not required to remain a separately named mandatory hop. Its durable mechanisms may become a VeraMesh edge/relay capability set. Hot interactive traffic should not be queued merely because durable relay storage exists.

## Operations

Initial operations:
- `lane.open`
- `lane.renew`
- `lane.close`
- `lane.list`
- `fs.read_text`
- `fs.write_text`
- `process.exec`

Initial resource namespaces are `fs:<canonical-path>` and `cwd:<canonical-path>`. Parent claims cover descendants. Read/read overlap is permitted.

A lane can only narrow authority from its authenticated session ceiling. VLAN placement, transport membership, relay custody, pairing, or message possession does not create execution authority.

`process.exec` is deliberately broad host execution authority. A `cwd:` claim is collision/accounting metadata, not an OS sandbox. The reference agent excludes `process.exec` from its default session ceiling and only enables it with the explicit local `--allow-process-exec` startup grant.

The reference agent may use `--state-db PATH` for SQLite-backed durable fencing and request idempotency. Completed duplicate requests replay as historical evidence with `current_state_not_implied=true`; requests left pending across a crash remain `REQUEST_OUTCOME_UNKNOWN` and are not blindly re-executed.

Active lane membership remains process-local in the current reference cut. Restart invalidates the live lane set while durable fencing continues monotonically.

`reference/veraport_agent` is currently a local reference implementation. Its JSONL stdio adapter demonstrates multiplexed concurrent requests without exposing a network listener. The authenticated hot network/MCP bridge is the next implementation stage.
