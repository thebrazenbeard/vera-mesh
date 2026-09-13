# VeraMesh V1 / VeraRelay 0.4.0 / VERA Mobile 0.2.0 Design

Date: 2026-09-13
Status: APPROVED DESIGN, pending written-spec review

## Purpose

VeraRelay is a blind, durable, authenticated courier between Vera-capable endpoints. It knows who is talking, what authority that principal has, where an opaque message is going, whether custody was accepted, whether the recipient durably received it, and whether the relay is healthy. It does not run inference, own canonical Vera memory, decide protected Vera effects, or read application plaintext.

VeraMesh V1 is intentionally narrow:

`VERA Mobile -> private transport -> VeraRelay -> Vera Host Adapter`

V1 does not include generic observer nodes, arbitrary mesh routing, distributed discovery, or speculative capability negotiation.

## Frozen predecessor

VeraRelay `0.3.0-0005` is prototype evidence, not the implementation base whose identity may be silently rewritten. Its SPK, hash, tests, source snapshot, known defects, and observed NAS behavior are preserved as predecessor evidence. Fixes belong to `0.4.0`.

Known `0005` defects include weak algorithm-label enforcement, no real role/scope authorization, mutable ACK regression, conflicting duplicate-ID acceptance, health-only audit failure, corrupt queue poisoning, stale validation/upgrade metadata, and unpinned source provenance.

## Protocol and request authentication

V1 uses HTTP/JSON and RFC 9421 HTTP Message Signatures. It does not introduce CBOR/COSE in the first interoperability cut.

Protected requests use one signature label, `vera`, and one accepted algorithm, `ecdsa-p256-sha256`. The verifier MUST inspect the actual public key and reject any key that is not EC P-256; an algorithm label is not evidence of key type or curve.

`keyid` identifies the enrolled signing key from SHA-256(SPKI). `tag` is fixed to `veramesh-v1`. Every protected request covers `@method` and `@path`; body-bearing requests also cover `content-digest` and `content-type`. V1 protected endpoints avoid semantically meaningful query strings.

Bodies use RFC 9530 `Content-Digest` with SHA-256. The verifier MUST recompute the digest over the received bytes and verify the HTTP signature over the covered components.

Every signature requires `created`, `expires`, `nonce`, `keyid`, `alg`, and `tag`. V1 uses a five-minute freshness window and a cryptographically random nonce of at least 128 bits. Nonces are single-use per signing principal and are consumed transactionally.

HTTP authentication does not use a global monotonically increasing request sequence. Message ordering belongs to the message layer.

Pairing redemption uses the same RFC 9421 profile. Before enrollment, `keyid` is derived from the supplied P-256 SPKI in the digest-covered request body; the relay verifies the derived ID, request signature, token, requested enrollment binding, and transaction state before creating the principal.

## Principals and authorization

V1 has three explicit principal classes:

- `phone-client`: may submit phone-originated sealed envelopes, fetch host-originated envelopes addressed to itself, and create recipient receipts for those envelopes.
- `vera-host`: may submit host-originated sealed envelopes, fetch phone-originated envelopes addressed to itself, and create recipient receipts for those envelopes.
- `relay-admin`: local administrative authority for enrollment creation, revocation, migrations, diagnostics, support export, recovery, and transport configuration.

Authentication grants no permission by itself. Every protected operation is authorized against the enrolled principal, active key, role, explicit scope, recipient, and operation.

The public API does not accept an arbitrary queue-direction claim and then authorize it. Direction and legal recipient class are derived from the authenticated role and endpoint contract.

`relay-admin` is not exposed as a normal remote VeraMesh application principal in V1. Pairing-session creation, destructive recovery, relay-key rotation, support export, and network/transport mutation remain local administrative operations.

## End-to-end message security

Relay-visible application payloads are opaque sealed envelopes. VeraRelay never holds endpoint decryption keys and does not decrypt application plaintext.

Endpoint signing and encryption identities are separate. P-256 signing keys authenticate messages and requests. HPKE keys encrypt application envelopes.

VERA Mobile uses Tink X25519 HPKE key material protected at rest by an Android-Keystore-backed master key. The P-256 signing identity remains directly Android Keystore backed. Vera Host uses an independent conforming HPKE/signature implementation and its own key storage policy.

The sender signs an inner application envelope before HPKE encryption. The recipient decrypts and verifies the original sender signature. HTTP Message Signatures prove who deposited a request with the relay; inner signatures prove end-to-end authorship independently of the relay.

The HPKE context/AAD binds at minimum protocol version, sender principal, sender signing-key ID/epoch, recipient principal, recipient HPKE-key ID/epoch, stream ID, message ID, message sequence, and message type. A relay or intermediary cannot substitute those fields without decryption or verification failure.

## Sealed-mailbox delivery model

VeraRelay is a sealed mailbox and handoff ledger, not a mutable broker state machine.

Accepted message envelopes are immutable. Delivery evidence is represented as append-only receipts/events. Current delivery state is a projection of legal events; it is not stored as a freely mutable status field.

The sender retains its durable outbound copy until it receives a valid recipient-signed durable-storage receipt. The recipient durably stores the sealed envelope before issuing that receipt. The relay retains its copy for a bounded rescue-retention period after durable recipient receipt.

The baseline delivery guarantee is at-least-once until durable recipient storage. Duplicate transport delivery is permitted. Recipients deduplicate by stable message ID.

`recipient_processed` is optional application evidence. It does not gate relay durability or garbage collection once durable recipient storage has been cryptographically acknowledged.

Relay acceptance, recipient durable storage, and recipient processing are different propositions and therefore have different receipts/signers.

Relay acceptance receipts are signed by the relay custody key and bind at least relay installation identity, message ID, envelope hash, sender, recipient, and acceptance time. They prove relay custody only; they do not prove endpoint authorship.

Recipient-storage and recipient-processing receipts are endpoint-signed protocol messages. The relay cannot fabricate them.

Idempotency is keyed by sender principal plus stable message ID. Retrying the same immutable envelope returns the original acceptance result. Reusing the same message ID with different immutable content returns `409 idempotency_conflict`.

Ordering evidence uses a monotonically increasing message sequence per logical sender-to-recipient stream. A sequence gap is observable but does not automatically block all later delivery forever.

## SQLite transactional ledger

SQLite is the sole mutable relay state store in `0.4.0`. It uses WAL mode, `synchronous=FULL`, foreign keys, explicit schema versions, and transactional migrations.

The minimum logical state includes principals, signing keys, encryption-key bindings, roles/scopes, pairing sessions, replay nonces, immutable messages, receipts/events, audit events, external audit checkpoints, schema metadata, and relay installation identity.

Security-significant transitions are atomic. Pairing redemption consumes the token, creates the principal, binds signing/encryption keys, grants the predetermined role/scopes, and appends its audit event in one transaction. Message acceptance performs idempotency checks, stores the immutable envelope, and appends custody/audit evidence in one transaction.

The relay database is a durable transactional working ledger, not the sole system-of-record for the conversation. Sender outboxes and recipient inboxes provide independent recovery copies.

## Failure, recovery, and health semantics

Absence, timeout, or transport failure never proves that a transition did not commit. Ambiguous outcomes are reconciled from durable state and retried idempotently with a fresh HTTP nonce/signature and the same stable message identity.

If relay commit succeeds but the response is lost, retry returns the original acceptance. If recipient inbox storage succeeds but its receipt is lost, redelivery is deduplicated and the recipient reissues the valid storage receipt.

Individual corrupt records are not silently discarded. They enter explicit quarantine/degraded handling and affect health according to severity. Security-integrity failure blocks security-sensitive mutations until reconciled; `0.4.0` does not repeat `0005` behavior where audit failure merely made health red while writes continued.

Health is structured, not a single boolean. At minimum it reports process, database, audit, storage, auth, transport, pairing, write-availability, and degraded reasons, plus a derived overall state.

Transport loss does not imply database failure. A healthy relay core with unavailable private ingress reports those states separately.

Schema migrations are transactional. A release never opens normal writable service against a partially migrated schema. Downgrade compatibility must be explicitly declared; otherwise rollback restores the captured predecessor software and compatible data together.

Resource limits are implementation-profile evidence, not invented protocol constants. The DS216 profile must define tested body limits, pending-envelope quotas, database/disk thresholds, retention bounds, maintenance/checkpoint policy, and rate limits before release acceptance.

## Audit and tamper evidence

The relay maintains an append-only logical audit history bound to transactional state. Local chaining alone is not treated as immutable against a fully compromised NAS.

Periodic audit-head checkpoints are persisted by Vera Host and returned as host-signed checkpoint receipts. A relay that later rewrites its own local history cannot forge a historical host receipt.

Migration from `0005` verifies and inventories the predecessor JSONL audit material, records exact file hashes and final verified legacy head, preserves the predecessor evidence without rewriting it, and begins the SQLite-era history with a migration event that references that predecessor manifest/head.

## Key lifecycle and compromise recovery

Phone signing, phone HPKE, host signing, host HPKE, and relay custody are separate cryptographic roles. The relay custody key signs relay-custody evidence only and is not a Vera identity key.

Keys carry stable key IDs and monotonically increasing epochs beneath logical principals. Normal rotation is authorized by the old active signing key when available. Old HPKE private keys remain available to their endpoint until envelopes addressed to that epoch are durably resolved or expire beyond recovery retention.

Normal rotation is recorded as `active -> retired`; compromise is `active -> revoked`. Historical key records are retained for verification and audit rather than deleted.

Loss of an endpoint key is an explicit administrative recovery discontinuity. A reinstalled/wiped phone receives new cryptographic identity material and does not silently inherit the predecessor installation's key continuity.

VeraRelay never backs up endpoint private keys. Relay recovery data and endpoint-secret recovery remain separate. Loss of the NAS custody key creates a new relay installation identity rather than pretending cryptographic continuity.

## Pairing

Pairing-session creation is local-admin-only and predetermines the role/scopes available to the enrollment. A remote client cannot request arbitrary authority.

Redemption proves possession of the proposed P-256 signing key, binds the separately supplied HPKE public key, consumes the one-time token, and creates the principal atomically. Token reuse, role escalation, key mismatch, stale sessions, and concurrent redemption are rejection cases.

## Transport boundary

V1 assumes private transport but does not treat transport membership as Vera authorization. Tailscale Serve is the intended first ingress; Funnel/public exposure is not part of V1.

The relay core remains bound to localhost unless a later reviewed transport adapter requires otherwise. Tailscale configuration is a separate administrative mutation and must be inspected before change and read back after change. Vera authorization still occurs at the application layer.

## Release and provenance model

`vera-mesh` is the protocol authority. It contains the normative V1 contract, state/receipt definitions, schemas, test vectors, threat model, conformance suite, and an implementation registry pinning exact source commits and release hashes.

The first conforming implementation set is:

- VeraRelay `0.4.0`: Synology / Node 22 / SQLite
- VERA Mobile `0.2.0`: Android / Java / Tink
- Vera Host Adapter `0.1.0`: Windows local Vera runtime / Python / Tink

No floating branch or `latest` reference is sufficient for conformance. Each accepted implementation is bound by exact repository, commit, build identity, and artifact SHA-256 where an artifact exists.

Release evidence is separated into `SOURCE`, `BUILD`, `CONFORMANCE`, `DEVICE_TEST`, `DEPLOYED`, and `END_TO_END_ACCEPTED`. Passing one state does not imply a later state.

The conformance suite must include at least one implementation path built independently from the protocol specification rather than sharing internal relay code.

## Conformance and hostile acceptance

The release is not accepted merely because happy-path tests pass. Required hostile cases include: non-P-256 keys labeled as P-256; malformed/freshness-invalid/replayed RFC 9421 signatures; digest mismatch; pairing double redemption; role/scope escalation; cross-recipient fetch; same-ID/different-envelope collision; lost response after commit; duplicate delivery; receipt replay; backwards/invalid receipt transition; corrupted ledger/audit copy; audit-checkpoint mismatch; quota exhaustion; disk-low behavior; process death at transaction boundaries; NAS reboot with pending mail; transport loss; upgrade with pending envelopes; and predecessor rollback/recovery.

Cross-language vectors must prove Android signing -> Node verification and independent host signing -> relay verification. RFC 9421 ECDSA signatures use the required fixed P-256 signature representation; Android encoding conversion, if required by provider behavior, is tested as representation handling only and never implements ECDSA mathematics.

End-to-end acceptance requires a real phone, the real DS216 package, and the real Vera Host Adapter to pair independently, exchange sealed messages in both directions, verify endpoint authorship, verify relay custody receipts, verify recipient durable-storage receipts, survive an ambiguous retry, and preserve recoverability across a controlled restart.

## Implementation order

1. Freeze and inventory `0.3.0-0005` prototype evidence.
2. Establish immutable VeraRelay source provenance without rewriting predecessor evidence.
3. Publish the normative VeraMesh V1 protocol/specification and test vectors.
4. Build the SQLite transaction/migration layer under tests.
5. Build strict RFC 9421/P-256 request authentication and real role/scope authorization.
6. Build immutable sealed-envelope storage, idempotency, signed custody receipts, and append-only recipient receipt semantics.
7. Build VeraRelay V1 HTTP API and structured health/recovery behavior.
8. Build Vera Host Adapter `0.1.0` independently against the spec.
9. Build VERA Mobile `0.2.0` independently against the spec, including INTERNET permission, transport, pairing, signing, HPKE, durable outbox/inbox, and receipts.
10. Run independent conformance + hostile tests; fix real failures.
11. Build the `0.4.0` SPK only after source/conformance evidence exists.
12. Deploy only with Patrick's exact deployment authority; then run DS216 + phone + host end-to-end acceptance.

## Explicit non-goals for V1

V1 does not add a generic service bus, MQTT/NATS/Matrix dependency, binary protocol rewrite, direct-phone-to-host fast path, remote relay-admin API, endpoint-private-key escrow, NAS plaintext access, arbitrary mesh topology, exactly-once delivery claim, or application-processing guarantee.

Research after this design must answer a specific implementation blocker, compatibility question, or falsification test. Additional speculative architecture is not a release prerequisite.

## Immediate implementation probes

Before depending on them, implementation must verify on the actual target/runtime:

- Synology Node v22 build exposes the required `node:sqlite` functionality.
- Android provider behavior for RFC 9421 P-256 signature encoding is interoperable with the Node verifier; use provider P1363 directly where available or a tested DER-to-P1363 representation adapter.
- Tink HPKE keyset protection and X25519 support work on the targeted Android API range.
- Tailscale Serve command/state behavior on the installed NAS version matches the adapter assumptions before any network mutation.

A failed probe changes the implementation mechanism, not the approved system semantics, unless the failure proves the semantics infeasible.

## Approval record

Patrick approved: HTTP/JSON + RFC 9421; three-principal authorization; pragmatic Tink/Android-Keystore HPKE storage; sealed-mailbox + signed-receipt delivery; provenance/release order; failure/recovery semantics; and key lifecycle/compromise recovery. The design was then explicitly redirected away from endless pre-build refinement: land the implementation and let real evidence drive subsequent changes.
