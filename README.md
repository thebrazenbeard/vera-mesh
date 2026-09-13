# VeraMesh

VeraMesh is the umbrella multi-device architecture for connecting Vera-capable nodes without collapsing transport, identity, routing, relay semantics, or authority into one mechanism.

VeraRelay is the NAS relay node inside VeraMesh. The current Synology implementation runs on DSM 7 / `armada38x`, keeps its core listener on `127.0.0.1:17443`, and is designed to use a private ingress such as Tailscale Serve rather than exposing the relay directly to the public Internet.

## Core principles

- Transport is replaceable. Tailscale is a private carrier, not Vera application authentication.
- Device identity is Vera-owned. Existing Android identity uses P-256 / ES256 with device ID = SHA-256 of the SPKI public key.
- Pairing is explicit and one-time, with proof of possession.
- Requests are signed and protected by timestamp freshness, nonce replay defense, monotonic sequence checks, and revocation.
- Delivery is durable and directional (`phone-to-host`, `host-to-phone`) with explicit acknowledgement state.
- Relay state and audit history survive restart and upgrade.
- Audit integrity fails closed, including legacy rotated audit segments.
- Capability and health advertisement are protocol concerns; provider-specific ingress is not.
- Protected effects remain governed outside transport mechanics.

## Current implementation relationship

```text
Phone / client node
      |
private transport (e.g. Tailscale)
      |
HTTPS / ingress adapter
      |
127.0.0.1:17443
      |
VeraRelay (NAS relay node)
      |
VeraMesh routing / delivery semantics
      |
Vera host node
```

See `docs/ARCHITECTURE.md`, `docs/SECURITY_MODEL.md`, and `docs/VALIDATION_MATRIX.md` for the current foundation.
