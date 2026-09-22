# VeraMesh Parallel Coordination — 2026-09-21 V1

Status: DRAFT / NON-CANONICAL / COORDINATION CHECKPOINT

This checkpoint binds the current VeraMesh coordination frontier without performing any merge, deployment, credential/permission mutation, service restart, provider/product mutation, or other protected effect.

## Exact source frontier

- canonical repository: `thebrazenbeard/vera-mesh`
- canonical `main`: `d3bbaa247797dce5f6a26e7006206f37cfef66fe`
- current implementation successor: PR #12
- PR #12 branch: `vera/veraport-chatgpt-mcp-v2-20260920`
- PR #12 exact live head: `9bffc57930587bf74a12657bbeaa913474ab5574`
- PR #12 remains stacked on PR #11; the repository is intentionally not represented as integrated on `main`.
- current exact-head disposition: `CHANGES_REQUIRED` for inherited wire-bound and application-session lifecycle defects.

PR #12's prose still contains earlier successor-head provenance. Exact-head authority belongs to the current Git ref above.

## Parallel ownership

### VeraMesh source lane

PR #12 R4 materially closes Issue #17's two immediate source defects:
- `max_inflight` capacity is acquired before another request frame is read and before handler-task creation;
- ordinary `lane.list` / `fs.read_text` requests no longer enter the durable mutation ledger;
- mutation IDs remain fail-closed under a fixed 100,000-record capacity with no automatic forgetting.

These R4 changes still require independent exact-head execution and hostile review. Older 92-PASS evidence does not transfer to `9bffc579...`.

Two inherited source blockers remain before machine acceptance:

1. Wire-safe reads / Issue #14 capability frontier:
   - `LocalExecutor.max_read_bytes` and stream `max_frame_bytes` both default to 1 MiB;
   - JSON wrapping plus `ensure_ascii=True` can expand a locally valid file beyond the response-frame ceiling;
   - response-side `write_frame()` failure is not converted into a correlated bounded application response;
   - source needs an end-to-end response-size contract or ranged/chunked reads plus hostile boundary/escaping/overflow tests.

2. Application-session lifecycle / Issue #19:
   - session end currently does not reap exact-session lanes;
   - lane TTL is not clamped to remaining authenticated session lifetime;
   - admission-time expiry does not itself reconcile already-admitted operations;
   - disconnect/expiry must drain under bounded deadlines and reap only the exact session namespace without touching other sessions.

No live state-database cleanup or runtime mutation is implied by source work.

### Protocol foundation lane

PR #1 remains at exact head `20b2a9b68c702d5913404b21c5bbea048f495e33`.

Task 2 normative closures remain accepted at that subject, but three executable cross-field negatives remain assigned for isolated Task 3 closure:
- recipient receipt `signer.principal != recipient_principal`;
- recipient receipt `signature.key_id != signer.key_id`;
- outer message field, especially `recipient_principal`, mismatching the corresponding `aad_binding`.

This work must remain isolated from the VeraPort/MCP successor line.

### VCP control-plane lane

VCP remains the control/governance owner for these independent subjects:

- VCP PR #91 @ `7466bf27d6ebdc24e121e65d1d163cd2e7905844`: MCP authority, route-currentness, and RDC-equivalence gates.
- VCP PR #96 @ `39ebc98656ac3f95bc78e3913609d15b53d25fb6`: VeraRelay lifecycle/currentness contract for VeraMesh Issue #13.
- VCP PR #99 @ `08637d04cba2914c4058ee7d5094fff87d23eadf`: controller-key recovery/safe-rotation gate for VeraMesh Issue #15.

VeraMesh source must consume these as governance/control constraints rather than duplicate or silently widen them.

### Review / execution lanes

Durable Bus coordination is active from `bus/veramesh-coordinator-v1`.

Current assignments:
- BT2 Coordinator: independently execute PR #12 exact head `9bffc579...` with full VeraPort suite, focused R4 admission/ledger tests, compile gate, and exact working-tree evidence.
- VCP: independently hostile-review PR #12 R4 admission/backpressure/idempotency/ledger semantics at exact head `9bffc579...`.
- Vera: implement the three PR #1 Task 3 cross-field negative closures on an isolated successor.

No exact-head PASS is inferred until the assigned return is durably bound and the reviewed head still matches.

### Protected-effect gates

Issue #15 remains blocked on controller private-key custody or separately authorized safe rotation. No key generation, enrollment, trust mutation, ACL change, restart, or predecessor retirement is authorized here.

Issue #16 remains a ChatGPT product/workspace gate. Local MCP server readiness, Secure MCP Tunnel readiness, app registration, workspace association, and tool invocation are distinct states. No tunnel, app, developer-mode, plan, workspace, API-key, or provider mutation is authorized here.

## Route-health lane

PR #9 @ `34d581a9ba601c5f674f4d55e6e2557aa36c8b63` remains an independent route-health/convergence contract stacked on the foundation. It establishes that handshake success and last-known-good do not establish current data-plane health. It remains logically separate from PR #12 until an explicit composition is reviewed.

## Historical/source custody

- PR #1 @ `20b2a9b68c702d5913404b21c5bbea048f495e33`: architecture/foundation.
- PR #7 closed unmerged @ `1d5d2893e2119135ea26660abc73a708d0261a2e`: VeraPort workstation bridge source provenance.
- PR #8 @ `17065d427577b909c6dc6c66e778a2bf25881b6f`: Relay Vera recovery/source custody.
- PR #11 @ `76510806a6bd832b5d0f1cdd7bc9439e068c9d03`: persistent ChatGPT MCP controller predecessor.
- PR #12 @ `9bffc57930587bf74a12657bbeaa913474ab5574`: current controller repair successor.
- PR #18: this non-canonical coordination checkpoint.
- Issue #19: application-session teardown/lane-lifecycle source blocker.

Closed/unmerged predecessors remain provenance and are not reopened merely to make the graph cosmetically linear.

## Coordination decision

NOW:
1. Keep PR #12 at `CHANGES_REQUIRED` until wire-bound read semantics and Issue #19 session lifecycle are closed.
2. Continue independent exact-head R4 execution and hostile review so accepted R4 ledger/admission work is preserved.
3. Continue PR #1 Task 3 cross-field negative closure independently.
4. Preserve PR #9 as a parallel routing contract.
5. Consume VCP #91/#96/#99 as control constraints.
6. Keep Issues #15/#16 behind explicit protected-effect gates.

NOT NOW:
- machine/live-auth acceptance from PR #12;
- merge to `main`;
- controller rotation or credential generation;
- service restart/deployment;
- Secure MCP Tunnel or ChatGPT app registration;
- process enablement;
- live write qualification;
- branch deletion/history rewrite.

This checkpoint is an execution-coordination artifact, not a qualification receipt or authorization expansion.


## Execution update — Issue #17 successor

Source-only repair is now staged as VeraMesh Draft PR #20:

- base: PR #12 exact head `9bffc57930587bf74a12657bbeaa913474ab5574`
- head: `5f1ed8d61fd0faf05bc5833d70b8a98ac9875691`
- branch: `work/veraport-request-ledger-health-v2-20260921`
- compare: ahead 3 / behind 0
- changed files: exactly mutation-ledger profile, request-ledger hostile tests, and state-store implementation
- PR state: OPEN / DRAFT / MERGEABLE at readback
- hosted workflow evidence: none observed for exact head

The existing PR #12 head already removed read-only requests from durable mutation journaling and introduced bounded fail-closed mutation capacity. PR #20 adds the remaining health/degradation visibility: storage bytes, pending/oldest ages, write-admission state, degraded reason, and read-only survival when ledger-health inspection is unavailable.

Claim ceiling remains source/static-readback only until executable qualification occurs.
