# VeraPort V1 hot-session authentication profile

Status: implementation candidate.

## Purpose

Bind one persistent interactive VeraPort session to an already-enrolled controller identity and one exact workstation identity without making transport reachability or relay custody into authority.

This profile reuses VeraMesh's P-256 / SHA-256 identity convention: key ID is SHA-256 of DER SPKI bytes. The reference principal forms are `controller:<key-id>` and `workstation:<key-id>`.

## Handshake

1. Workstation emits a fresh 32-byte random challenge containing its principal and key ID.
2. Controller signs a client-auth object binding:
   - protocol version;
   - controller principal and key ID;
   - exact workstation principal;
   - workstation challenge;
   - fresh client nonce;
   - requested capability subset.
3. Workstation verifies enrollment, key binding, proof of possession, challenge binding, and local capability ceiling.
4. Workstation returns a signed server-accept binding:
   - fresh session ID;
   - both principals;
   - workstation key ID;
   - both nonces;
   - exact granted capability subset;
   - expiry.
5. Controller verifies the server signature against the pinned workstation key before treating the session as authenticated.

A new workstation challenge invalidates replay of an old client-auth proof.

## Signature format

ECDSA P-256 / SHA-256 is encoded as IEEE P1363 `r || s`, 64 raw bytes, Base64url without padding, matching the existing VeraMesh signed-object convention.

The hot-session profile intentionally does not depend on JSON reserialization for signature verification. It signs an explicit ordered UTF-8 field encoding.

Each field is:

```text
ASCII(field-name) || "=" || ASCII(decimal-byte-length) || ":" || UTF8(value) || "\n"
```

Client signature base:

```text
UTF8("veramesh-veraport-v1/session-client-auth\n")
protocol_version
controller_principal
controller_key_id
workstation_principal
server_challenge
client_nonce
requested_capabilities
```

Server signature base:

```text
UTF8("veramesh-veraport-v1/session-server-accept\n")
protocol_version
session_id
controller_principal
workstation_principal
workstation_key_id
server_challenge
client_nonce
granted_capabilities
expires_at_ms
```

Capability values are deduplicated, lexically sorted, and joined by ASCII comma before field encoding. Capability names in VeraPort V1 do not contain commas.

## Authority

Successful mutual authentication establishes identity and a session capability ceiling; it does not itself authorize arbitrary workstation execution.

The workstation's local policy is authoritative for which capabilities an enrolled controller may request. The granted set MUST be a subset of both the controller request and local policy.

`process.exec` remains subject to the separate local default-deny gate documented by VeraPort.

## Transport

This handshake is application authentication, not transport confidentiality.

Production direct/edge hot paths MUST run over a reviewed confidentiality/integrity transport such as QUIC/TLS. The current reference byte-stream tests use loopback only and do not qualify raw TCP for deployment.

## Reconnection

A transport reconnection does not silently resurrect process-local lanes. A new or resumed authenticated application session must preserve durable fencing/idempotency semantics and reconcile ambiguous requests before retry.
