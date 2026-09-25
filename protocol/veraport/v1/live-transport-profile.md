# VeraPort V1 live transport profile

Status: implementation candidate.

## Current reference carrier

The first network carrier candidate is persistent TLS 1.3 over an asyncio byte stream with ALPN `veraport/1`.

TLS supplies transport confidentiality/integrity and server certificate validation. VeraPort application authentication remains mandatory above TLS and performs controller/workstation proof of possession plus capability-ceiling binding.

TLS reachability or certificate validity alone grants no VeraPort capability.

## Connection flow

1. Establish TLS 1.3 with `veraport/1` ALPN.
2. Perform the VeraPort hot-session mutual application handshake.
3. Bind the accepted `SessionBinding` to a request-handler factory.
4. Keep the same byte stream alive.
5. Multiplex many concurrent request IDs over that stream.
6. Use controller-routing failover semantics if the stream disappears.

The server does not construct a general execution handler until the controller has passed application authentication.

## QUIC disposition

QUIC remains a desirable future carrier because transport-level independent streams and connection migration fit VeraPort well.

The VeraPort architecture does not depend on QUIC. The transport interface is intentionally replaceable so QUIC can be introduced after implementation/security review without changing application identity, lane authority, fencing, idempotency, or controller failover semantics.

## Deployment boundary

The current TLS integration test is loopback-only with a generated test certificate. It establishes source-level interoperability evidence, not permission to expose a listener on Lappy or Synology.

Production certificate/key provisioning, listener binding, firewall/network exposure, service installation, controller credentials, and end-to-end acceptance remain separate effects.
