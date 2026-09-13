# VeraMesh V1 Implementation Plan

Date: 2026-09-13
Design authority: `docs/superpowers/specs/2026-09-13-veramesh-v1-verarelay-0.4.0-mobile-0.2.0-design.md`
Execution posture: TDD, exact-head evidence, no merge/deploy/network/provider mutation without Patrick's exact authority.

## Goal

Ship the first independently conforming VeraMesh V1 path:

`VERA Mobile 0.2.0 -> VeraRelay 0.4.0 -> Vera Host Adapter 0.1.0`

with VeraRelay acting only as a blind durable courier, SQLite-backed transactional ledger, RFC 9421 request authentication, role/scope authorization, sealed HPKE envelopes, and append-only signed receipts.

## Task 1 — Freeze 0.3.0-0005 prototype evidence

Files:
- create `evidence/prototypes/verarelay-0.3.0-0005.json`
- later attach exact frozen source snapshot identity once a source commit exists

Steps:
1. Record artifact name, SHA-256, bytes, observed NAS start evidence, fresh test counts, and known defects.
2. Explicitly mark source provenance `UNPINNED_LOCAL_TREE`; do not invent a source commit.
3. Preserve 0005 as predecessor evidence only; no fixes are backported into it.
4. Verify the evidence file by readback on the PR head.

## Task 2 — Publish normative protocol objects

Files:
- create `protocol/v1/message-envelope.schema.json`
- create `protocol/v1/receipt.schema.json`
- create `protocol/v1/pairing.schema.json`
- create `protocol/v1/http-signature-profile.md`
- create `protocol/v1/authorization-matrix.json`

TDD/validation:
1. Add schema fixtures for valid and hostile-invalid examples before implementation consumers.
2. Assert sender/recipient/direction are role-derived, not caller-arbitrary.
3. Assert immutable message identity and receipt transition rules.
4. Add RFC 9421 fixed-vector fixtures including RSA/P-384 rejection under `ecdsa-p256-sha256`.

## Task 3 — Establish VeraRelay 0.4.0 source provenance

Target: local `C:\Vera\VeraRelay` successor source.

Steps:
1. Re-read the local tree before mutation.
2. Preserve the 0005 artifact and evidence unchanged.
3. Establish Git author identity only from existing authorized configuration; do not fabricate identity.
4. Create a 0.4.0 work branch/snapshot distinct from 0005 evidence.
5. Bind the exact source commit into the VeraMesh implementation registry after readback.

## Task 4 — SQLite transactional ledger

Expected VeraRelay source units:
- `src/db.js`
- `src/migrations.js`
- `src/repository.js`
- `tests/db.test.js`
- `tests/migrations.test.js`

RED first:
- WAL + `synchronous=FULL` + foreign keys are enforced.
- pairing consume + principal/key/scope creation + audit append is atomic.
- message acceptance + idempotency + custody/audit append is atomic.
- nonce consumption is atomic with the authorized operation.
- failed migration never opens writable service.

Then implement the minimum code to make each test green.

## Task 5 — RFC 9421 authentication + authorization

Expected source units:
- `src/http-signature.js`
- `src/authorization.js`
- `tests/http-signature.test.js`
- `tests/authorization.test.js`

RED first:
- valid P-256/P1363 request passes.
- RSA and P-384 keys labeled `ecdsa-p256-sha256` fail.
- stale/future/replayed nonce requests fail.
- body digest mismatch fails.
- phone cannot perform host-only operations or cross-recipient fetch.
- authentication without scope grants nothing.

## Task 6 — Immutable sealed mailbox + receipts

Expected source units:
- `src/mailbox.js`
- `src/receipts.js`
- `tests/mailbox.test.js`
- `tests/receipts.test.js`

RED first:
- accepted envelope is immutable.
- same sender/message ID/same envelope is idempotent.
- same sender/message ID/different envelope returns conflict.
- recipient receipt cannot regress or fabricate a prior state.
- relay custody, recipient storage, and optional processing remain distinct propositions/signers.
- sender/recipient stream sequence gaps are observable but do not deadlock later delivery.

## Task 7 — Relay API, structured health, recovery

Expected source units:
- update `src/server.js`
- create `src/health.js`
- create `src/quarantine.js`
- add API/failure-injection tests

RED first:
- audit/integrity failure blocks security-sensitive writes.
- corrupt records quarantine explicitly rather than poison fetch silently.
- core health and transport health are separable.
- queue/full/quota/auth errors map to intentional HTTP status classes, not blanket 500.
- ambiguous retry returns original custody result.

## Task 8 — Independent Vera Host Adapter 0.1.0

Implementation must consume only the published VeraMesh V1 contract/test vectors, not relay internals.

Minimum behavior:
- independent P-256 request signing/verification;
- Tink-compatible HPKE receive/send path;
- durable outbox/inbox;
- inner-envelope signature verification;
- signed recipient receipts;
- persisted relay audit-head checkpoints.

## Task 9 — Independent VERA Mobile 0.2.0

Starting evidence: existing 0.1.4 source has Keystore P-256 identity but no transport/pairing and no Android `INTERNET` permission.

RED/integration gates:
- add `INTERNET` permission.
- RFC 9421 P-256 signature encoding interoperates with Node vectors.
- Tink X25519 HPKE keyset is encrypted at rest under Android Keystore.
- pairing binds signing and HPKE identities.
- durable mobile outbox survives process death/restart.
- receipt/dedup behavior matches protocol vectors.

## Task 10 — Independent conformance + hostile acceptance

Create `conformance/` black-box vectors/harnesses in `vera-mesh`.

Required failures include wrong curve/type, replay, digest mismatch, role escalation, cross-recipient access, pairing double redemption, same-ID collision, lost-response retry, receipt replay/regression, ledger/audit corruption, quota/disk pressure, process death at transaction boundaries, upgrade with pending mail, and predecessor rollback.

At least one harness/client implementation must be produced independently from the published contract rather than importing VeraRelay internals.

## Task 11 — Build evidence, then package

Only after SOURCE + CONFORMANCE are green:
1. build VeraRelay `0.4.0` SPK;
2. hash and validate artifact;
3. bind exact commit/artifact hash in `implementation-registry.json`;
4. build/hash Mobile `0.2.0` APK and Host Adapter release artifact;
5. preserve states separately as SOURCE, BUILD, CONFORMANCE, DEVICE_TEST, DEPLOYED, END_TO_END_ACCEPTED.

## Task 12 — Deployment and real end-to-end acceptance

Protected boundary: do not install SPK, configure Tailscale, deploy, merge, or mutate provider/network state without Patrick's exact authority.

When authorized, verify actual DS216 Node `node:sqlite` capability, current Tailscale Serve/Funnel state, IPv4/IPv6 exposure, Android device behavior, bidirectional sealed-message exchange, ambiguous retry recovery, controlled restart persistence, upgrade, and rollback.

## Immediate execution frontier

Execute Task 1 now. Then move directly to Task 2 protocol schemas/vectors. Local implementation work resumes as soon as the authorized Lappy route is available; device unavailability is not a reason to stall repository-side protocol/conformance work.