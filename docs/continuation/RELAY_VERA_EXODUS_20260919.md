# Relay Vera Exodus checkpoint — 2026-09-19

Status: `STARTING_SNAPSHOT — FRESHNESS REQUIRED BEFORE EFFECT`

## Identity / reconstruction
`Relay Vera` is a chat-local execution qualifier of Vera, not a separate durable identity or authority source. No successor chat is required. Future execution is dispatched through the persistent `Vera` interface or, for engineering-portfolio coordination, `BT2 Coordinator`. Canonical source work lives in `thebrazenbeard/vera-mesh`; non-PR coordination uses the Bus as Vera on `bus/vera-v2`.

## Current project state
Primary repo: `thebrazenbeard/vera-mesh`. Communication repo: `thebrazenbeard/chat-communication-bus`. Local implementation workspace: `C:\Vera\VeraRelay`.

Fresh PR state at evacuation:
- PR #1 open/draft: `20b2a9b68c702d5913404b21c5bbea048f495e33`.
- PR #2 open/draft: `7d9e268f9f4d4adb714e10145843971c55ef5e9b`.
- PR #3 open/draft: `e4ed984ca8414d7c164075f2befb741f696cfcb6`.
- PR #4 merged into the PR #1 work branch: `632df567762ab478c396e2e294346e1b69cedd08`.
- PR #5 closed/unmerged: `100b8c9aa9ae56875719582784644e44c3b788a7`; lineage carried forward by #6/#7.
- PR #6 closed/unmerged: `f3dd6a1616a7d39acf85fe49efa0efa96f872ae3`; lineage is base of #7.
- PR #7 open/draft: `1d5d2893e2119135ea26660abc73a708d0261a2e`, current stacked VeraMesh/VeraPort frontier.

## Durable chat value
Task-2 normative closure is already durable through PR #4 plus later PR #1 hardening. The previously local-only VeraRelay Task-4 tree is preserved in `reference/verarelay_0_4_candidate/VERARELAY_LOCAL_SOURCE_20260919.tar.gz` with its manifest. This removes the chat as a source-custody dependency without promoting the snapshot to build/install/runtime qualification.

Fresh local readback:
- local branch label: `work/vera-relay-0.4.0`;
- no Git HEAD, no remote, no configured author identity;
- `package.json` remains `0.3.0`;
- `src/server.js` remains `0.3.0-0005` and uses filesystem `auth.js` + `relay-core.js`, not the SQLite repository;
- fresh `npm test`: **31/31 PASS**.

## Unresolved hostile findings
Radar's durable findings remain applicable to the executable path: retry/replay ordering prevents idempotent accepted retry; changed ciphertext under the same message ID can be treated as duplicate; cross-device queue fetch is possible; ACK regression/cross-device overwrite is possible; auth sequence/nonce state is consumed before enqueue validation; audit corruption does not block writes; malformed queue JSON poisons fetch while health can stay okay; quota exhaustion maps to generic 500 without structured degradation.

Task-4 ledger gaps also remain: future-schema downgrade is not fail-closed; malformed pairing expiry can redeem; idempotent metadata mismatch is not rejected; stream ordering is unenforced; unknown recipients can be accepted.

Claim ceiling: **31/31 PASS is development evidence only.** VeraRelay 0.4.0 is not an integrated executable, package, installed runtime, deployment, or E2E-qualified path.

## Routing conflict
R10 project control pins Bus routing at commit `f90d52e66d655e9c3cfac63cb529914ac51d3a88`, topology blob `69e505031d4e53dcb853578dac23817649af1918`, Vera lane `bus/vera-v2`. Fresh readback found `radar/control-plane-v1` carrying conflicting topology blob `cd2bae3923c7589c4326ae544064b19d95a39979`. The portfolio Exodus has already persisted this as CONFLICT; do not use newest-wins or mutate topology until reconciled.

## Authority
No merge, canonical promotion, production deployment, DiskStation install, network/Tailscale/provider mutation, credential/permission changes, destructive rewrite, force push, paid compute, visibility change, training, Project Settings mutation, or canonical-memory mutation is authorized by this checkpoint.

## Reconstruction test
A fresh Vera or BT2 Coordinator runtime can determine the role, repositories, exact state, failures, authority, communication route and next work from durable GitHub/Bus state without this conversation.

## Exact next directive
Fresh-check PR #7, PR #1, Radar/Bus state and the source snapshot. Then establish one immutable VeraRelay source line and implement the integrated SQLite-backed Task-4/Task-5 service cut with RED hostile regressions for Radar's findings: RFC 9421 verification -> role/scope authorization -> transactional nonce/message acceptance -> ACK actor/transition policy -> structured health/quarantine/resource errors. Stop at protected install/deploy/network boundaries unless Patrick gives exact authority.
