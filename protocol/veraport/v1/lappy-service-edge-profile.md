# VeraPort V1 Lappy service and edge boundary

Status: implementation candidate.

## Lappy service posture

The Windows/Lappy service is fail-closed by default.

Configuration defaults and invariants:
- listener address must be a literal IP;
- non-loopback binding requires explicit `allow_non_loopback_listener=true`;
- `process.exec` defaults false;
- at least one allowed filesystem root is required;
- TLS certificate/key, workstation identity key, and controller trust file must already exist;
- missing trust/identity material blocks startup rather than generating replacement identity;
- state database persists beneath an explicitly configured path;
- lane/inflight bounds are finite.

Network exposure remains a separate installation/deployment decision. Source support for an override is not authority to enable it.

## Persistent identity/trust

The workstation private key is loaded from an existing PEM and must be P-256.

Controller trust is an explicit `VERAPORT_CONTROLLER_TRUST_V1` JSON record. Every controller entry binds:
- exact controller principal;
- exact key ID;
- P-256 public key;
- local capability ceiling.

Principal and key ID are recomputed from the supplied public key. Mismatch fails closed. Empty trust fails closed.

No first-run trust-on-first-use behavior is defined.

## Edge roles

A VeraMesh edge has two distinct behaviors:

### LiveEdgeProxy
A current authenticated edge can forward a live request synchronously. It returns the actual downstream VeraPort result.

### DurableRelayFallback
A durable fallback accepts a request for queued delivery. Its immediate result is only `QUEUED_NOT_EXECUTED` plus a relay custody receipt identifier.

Queue custody MUST NOT be promoted to Lappy execution or operation completion.

These roles may be implemented by one Synology/VeraMesh process, but their evidence semantics remain distinct.
