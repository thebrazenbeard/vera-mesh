# VeraMesh V1 executable normative-closure RED evidence

Reviewed primary head: `ce9bcbc44b7d947c2475cb3af3b06a10f14bc515`
Review lane: `review/veramesh-v1-conformance-vectors-20260913`
Test: `conformance/v1/review/test_primary_normative_closures.py`
Status: `RED / EXPECTED_FAILURE / PRIMARY_NOT_CLOSED`

The independent test was run against a detached local checkout of the exact primary head above with:

```powershell
$env:VERAMESH_PRIMARY_TREE='C:\Vera\scratch\vera-mesh-primary'
python -m unittest conformance.v1.review.test_primary_normative_closures -v
```

Observed result: **8 tests executed, 13 assertion failures, exit code 1**. The failures are the intended RED proof that the checks actually detect the currently open protocol defects rather than passing vacuously.

Detected failures:

- protected RFC 9421 mailbox signatures do not bind the request to a relay/destination identity;
- the RFC 9530 profile does not define accept/reject behavior for additional `Content-Digest` members;
- the normative protocol does not forbid inner-signature verification after JSON parse/reserialization;
- pairing does not establish relay installation / custody key / key-epoch trust bootstrap;
- pairing proof has no normative bytes-to-sign/signature-base contract;
- delivery receipts have no normative bytes-to-sign/signature-base contract;
- the runner's `pair(role)` adapter does not explicitly state that `role` is local-admin/test-harness setup rather than remote caller authority;
- `valid-redeem.json`, `valid-recipient-storage.json`, `valid-recipient-processed.json`, and `valid-relay-custody.json` contain all-zero signature bytes.

No primary-branch file was modified by this run. No merge, deployment, install, network/provider mutation, or other protected effect occurred.
