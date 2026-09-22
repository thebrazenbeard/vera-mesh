# VeraPort mutation ledger and request-admission profile V1

Status: SOURCE CONTRACT / NOT INSTALLED BY SOURCE PRESENCE

## Stream admission

`max_inflight` is an admitted-outstanding-request bound.

A VeraPort server acquires capacity before reading the next request frame and before
creating its handler task. When all capacity is occupied, the server stops reading and
relies on transport backpressure. It does not create an unbounded in-process waiting-task
queue.

`MAX_INFLIGHT != UNBOUNDED_QUEUE_WITH_BOUNDED_EXECUTION`

## Durable request classification

Only mutation-class operations use the durable request/idempotency ledger:

- `lane.open`
- `lane.renew`
- `lane.close`
- `fs.write_text`
- `fs.append_text`
- `fs.mkdir`
- `fs.move`
- `fs.replace_text`
- `fs.append_text`
- `fs.mkdir`
- `fs.move`
- `fs.replace_text`
- `process.exec`
- `process.start`
- `process.input`
- `process.terminate`

Ordinary `lane.list` probes and `fs.read_text` operations do not create durable request
rows. Reads remain subject to session, lane, fencing, root, and workstation read-size
authorization.

## Bounded mutation history

The initial V1 state-store policy retains admitted mutation IDs exactly and performs no
automatic eviction. Automatic deletion is forbidden because forgetting an admitted
mutation ID could allow a late retry to execute the mutation again.

The ledger has a fixed source-default capacity of 100,000 mutation request IDs and a
90-percent warning threshold. Existing IDs remain replayable at capacity. New mutation
IDs fail closed with `REQUEST_LEDGER_CAPACITY_EXCEEDED`.

This is intentionally availability-sacrificing before it is replay-safety-sacrificing.
A future compaction/expansion mechanism requires a separately reviewed migration that
preserves late-retry rejection.

Health states are:

- `HEALTHY`
- `DEGRADED_NEAR_CAPACITY`
- `FULL_FAIL_CLOSED`

`lane.list` reports the current request-ledger health without adding a durable request
row.

## Storage failure

Mutation-ledger admission failure occurs before mutation dispatch. A mutation cannot be
executed when its durable idempotency admission fails.

Read-only operations do not invoke mutation-ledger writes, so a ledger write failure does
not by itself convert a healthy read-only data plane into a failed application-auth
result.

## Claim ceiling

This profile is source behavior only.

It does not establish workstation installation, MCP server runtime, plugin registration,
current route, live read/write success, process execution, or RDC-equivalence boundary
PASS.
