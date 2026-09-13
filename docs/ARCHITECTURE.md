# VeraMesh Architecture

## Scope

VeraMesh is the umbrella architecture for connecting Vera-capable nodes. VeraRelay is one node role inside that architecture, not a competing transport stack or identity system.

## Separation of concerns

### Identity
A VeraMesh device has a stable cryptographic identity. The current Android implementation uses an Android Keystore P-256 keypair. Device ID is derived as SHA-256 over the SPKI-encoded public key. The private key remains device-local.

### Enrollment / pairing
Enrollment is explicit. A relay creates a short-lived one-time token. The joining device presents its public key and signs a token-bound canonical pairing proof. The relay verifies that the claimed device ID matches the supplied public key and verifies proof of possession before persisting an active device record.

### Transport
Transport is replaceable. The initial preferred private transport is Tailscale, with Tailscale Serve proxying HTTPS to the localhost relay. Tailscale is not the Vera application-auth layer and must not be treated as sufficient authorization by itself.

### Relay
VeraRelay currently provides durable directional queues, application authentication, replay defense, acknowledgements, audit chaining, health/capability advertisement, cleanup, and lifecycle supervision.

### Routing
Routing semantics belong to VeraMesh, not to Tailscale or the NAS package. A relay node should advertise role, capabilities, health, supported queue directions, protocol version, and relevant degradation state without leaking provider-specific assumptions into the core protocol.

## Current message flow

```text
Phone node
  -> private ingress transport
  -> VeraRelay localhost HTTP core
  -> durable phone-to-host queue
  -> Vera host node

Vera host node
  -> VeraRelay localhost HTTP core
  -> durable host-to-phone queue
  -> private ingress transport
  -> Phone node
```

Messages are expected to carry stable message IDs, ciphertext payloads, sequence information, timestamps/expiry information, and acknowledgement progression. Relay persistence provides eventual-delivery behavior across ordinary process restarts.

## Acknowledgement model

The current relay distinguishes receipt by the relay from later recipient outcomes. Existing states include `relay_received`, `recipient_delivered`, `recipient_processed`, and `rejected`. Delivery state must not be promoted beyond what has actually been observed.

## Health and capabilities

A VeraMesh relay should distinguish at least:
- process alive
- relay core healthy
- audit integrity valid
- listener boundary correct
- application authentication enabled
- transport ingress present/absent
- pairing enabled/disabled
- queue/degradation state

`Running` in a package manager is not sufficient evidence of end-to-end health.

## Persistence boundaries

Relay state under the DSM package data directory is designed to survive upgrades. Current durable categories include config, devices, auth/replay state, queues, audit history, runtime state, and diagnostics. Upgrade logic must preserve valid predecessor data and explicitly handle schema or format migrations rather than silently resetting state.

## VeraMesh growth path

Future node roles may include phones, workstations, local Vera runtime hosts, relays, observers, or additional trusted appliances. The architecture should therefore keep identity, transport, routing, delivery, state, and authority independently evolvable.
