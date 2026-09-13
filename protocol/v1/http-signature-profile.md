# VeraMesh V1 HTTP Message Signature Profile

Status: normative protocol object for VeraMesh V1.

This profile fixes the RFC 9421 choices needed for independent implementations. It does not replace RFC 9421 parsing or structured-field processing with an ad-hoc signature format.

## Required profile

- Signature label: `vera`.
- Accepted algorithm: `ecdsa-p256-sha256` only.
- Signature representation: IEEE P1363 raw `r || s`, exactly 64 bytes, base64-encoded in the `Signature` field. DER is rejected.
- Public key: an EC P-256 public key in DER SubjectPublicKeyInfo (SPKI) form. The verifier parses the key and checks both the EC key type and the P-256 curve OID; the `alg` label is not trusted as evidence of either.
- `keyid`: lowercase hexadecimal SHA-256 of the exact SPKI bytes, 64 characters. It identifies an enrolled active signing key.
- `tag`: `veramesh-v1`.
- Body digest: RFC 9530 `Content-Digest` with SHA-256 over the exact received body bytes. The digest is covered by the signature for body-bearing requests.
- Freshness: `created` and `expires` are Unix seconds. A verifier accepts only when `expires > created`, `expires - created <= 300`, `created <= now + 30`, and `expires >= now`; the 30-second future allowance is clock-skew tolerance, not an extension of the five-minute signed lifetime.
- `nonce`: base64url without padding, at least 128 bits of entropy. A nonce is single-use for the signing principal and is consumed transactionally with the authorized operation.
- Protected V1 endpoints do not use semantically meaningful query strings. An implementation rejects a protected request with a query rather than silently dropping it from the authorization decision.

Every protected request requires `created`, `expires`, `nonce`, `keyid`, `alg`, and `tag`. A request signature authenticates the deposit to VeraRelay; it does not prove endpoint-to-endpoint authorship of the sealed ciphertext. That proof is the inner envelope signature.

## Covered components

For `POST /v1/mailbox/envelopes` the covered component list is exactly:

```text
("@method" "@path" "content-digest" "content-type")
```

For a body-bearing protected endpoint, `content-digest` and `content-type` are required. For a bodyless protected endpoint, the implementation-specific profile for that endpoint may omit the body fields, but V1 mailbox operations do not.

`@path` is the path component only. The method is the uppercase method value. Header field names are case-insensitive, but the covered values are reconstructed from the received message under RFC 9421 rules.

The corresponding header shape is:

```http
Content-Digest: sha-256=:<base64-sha256-of-exact-body>:
Signature-Input: vera=("@method" "@path" "content-digest" "content-type");created=<unix-seconds>;expires=<unix-seconds>;nonce="<base64url-nonce>";keyid="<sha256-spki-hex>";alg="ecdsa-p256-sha256";tag="veramesh-v1"
Signature: vera=:<base64-64-byte-p1363-signature>:
```

The signature base is the RFC 9421 derived component sequence, with one line per covered component followed by `@signature-params`:

```text
"@method": POST
"@path": /v1/mailbox/envelopes
"content-digest": sha-256=:<base64-sha256-of-exact-body>:
"content-type": application/json
"@signature-params": ("@method" "@path" "content-digest" "content-type");created=<unix-seconds>;expires=<unix-seconds>;nonce="<base64url-nonce>";keyid="<sha256-spki-hex>";alg="ecdsa-p256-sha256";tag="veramesh-v1"
```

The exact fixed serialization, including structured-field quoting, is tested by `vectors/http-signature-vectors.json`. Implementations must not sort, lowercase, or otherwise normalize the signature-base lines beyond RFC 9421 processing.

## Verification order and failure semantics

An implementation MUST fail closed in this order, without consuming a nonce for a request that has not authenticated:

1. Parse `Signature` and `Signature-Input` as RFC 9421 structured fields and require exactly one `vera` signature.
2. Require the profile parameters and exact covered-component set.
3. Recompute `Content-Digest` over received bytes and compare it before signature acceptance.
4. Resolve `keyid` to an enrolled active key, parse the exact stored SPKI, and enforce EC P-256.
5. Check `created`/`expires` freshness and nonce syntax.
6. Reconstruct the RFC 9421 signature base and verify the 64-byte P1363 signature.
7. Transactionally consume the nonce and authorize the operation against principal, active key epoch, role, scope, exact recipient, and endpoint.

An unknown key, wrong curve or key type, malformed signature, digest mismatch, stale/future request, replayed nonce, or authorization failure is a deterministic rejection. A timeout or lost response after step 7 is not evidence that the operation did not commit; the client retries the same immutable message identity with a fresh HTTP nonce and signature and reconciles the original result.

## Pairing redemption

`POST /v1/pairing/redeem` uses this same profile. Before enrollment, the `keyid` in the HTTP signature is derived from the supplied P-256 SPKI in the digest-covered body. The relay verifies the key type, derived ID, signature, token/session state, and proof-of-possession before atomically consuming the session and creating the predetermined principal and scopes.

The redemption body is forbidden from carrying `enrollment_role`, `enrollment_scopes`, `direction`, or any other authority request. Those values come only from the local-admin-created pairing session.

## Interoperability and hostile vectors

`vectors/http-signature-vectors.json` contains a fixed valid P-256/P1363 vector and rejection vectors for RSA, P-384, digest mismatch, stale freshness, and nonce replay. The vector private key is test-only material; it is not a deployment secret. The key-id assertion is over the public SPKI bytes, not over a PEM wrapper or textual key representation.
