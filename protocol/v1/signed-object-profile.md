# VeraMesh V1 Signed Object Profile

Status: normative.

Pairing proofs and delivery receipts use ECDSA P-256/SHA-256 with IEEE P1363 64-byte signatures. Their signed bytes are domain-separated UTF-8 bytes followed by RFC 8785 JSON Canonicalization Scheme (JCS) bytes. Implementations MUST verify those exact bytes and MUST NOT verify a parse-and-reserialize variant using a different serialization.

## Pairing proof

The pairing proof signature base is exactly:

```text
UTF8("veramesh-v1/pairing-redeem\n") || JCS(redemption-without-proof)
```

`redemption-without-proof` is the complete pairing redemption object with the `proof` member omitted. The raw pairing token is therefore covered by the proof. Verification uses the P-256 public key supplied by the redemption object after independently validating the key type, curve, and `signing_key_id = SHA256(SPKI)`.

## Delivery receipts

The receipt signature base is exactly:

```text
UTF8("veramesh-v1/receipt\n") || JCS(receipt-without-signature)
```

`receipt-without-signature` is the complete receipt object with the `signature` member omitted. The verifier resolves the signer key and epoch from the trusted principal/key history and verifies the exact JCS bytes before accepting the receipt proposition.

`protocol/v1/vectors/object-signing-keys.json` supplies test-only public keys used by the fixed pairing and receipt fixtures. The private keys are not protocol state or deployment credentials.

## Relay custody trust bootstrap and retry semantics

A successful local-admin pairing-session creation persists the relay trust tuple as (relay_installation_id, relay custody SPKI, custody key ID, custody key epoch). The endpoint and relay use that tuple as the trust bootstrap for relay-custody evidence; a receipt is not trusted merely because its signature verifies under some P-256 key.

A relay-custody receipt is accepted only when all three asserted values—relay_installation_id, signer.key_id, and signer.key_epoch—match the currently pinned tuple for that installation. The receipt signer principal MUST also be relay:<relay_installation_id>, and signature.key_id MUST equal signer.key_id. Custody-key rotation creates a new pinned tuple with a strictly greater epoch; historical tuples remain available for audit but are not current trust anchors.

The relay persists the exact signed custody-receipt bytes it generated. An ambiguous retry for the same sender principal plus immutable message ID and envelope hash returns those exact bytes; a semantically similar but byte-different caller-supplied custody receipt is not equivalent and is never accepted as a substitute.

Relay rescue-retention garbage collection may advance only after the relay has independently verified a recipient-storage receipt against the exact recipient principal, endpoint key ID/epoch, message ID, and envelope hash. A recipient-processed receipt is optional evidence and never substitutes for verified recipient storage.
