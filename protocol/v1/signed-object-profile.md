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
