# Task 2 hostile review — exact head 25d5040

Reviewed source: `work/vera-mesh-foundation-20260913@25d5040c4898f6fe4bde8bf540fe9ed7b985ba2d`

Verdict: `FIX_BEFORE_TASK2_CLOSE`

This review is implementation pressure on the approved design, not a request to reopen architecture.

## P0 findings

### Pairing proof has no defined signed bytes

`pairing.schema.json` adds a nested P-256 `proof`, but the normative contract never defines the bytes that proof signs. The fixture named `valid-redeem.json` uses an all-zero 64-byte P1363 signature, and `test_protocol_objects.py` never verifies it cryptographically.

Closure: remove the redundant proof and rely on the RFC 9421 redemption request signature for proof-of-possession, or define one exact proof signature base and ship a real valid fixed vector.

### AAD and envelope hashes depend on test-local serialization

`test_protocol_objects.py::canonical_json()` sorts keys and removes whitespace, then uses those bytes for `aad_sha256` and `envelope_sha256`. No normative protocol object currently makes that byte rule authoritative.

Closure: freeze exact serialization bytes in the protocol and vectors. Independent Node/Java/Python implementations must not infer protocol law from one Python test helper.

### Receipt signatures have no normative signature base

`receipt.schema.json` carries P1363 signatures but does not define which exact receipt bytes are signed. Schema validation also cannot prove signer/recipient/key cross-field equality.

Closure: freeze exact signed receipt bytes and add hostile fixtures for signer principal mismatch, signature key mismatch, and relay signer/installation mismatch.

## P0/P1 findings

### Cross-relay request replay remains open

The HTTP signature profile covers method, path, digest, and content type, but no stable relay-installation/destination value. A credential enrolled at multiple relays can therefore produce a request that is structurally reusable at the same path when the other relay has not seen the nonce.

Closure: add a covered relay-installation binding or prove/freeze a stable covered authority component.

### Relay custody trust bootstrap remains open

Custody receipts identify a relay installation, but pairing/bootstrap does not yet normatively give endpoints the trusted custody public key and epoch for that installation.

Closure: bind and persist `relay_installation_id -> relay custody key + epoch` during trust establishment.

## P1 findings

Still open from the previous review: extra-algorithm `Content-Digest` policy, semantic custody-retry equivalence, exact inner-envelope signature bytes, and explicit relay GC use of verified relay-visible recipient-storage evidence. Freshness boundaries are now deterministic and can be considered closed.

`/v1/health` is currently unauthenticated in the authorization matrix. That differs from the approved role/status model and should either be narrowed to explicitly safe public fields or restored to role/scoped status access.

## P2 finding

Nonce generation can require at least 16 CSPRNG bytes, but a verifier cannot measure entropy from the received nonce. Verification should enforce decoded length/encoding, uniqueness, freshness, and binding; generation requirements belong to the signer profile.

## Positive evidence

The exact Task 2 head materially improves the protocol foundation: strict actual P-256 checks are explicit, freshness inequalities are deterministic, HTTP P1363 vectors include a real valid signature plus RSA/P-384 rejection, direction is role-derived, and the schemas separate custody/storage/processed propositions.
