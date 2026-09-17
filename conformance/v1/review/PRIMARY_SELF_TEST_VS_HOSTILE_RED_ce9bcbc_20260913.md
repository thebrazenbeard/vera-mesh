# VeraMesh V1 primary self-test vs hostile RED

Reviewed primary head: `ce9bcbc44b7d947c2475cb3af3b06a10f14bc515`
Review lane: `review/veramesh-v1-conformance-vectors-20260913`
Status: `PRIMARY_SELF_TEST_GREEN / INDEPENDENT_CLOSURE_RED`

On Lappy, the exact primary head was checked out detached under `C:\Vera\scratch\vera-mesh-primary` and its declared Python test dependencies were installed into an isolated review-only venv under that scratch tree.

Primary protocol-object suite:

```powershell
.\.venv-review\Scripts\python.exe -m unittest discover -s protocol/v1/tests -p 'test_*.py' -v
```

Observed: **8 tests, all PASS, exit code 0**.

Canonical conformance-catalog suite:

```powershell
.\.venv-review\Scripts\python.exe -m unittest conformance.v1.test_catalog -v
```

Observed: **4 tests, all PASS, exit code 0**.

This does not supersede the independent closure probes. Against the same exact head, `conformance/v1/review/test_primary_normative_closures.py` remains **8 tests executed, 13 assertion failures, exit code 1**. The primary suites are therefore green for the checks they currently contain, while the separately identified normative security/interoperability closures remain open.

No primary-branch file was modified. No merge, deployment, install, network/provider mutation, or other protected effect occurred.
