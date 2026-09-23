# VeraMesh / WorkBridgeMCP repair continuation — 2026-09-23

This is a resumable engineering record, not proof of an installed route. Fresh-check every ref, check, and runtime surface before acting on it.

## Exact source subjects

- VeraMesh base: draft PR #32, `work/veramesh-workbridge-current-v2-20260922` at `ca7a933bf6fa6e054244e415c59904c5d7c734d2`.
- VeraMesh repair branch: `fix/veramesh-workbridge-20260923`.
- WorkBridge base: PR #5 at `e89a0b43717a4c9a4aabfd1d7e1c967a375f65c4`.
- WorkBridge repair: draft PR #6, `fix/workbridge-veramesh-20260923` at `ab5ca2dd263bdb35e9fcecb297bf094e3a43017e`; code-bearing ancestor `613c3df0e7bd1d43b123d249e3aaca4366852546`.

## Source repair and evidence

- VeraMesh now binds its private WorkBridge bearer token to the exact configured MCP endpoint and rejects redirects. A regression proves a redirected destination cannot receive the credential.
- VeraMesh's real-binary integration test starts a WorkBridge process on authenticated loopback HTTP, then exercises read, stat, and list through the actual VeraMesh adapter; write remains unavailable in a read-only profile.
- The VeraMesh CI integration job builds the exact WorkBridge repair commit above. It does not use an unbound moving branch.
- Local Windows Go 1.25.12 focused WorkBridge tests and full gateway tests/vet/build: PASS. The full gateway test suite against a real WorkBridge binary built from exact head `ab5ca2d` passed locally. That binary's SHA-256 was `3853be9cb3f0af1ad833e29e1eb022906a0979e3531534147573ae8daccfc62a`.
- WorkBridge exact-head CI run `35846554913` at `ab5ca2d`: Ubuntu tests PASS, Windows tests PASS, Windows binary build PASS. Local stdio MCP initialize/tools-list and authenticated HTTP smoke also passed; unauthenticated HTTP health returned 401.
- The GitHub security-agent jobs for both repositories failed because their requested model was unsupported. Their failure is neither a source test failure nor an independent review pass.

## Remaining gates

- Commit/push/read back this VeraMesh repair branch; create a reviewable draft PR and verify exact-head CI, including the real-binary integration job.
- Fresh-check Lappy availability. The available Remote Desktop Commander connection reported Lappy offline, so there is no current Lappy install/runtime/ChatGPT tool-effect readback.
- Merge, install, credentials, provider, network, and ChatGPT registration are separate protected effects. No such effect is claimed here.

The next engineer should start with remote heads and check runs, not with the cached hashes in this file.
