# VeraMesh

VeraMesh is the umbrella multi-device architecture for connecting Vera-capable nodes without collapsing transport, identity, routing, relay semantics, workstation execution, or authority into one mechanism.

VeraRelay is a current VeraMesh relay/edge implementation. It is not a mandatory permanent hop: its useful durability, replay, receipt, audit, enrollment, and health mechanisms may remain in VeraRelay, be absorbed into a broader VeraMesh edge daemon, or be replaced if the replacement preserves or strengthens those invariants.

## Core principles

- Transport is replaceable. Tailscale or any other private carrier is connectivity, not Vera application authorization.
- Device identity is Vera-owned.
- Pairing is explicit and proof-of-possession based.
- Requests are replay/freshness protected.
- Durable delivery and live interactive delivery are separate path classes.
- Relay/edge receipt is not recipient completion.
- Protected effects remain governed outside transport mechanics.
- Interactive workstation access is direct-first, persistent-session-first, and multiplexed.

## VeraPort / VeraRDC

VeraPort is the VeraMesh workstation/RDC application profile. A VeraPort is a logical execution lane, not a TCP/UDP port.

One authenticated session may carry many independent lanes concurrently. The current reference core implements capability narrowing, collision claims, expiring leases, durable fencing/idempotency, root-bounded filesystem operations, default-deny argv process execution, and concurrent local dispatch.

The synchrony profile adds three ordered path classes:

```text
DIRECT_STREAM  -> authenticated hot path to Lappy
EDGE_STREAM    -> authenticated hot path through a VeraMesh edge
DURABLE_RELAY  -> store-and-forward fallback/recovery
```

The hot path wins when it is current and authenticated. A Synology edge can remain useful for rendezvous, controller ingress, audit, durable fallback, and offline delivery without forcing every interactive command through a queue.

The ChatGPT-facing MCP/plugin adapter is intended to be thin. The persistent Lappy connection lives behind it so each tool call can enter an already-hot VeraMesh session instead of reconnecting from scratch.

See:
- `docs/ARCHITECTURE.md`
- `docs/SECURITY_MODEL.md`
- `docs/VALIDATION_MATRIX.md`
- `protocol/veraport/v1/README.md`
- `protocol/veraport/v1/synchrony-profile.md`
- `docs/superpowers/specs/2026-09-19-veraport-rdc-v1-design.md`

No current source status implies deployment, Lappy installation, network exposure, MCP registration, or end-to-end acceptance.
