# VeraMesh V1 independent hostile conformance lane

Status: `REVIEW_FIXTURES / NON_NORMATIVE / PARALLEL`

This directory is intentionally isolated from the normative `protocol/v1` objects being authored on the primary VeraMesh work branch. It exists to give the implementation lane black-box failure cases that can be applied without importing VeraRelay internals.

Base reviewed: `work/vera-mesh-foundation-20260913@3ee4cfc5951f7a1157851873d8fe1e7bea7626e1`.

The fixtures encode only behaviors already required by the approved design or by the hostile preimplementation review. Where Task 2 must still freeze an exact wire rule, the case is marked `requires_normative_closure: true` instead of silently inventing a rule.

The review lane currently contains 36 hostile/recovery cases: 21 primary cases plus 15 coverage-gap cases. Seven primary cases deliberately remain dependent on exact Task 2 normative closure.

## Evaluator contract

A conforming implementation should be testable as a black box. The evaluator supplies enrolled principals/keys or pairing inputs, sends one or more requests/messages, and observes protocol-visible results. No test requires direct imports from VeraRelay source.

Each case records a stable ID, domain, externally visible setup/stimulus, required observable properties, forbidden outcomes, and—where applicable—whether exact normative closure is still required.

## Priority

`P0` means a failure would break identity, authorization, custody, or end-to-end authenticity. `P1` means replay, idempotency, recovery, or interoperability would be unreliable. `P2` means the implementation could still function but independent clients could disagree at boundaries.

`TRACEABILITY.md` maps approved V1 obligations to review vectors. `normative-closure-checklist.md` isolates the seven exact Task 2 decisions that must be frozen before exact-byte interoperability vectors can be canonicalized.

These vectors are review evidence, not release acceptance by themselves. Once the normative protocol freezes, equivalent machine-executable vectors should be promoted into the canonical `conformance/` suite with exact bytes and expected status/error codes.
