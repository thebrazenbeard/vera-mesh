# VeraMesh Parallel Coordination — 2026-09-21 V1

Status: DRAFT / NON-CANONICAL / COORDINATION CHECKPOINT

This checkpoint binds the current VeraMesh coordination frontier without performing any merge, deployment, credential/permission mutation, service restart, provider/product mutation, or other protected effect.

## Exact source frontier

- canonical repository: `thebrazenbeard/vera-mesh`
- canonical `main`: `d3bbaa247797dce5f6a26e7006206f37cfef66fe`
- current implementation successor: PR #12
- PR #12 branch: `vera/veraport-chatgpt-mcp-v2-20260920`
- PR #12 exact live head observed for this checkpoint: `9bffc57930587bf74a12657bbeaa913474ab5574`
- PR #12 remains stacked on PR #11; the repository is intentionally not represented as integrated on `main`.

PR #12's prose still names an earlier successor head `87f9be76d3fff35ba1bae7674d4af8957153ac6a`. That is provenance inside the PR body, not current head authority. Current head is the Git ref above.

## Parallel ownership

### VeraMesh source lane

Owns source-level continuation from PR #12.

Immediate runnable frontier: Issue #17, request-ledger growth and read-only persistence.

Required direction:
- read-only `lane.list`, `fs.read_text`, and route/data-plane probes must not create permanent durable idempotency rows absent an explicit bounded read-ledger requirement;
- mutating operations retain durable replay/idempotency protection;
- retention/compaction must not reopen old mutation IDs as new effects;
- PENDING/ambiguous mutation evidence must remain fail-closed;
- health must expose ledger/storage degradation before route failure.

No live state-database cleanup or runtime mutation is implied by source work.

### VCP control-plane lane

VCP remains the control/governance owner for these independent subjects:

- VCP PR #91 @ `7466bf27d6ebdc24e121e65d1d163cd2e7905844`: MCP authority, route-currentness, and RDC-equivalence gates.
- VCP PR #96 @ `39ebc98656ac3f95bc78e3913609d15b53d25fb6`: VeraRelay lifecycle/currentness contract for VeraMesh Issue #13.
- VCP PR #99 @ `08637d04cba2914c4058ee7d5094fff87d23eadf`: controller-key recovery/safe-rotation gate for VeraMesh Issue #15.

VeraMesh source must consume these as governance/control constraints rather than duplicate or silently widen them.

### Protected-effect gates

Issue #15 remains blocked on controller private-key custody or separately authorized safe rotation. No key generation, enrollment, trust mutation, ACL change, restart, or predecessor retirement is authorized here.

Issue #16 remains a ChatGPT product/workspace gate. Local MCP server readiness, Secure MCP Tunnel readiness, app registration, workspace association, and tool invocation are distinct states. No tunnel, app, developer-mode, plan, workspace, API-key, or provider mutation is authorized here.

## Route-health lane

PR #9 @ `34d581a9ba601c5f674f4d55e6e2557aa36c8b63` remains an independent route-health/convergence contract stacked on the foundation. It establishes that handshake success and last-known-good do not establish current data-plane health. It should remain logically separate from the PR #12 controller implementation until an explicit composition is reviewed.

## Historical/source custody

- PR #1 @ `20b2a9b68c702d5913404b21c5bbea048f495e33`: architecture/foundation.
- PR #7 closed unmerged @ `1d5d2893e2119135ea26660abc73a708d0261a2e`: VeraPort workstation bridge source provenance.
- PR #8 @ `17065d427577b909c6dc6c66e778a2bf25881b6f`: Relay Vera recovery/source custody.
- PR #11 @ `76510806a6bd832b5d0f1cdd7bc9439e068c9d03`: persistent ChatGPT MCP controller predecessor.
- PR #12 @ `9bffc57930587bf74a12657bbeaa913474ab5574`: current controller repair successor.

Closed/unmerged predecessors remain provenance and are not reopened merely to make the graph cosmetically linear.

## Coordination decision

NOW:
1. Continue source-only engineering on Issue #17 from PR #12 exact head.
2. Preserve PR #9 as a parallel routing contract.
3. Consume VCP #91/#96/#99 as control constraints.
4. Keep Issues #15/#16 behind their explicit protected-effect gates.

NOT NOW:
- merge to `main`;
- controller rotation or credential generation;
- service restart/deployment;
- Secure MCP Tunnel or ChatGPT app registration;
- process enablement;
- live write qualification;
- branch deletion/history rewrite.

This checkpoint is an execution-coordination artifact, not a qualification receipt or authorization expansion.
