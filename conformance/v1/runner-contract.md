# VeraMesh V1 black-box conformance runner contract

This contract defines how an implementation is tested without importing VeraRelay internals. The runner may speak HTTP to a running target and may use a separately controlled process supervisor, but it does not read the target database or call private modules.

## Target adapter boundary

An adapter supplies these black-box operations:

```text
pair(role) -> session material and enrolled principal
submit(principal, immutable envelope) -> HTTP result and custody receipt
fetch(principal, message_id) -> HTTP result and sealed envelope
receipt(principal, message_id, receipt) -> HTTP result
health() -> structured health response
restart() -> controlled process restart
```

The adapter may implement setup and teardown for its own isolated target. It must report the exact implementation repository, source commit, build identity, and artifact hash when those exist. A runner result is not release or deployment evidence.

## Result contract

Each case emits one JSON record with:

```json
{
  "case_id": "mailbox-same-id-different-envelope",
  "status": "PASS",
  "target": {"implementation": "...", "source_commit": "...", "artifact_sha256": "..."},
  "observations": ["..."],
  "http_statuses": [201, 409],
  "evidence": ["..."],
  "started_at": "...",
  "finished_at": "..."
}
```

Permitted statuses are `PASS`, `FAIL`, `BLOCKED`, `NOT_RUN`, and `UNRUN`. `PASS` requires fresh direct observation of every expected assertion for the exact target identity. A green schema or unit test does not establish a runtime case pass. `BLOCKED` identifies a missing target capability or safe test precondition; it is not a pass.

## Safety boundary

The conformance runner never installs a package, deploys, changes Tailscale or other network/provider state, changes credentials, merges a branch, deletes user data, or performs an irreversible recovery action. Cases requiring a real DS216, phone, host, disk pressure, or upgrade are marked `requires_external_target: true` and remain `UNRUN` until explicitly authorized and provisioned.

## Required evidence discipline

The runner records observed HTTP status, response body classification, message/receipt IDs, exact target identity, and relevant timestamps. It must not record endpoint private keys, pairing tokens, plaintext application payloads, or unrelated personal data. Ambiguous outcomes are reconciled from the target's public API and retried idempotently; absence of a response is never recorded as proof of non-commit.
