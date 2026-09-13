# VeraMesh V1 independent hostile conformance lane

Status: `REVIEW_FIXTURES / NON_NORMATIVE / PARALLEL`

This directory is intentionally isolated from the normative `protocol/v1` objects being authored on the primary VeraMesh work branch. It exists to give the implementation lane black-box failure cases that can be applied without importing VeraRelay internals.

Base reviewed: `work/vera-mesh-foundation-20260913@3ee4cfc5951f7a1157851873d8fe1e7bea7626e1`.

The fixtures encode only behaviors already required by the approved design or by the hostile preimplementation review. Where Task 2 must still freeze an exact wire rule, the case is marked `requires_normative_closure: true` instead of silently inventing a rule.

## Evaluator contract

A conforming implementation should be testable as a black box. The evaluator supplies enrolled principals/keys or pairing inputs, sends one or more requests/messages, and observes protocol-visible results. No test requires direct imports from VeraRelay source.

Each case records:

- `id`: stable review-vector identifier;
- `domain`: subsystem under test;
- `preconditions`: externally visible setup;
- `stimulus`: hostile or ambiguous action;
- `expected`: required observable property;
- `forbidden`: outcomes that would violate the approved design;
- `requires_normative_closure`: whether Task 2 still needs to freeze an exact representation/detail before executable automation is possible.

## Priority

`P0` means a failure would break identity, authorization, custody, or end-to-end authenticity. `P1` means replay, idempotency, recovery, or interoperability would be unreliable. `P2` means the implementation could still function but independent clients could disagree at boundaries.

These vectors are review evidence, not release acceptance by themselves. Once the normative protocol freezes, equivalent machine-executable vectors should be promoted into the canonical `conformance/` suite with exact bytes and expected status/error codes.