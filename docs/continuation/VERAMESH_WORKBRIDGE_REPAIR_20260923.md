# VeraMesh / WorkBridgeMCP repair continuation — 2026-09-23

This is a resumable engineering record, not proof of an installed route. Fresh-check every ref, check, and runtime surface before acting on it.

## Exact source subjects

- VeraMesh base: draft PR #32, `work/veramesh-workbridge-current-v2-20260922` at `ca7a933bf6fa6e054244e415c59904c5d7c734d2`.
- VeraMesh repair branch: `fix/veramesh-workbridge-20260923`.
- WorkBridge base: PR #5 at `e89a0b43717a4c9a4aabfd1d7e1c967a375f65c4`.
- WorkBridge repair: draft PR #6, `fix/workbridge-veramesh-20260923` at `db56871f74f641a0819a601fe166e3241352edaf`; code-bearing native-stderr repair `d9e8881ca6ffa17ea5b98a6af8c0ef2b2d171f15`.

## Source repair and evidence

- VeraMesh now binds its private WorkBridge bearer token to the exact configured MCP endpoint and rejects redirects. A regression proves a redirected destination cannot receive the credential.
- VeraMesh's real-binary integration test starts a WorkBridge process on authenticated loopback HTTP, then exercises read, stat, and list through the actual VeraMesh adapter; write remains unavailable in a read-only profile.
- The VeraMesh CI integration job builds exact WorkBridge code commit `d9e8881ca6ffa17ea5b98a6af8c0ef2b2d171f15`; it does not use an unbound moving branch. WorkBridge head `db56871f74f641a0819a601fe166e3241352edaf` adds the source pin/documentation around that code-bearing repair.
- Local Windows Go 1.25.12 focused WorkBridge tests and full gateway tests/vet/build: PASS. The full gateway test suite against a real WorkBridge binary built from exact head `ab5ca2d` passed locally. That binary's SHA-256 was `3853be9cb3f0af1ad833e29e1eb022906a0979e3531534147573ae8daccfc62a`.
- WorkBridge exact-head CI run `35846554913` at `ab5ca2d`: Ubuntu tests PASS, Windows tests PASS, Windows binary build PASS. Local stdio MCP initialize/tools-list and authenticated HTTP smoke also passed; unauthenticated HTTP health returned 401.
- The GitHub security-agent jobs for both repositories failed because their requested model was unsupported. Their failure is neither a source test failure nor an independent review pass.

## Source acceptance and remaining gates

- Before this record update, VeraMesh draft PR #33 head `5bc6c08b0a6f6ac796852cb0069e2a2d8ee0b42a` passed CI run `35847032209` (including real WorkBridge HTTP integration), reference run `35847032184`, and CodeQL run `35847032106`. Fresh-check checks after this documentation and source-pin update.
- The separate GitHub security-agent job failed before review because its requested model was unsupported. It did not perform independent security review.
- Lappy is reachable through the verified self-hosted runner `LAPPY-vera-blender`. Live execution reproduced the predecessor runner defect: Windows PowerShell 5.1 with strict error handling converted native WorkBridge stderr into `NativeCommandError`, terminating the scheduled-task wrapper. WorkBridge PR #6 now contains the source repair; persistent Lappy runtime must be requalified after fresh CI.
- Merge, installation, credentials, provider/network changes, and ChatGPT registration remain separate protected effects. No such effect is claimed here.

## Recovery after a rate or context limit

1. Read PR #33 and WorkBridge PR #6 descriptions, then verify remote heads and checks. The latest PR descriptions can hold later readbacks than this source checkpoint.
2. Confirm the VeraMesh workflow still pins the intended exact WorkBridge repair head. If WorkBridge moves, assess the new commit and update this pin with corresponding CI evidence.
3. Check Lappy connectivity before any read-only runtime qualification. Keep source, package, install, runtime, and ChatGPT effect states separate.
4. Resume authorized, non-colliding work only; do not infer merge or installation authority from a passing check.
