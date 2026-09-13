# VeraMesh V1 protocol-object tests

The test module validates every fixture against the normative Draft 2020-12 schemas, checks the cross-field invariants that JSON Schema cannot express, and verifies the independent RFC 9421/P-256 vectors.

The test-only dependencies are `jsonschema` and `cryptography`:

```bash
python -m pip install -r protocol/v1/tests/requirements.txt
python -m unittest discover -s protocol/v1/tests -p 'test_*.py' -v
```

The private key material used by the fixed HTTP vector is intentionally test-only and is not a deployment credential.
