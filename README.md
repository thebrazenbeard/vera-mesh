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

## VeraPort / VeraRDC extension candidate

VeraPort adds capability-bounded logical workstation lanes to VeraMesh. A VeraPort is not a literal TCP/UDP port: one authenticated session can multiplex many independent execution lanes without opening one public network socket per task.

The proposed split keeps VeraRelay as a durable control-plane courier while a future authenticated live data plane carries higher-volume terminal, file, screen, and UI traffic. The workstation agent owns execution authority; relay custody, VLAN placement, LAN/tailnet membership, or message possession does not grant OS capability.

The current reference slice lives in `protocol/veraport/v1/` and `reference/veraport_agent/`. It implements capability narrowing, lane leases/fencing, collision claims, root-bounded file access, argv-based process execution, and concurrent JSONL dispatch over a local stdio adapter. Broad `process.exec` authority is disabled by default and requires an explicit Lappy-side startup grant; allowed filesystem roots do not sandbox a child process once that grant is enabled. The reference slice does **not** expose a network listener or claim Lappy installation/end-to-end acceptance.

See `docs/ARCHITECTURE.md`, `docs/SECURITY_MODEL.md`, `docs/VALIDATION_MATRIX.md`, and `docs/superpowers/specs/2026-09-19-veraport-rdc-v1-design.md`.
