# PR #1 exact-head follow-up review

Reviewed head: `ce9bcbc44b7d947c2475cb3af3b06a10f14bc515`
Review posture: independent hostile/conformance lane
Verdict: `TASK2_CONFORMANCE_CATALOG_ADDED / PRIOR_FIX_BEFORE_TASK2_CLOSE_FINDINGS_STILL_OPEN`

The new canonical conformance catalog adds 24 black-box cases, but it does not close the previously identified protocol blockers.

Open exact-head findings:

1. No required canonical case for cross-relay replay / signed relay-installation binding.
2. No required canonical case for exact inner-envelope signature bytes and anti-reserialization behavior.
3. No required canonical case for endpoint trust bootstrap of `relay_installation_id -> custody key + epoch`.
4. No required canonical case for the strict RFC 9530 `Content-Digest` extra-member policy.
5. Canonical cases reference `valid-redeem.json` and valid receipt fixtures whose ECDSA signature bytes are still all-zero placeholders; those fixtures are schema-valid only, not cryptographically valid.
6. `conformance/v1/test_catalog.py` proves catalog shape/reference existence, not runtime conformance. No black-box adapter executes the cases yet.
7. `runner-contract.md` exposes `pair(role)`; implementations must preserve that role/scopes come from local-admin-created pairing state rather than remote caller authority.

Good new evidence:

- canonical black-box runner/result semantics are now explicit;
- catalog cases distinguish UNRUN/BLOCKED/PASS and forbid promoting schema/unit success into runtime conformance;
- failure-injection and upgrade/recovery cases are represented as future target tests.

Closure condition:

Repair the normative protocol and cryptographic fixtures, promote the omitted blockers into the canonical catalog, then make the non-device cases executable against a real target adapter before calling Task 2 conformance-complete.
