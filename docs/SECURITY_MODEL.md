# VeraMesh Security Model

## Trust boundaries

VeraMesh deliberately separates private transport membership from Vera application identity and authorization.

A transport such as Tailscale answers: "May this peer reach the private ingress?" Vera application authentication separately answers: "Is this the enrolled device, did it sign this exact request, is the request fresh, non-replayed, monotonic, non-revoked, and permitted?"

Compromise of one layer must not automatically imply compromise of the other.

## Current device authentication

The existing Android client identity uses P-256 ECDSA in Android Keystore.

- algorithm: ES256 / ECDSA P-256 with SHA-256
- private key: device-local
- public identity: SPKI-encoded public key
- device ID: SHA-256 of SPKI bytes

A relay stores the public identity and status, not the private key.

## Pairing

Pairing uses a short-lived high-entropy one-time token plus proof of possession. The pairing proof binds at least the device ID, nonce, and token-derived value. Successful enrollment consumes the pairing state. Pairing must fail closed on expiry, token mismatch, invalid proof, or device-ID/public-key mismatch.

## Signed requests

Authenticated requests bind the canonical method, path, timestamp, nonce, monotonic sequence, and body hash. Verification includes:

- enrolled active device
- supported key algorithm
- device ID matches stored public key
- valid signature
- timestamp within configured freshness window
- unseen nonce
- sequence strictly greater than last accepted sequence
- non-revoked status

State transitions are persisted so replay protection survives process restart.

## Network boundary

The current relay core is intended to bind only to `127.0.0.1:17443`. Remote ingress should be supplied by a replaceable private transport adapter. Current preferred design is Tailscale Serve proxying to localhost.

Forbidden default posture:

- direct public listener
- router port-forwarding to VeraRelay
- Tailscale Funnel
- treating tailnet membership as Vera application authentication

## Audit integrity

The relay uses a hash-chained JSONL audit trail. Historical rotated audit segments must remain part of one continuous verification chain. A failed integrity check is a health failure and must not be silently reset.

Legacy predecessor data may use rotated `audit-<timestamp>.jsonl` segments followed by live `audit.jsonl`; verification must process them in chronological segment order and then compare the resulting head to persisted `HEAD`.

## Availability vs integrity

A process being alive is weaker evidence than a healthy relay. Package-manager `Running` status is weaker still than end-to-end service health. Startup and status checks should distinguish:

- process exists
- process is the expected executable/runtime
- server child is present
- localhost endpoint answers
- health body is semantically healthy
- audit integrity is valid
- ingress boundary is as configured

## Authority boundary

Transport reachability, authenticated identity, and operational authority are distinct. Possessing a valid device key or authenticated network path must never silently grant protected Vera effects such as deployment, deletion, credential changes, production mutation, or other governed actions.
