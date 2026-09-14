# VeraMesh V1 Inner Authorship Profile

Status: normative.

Endpoint-to-endpoint authorship uses JWS Compact Serialization with `alg=ES256`. The complete compact JWS is then encrypted as the HPKE plaintext; VeraRelay sees only ciphertext.

The protected JWS header MUST contain `alg:"ES256"`, the sender signing `kid`, and `typ:"veramesh-v1-inner"`. The JWS payload is the exact original application payload bytes. ES256 is accepted only after the verifier independently resolves `kid` to an active EC P-256 key for the declared sender and key epoch.

The JWS signing input is the exact ASCII bytes defined by JWS Compact Serialization:

```text
BASE64URL(protected-header-bytes) || "." || BASE64URL(original-payload-bytes)
```

The recipient verifies the signature over those exact received compact-serialization segments before parsing application JSON or other payload formats. It MUST NOT parse the payload and reserialize it to manufacture different bytes for verification. The verified payload bytes are only then handed to the application layer.

Outer VeraMesh AAD binds message identity, sender signing key ID/epoch, recipient HPKE key ID/epoch, stream, and sequence. After HPKE decryption, the JWS `kid` MUST equal the outer `sender_signing_key_id`; a mismatch is rejected.
