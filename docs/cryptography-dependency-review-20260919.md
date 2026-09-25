# VeraPort cryptography dependency review — 2026-09-19

Status: REVIEWED / PROPOSED RANGE `cryptography>=42,<49`

## Prior boundary

The VeraPort reference package previously declared:

`cryptography>=42,<47`

No source-local rationale or API dependency was found that required excluding the 47.x or 48.x release lines.

## VeraPort usage surface

The current source uses these `cryptography` families:

- P-256 / `SECP256R1`;
- ECDSA with SHA-256;
- DER SPKI public-key serialization;
- PEM private/public-key serialization and loading;
- X.509 certificate construction/signing in tests;
- P1363 conversion through `decode_dss_signature` / `encode_dss_signature`.

It does not depend on the legacy symmetric algorithms or binary elliptic curves affected by the 47/48 transition notes.

## Upstream review

Cryptography 47.0.0 removed binary elliptic curves and OpenSSL 1.1.x support, tightened malformed-key handling, and changed some exception types for unsupported keys.

Cryptography 48.0.0 removed Python 3.8 support and introduced additional algorithms. Those changes do not conflict with VeraPort's Python >=3.11 / P-256 / PEM / X.509 usage.

The 49 development line contains additional removals. VeraPort therefore keeps an explicit upper boundary and requires a fresh review before admitting 49.x.

## Execution evidence

On 2026-09-19, exact VeraPort executable subject:

- head `fe6245fc9bf6392355bb9f13a88e010476ebdb89`
- tree `91d123408201b80a8d6edd7c4fbc20824fc3b330`

completed its whole-chain Lappy test under `cryptography 48.0.1`.

That prior result was outside the old declared range and therefore carried a qualification caveat. Widening the declared supported line to `<49` resolves that specific mismatch after fresh source review; the new dependency-boundary source head still requires readback/testing before qualification is promoted.

## Test dependency

The `test` optional dependency now explicitly declares pytest and pytest-asyncio so the async acceptance suite has a reproducible declared test environment. Runtime installation does not require those packages.

## Decision

Use:

`cryptography>=42,<49`

Do not admit 49.x without a new upstream/API review and exact-candidate tests.
