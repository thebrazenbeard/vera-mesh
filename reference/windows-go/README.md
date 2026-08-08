# Windows Go reference peer

This directory is the independent Go side of the Vera Mesh first-install interoperability slice. It intentionally does not import or execute the Python implementation.

Current implemented foundation:

- `VERA_MESH_CANONICAL_JSON_V2` syntax/byte canonicalization with the accepted 11 positive and 11 hostile fixtures.
- Durable NOTE inbox custody that binds `message_id`, `pair_id`, `trust_generation`, sender, recipient, message schema, payload byte count, payload SHA-256, and payload bytes. The live wire-session profile is deliberately not durable message identity.
- Canonical message IDs are RFC 9562 UUIDv4 lowercase text. They carry no authorization and no clock/order semantics.
- The inbox returns `DURABLE_INBOX_ACCEPTED` only after a checksum-framed journal record is written and `Sync` succeeds. Exact retries reuse the prior disposition; a reused ID with changed durable binding conflicts.
- A mechanically incomplete final journal frame is treated as never committed and truncated back to the last verified record boundary. A complete checksum/semantic failure blocks as storage corruption. A failed append/sync poisons the live store until recovery/reopen.

Deliberate non-claims and open gates:

- This is not yet a complete Windows peer executable. TLS/pairing/HELLO/capabilities/network plumbing still has active security/profile dependencies.
- Product canonical resource limits are not invented here. The accepted resource-limit profile is still a separate gate; `CanonicalizeSyntax` proves syntax/byte behavior only.
- Local tests are structural evidence only. They are not Windows x64, DS216, LAN, power-loss, package, or product acceptance evidence.
- The current security successor is not accepted while revoked-certificate re-enrollment remains under correction/review. Pairing must ultimately reject a tombstoned exact certificate identity before `PENDING`.

Local verification:

```text
go test -race ./...
go vet ./...
```
