# VeraMesh V1 hostile preimplementation review

Date: 2026-09-13
Review base: `work/vera-mesh-foundation-20260913@3ee4cfc5951f7a1157851873d8fe1e7bea7626e1`
Purpose: catch implementation contradictions without reopening the approved architecture.

## Verdict

`PROCEED_WITH_TARGETED_CLOSURES`

The approved architecture is implementable. I found no reason to stop the 0.4.0 / Host Adapter / Mobile 0.2.0 cut. I did find several protocol details that should be made explicit in Task 2 so independently written clients do not accidentally implement different systems.

## 1. HIGH — recipient receipt visibility versus relay garbage collection

The design says application payloads are opaque sealed envelopes, recipient storage receipts are endpoint-signed protocol messages, and the relay may garbage-collect only after a valid recipient-storage receipt exists.

Those three statements require a precise wire distinction. If `recipient_received` is itself only inside an HPKE-sealed payload, VeraRelay cannot inspect or verify it and therefore cannot safely use it as its garbage-collection predicate.

Closure required: define recipient-storage receipts as relay-visible signed protocol evidence containing only non-secret custody metadata (message id/hash, recipient principal/key epoch, receipt type/time, signature). Application plaintext remains sealed. If a private end-to-end copy of the receipt is also wanted, that is separate from the relay-visible custody evidence.

## 2. HIGH — relay custody signature needs a trust bootstrap

A relay-signed acceptance receipt is meaningful only if endpoints already know which relay custody public key belongs to the relay installation they trust.

Closure required: pairing/bootstrap must bind `relay_installation_id -> relay_custody_public_key` (and key epoch) into endpoint state. A custody signature from an unpinned key proves only that some key signed the receipt.

Rotation/replacement must preserve the distinction between a rotated custody key for the same installation and a replacement relay installation with discontinuous identity.

## 3. HIGH — freeze end-to-end signing bytes; do not sign parsed JSON

The design correctly rejected bespoke HTTP canonicalization by adopting RFC 9421, but the inner end-to-end message signature still needs byte-level semantics.

Do not define the inner signature as `sign(JSON object)` and rely on two languages to reserialize identically. The sender should serialize the unsigned inner envelope once, sign those exact UTF-8 bytes, and carry those exact bytes unchanged inside the HPKE plaintext wrapper. The recipient verifies the signature over the recovered bytes before parsing them as JSON.

This avoids inventing a second canonical-JSON protocol and keeps Java/Python interoperability testable with fixed byte vectors.

## 4. HIGH — request signatures need destination/relay binding

The currently approved minimum HTTP coverage is `@method` + `@path`, plus `content-digest` and `content-type` for bodies. RFC 9421 permits application-specific stronger coverage.

If the same endpoint signing key is ever enrolled at two relay installations, the same signed request could otherwise be valid at the same path on both relays when their nonce stores are independent.

Closure required: bind every protected request to the intended relay installation. Prefer a protocol field/header such as a relay-installation identifier that is itself covered by the HTTP signature if reverse-proxy authority rewriting makes `@authority` unstable. If actual Tailscale/HTTP behavior proves `@authority` stable end to end, covering `@authority` is also sufficient.

Do not leave cross-relay replay prevention implicit in deployment assumptions.

## 5. MEDIUM — exact freshness rule must be deterministic

`five-minute freshness window` is semantically clear but not yet enough for independent implementations.

Freeze exact verifier rules for `created`, `expires`, clock skew, inclusivity at boundaries, maximum lifetime, and whether `expires` must equal or merely be no later than `created + 300`.

RFC 9421 deliberately leaves application policy here; interoperability therefore depends on VeraMesh defining it.

## 6. MEDIUM — custody receipt idempotency is semantic, not necessarily byte-identical

RFC 9421 P-256 ECDSA signatures are nondeterministic and encoded as fixed 64-byte `r || s`. A relay that committed a message, crashed before persisting/transmitting the custody signature, and later receives an idempotent retry may create a different valid signature over the same receipt claims.

Closure required: either persist the exact signed custody receipt as part of acceptance or define retry equivalence by immutable receipt claims rather than identical signature bytes. Do not test idempotency by byte-for-byte signature equality.

## 7. MEDIUM — narrow `Content-Digest` acceptance policy

RFC 9530 allows multiple digest dictionary members. VeraMesh says SHA-256 only.

Freeze whether VeraMesh rejects any `Content-Digest` containing additional algorithms or accepts a valid `sha-256` member while ignoring extras. A strict single-member policy is simpler and reduces parser variation; whichever rule is chosen should be a hostile vector.

## Confirmed standard assumptions

- RFC 9421 defines `ecdsa-p256-sha256` and requires the HTTP signature value to be exactly 64 bytes: zero-padded 32-byte `r` followed by 32-byte `s`. Android DER output therefore needs provider P1363 support or a representation-only DER-to-P1363 adapter.
- RFC 9421 `@path` excludes the query string. The current V1 rule avoiding semantically meaningful protected query strings is therefore coherent.
- RFC 9421 explicitly expects applications to enforce nonce uniqueness and application-specific signature requirements.
- RFC 9530 `Content-Digest` is over actual message content and the verifier must recompute the digest over received bytes.
- RFC 9180 provides X25519/HKDF-SHA256 as a standard HPKE KEM; replay, ordering, and application identity remain VeraMesh responsibilities.

## Recommendation to the implementation lane

Do not redesign VeraMesh. Close items 1-7 directly in the Task 2 normative protocol objects and hostile vectors, then keep building. These are specification precision points revealed by implementation pressure, not a new architecture phase.
