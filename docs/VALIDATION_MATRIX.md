# VeraMesh / VeraRelay Validation Matrix

This matrix defines evidence required before a relay deployment should be treated as production-ready. `Running` in a package manager is not sufficient.

## Baseline and identity

- Confirm exact package version and installed artifact SHA-256.
- Confirm Node/runtime version, architecture, package UID, target path, data path, and config path.
- Confirm listener is `127.0.0.1:17443` and no unintended IPv4/IPv6 public/LAN listener exists.
- Confirm capability advertisement identifies VeraMesh relay role and transport-neutral semantics.

## Lifecycle

- Cold start from stopped state.
- Repeated stop/start cycles.
- Stale PID file handling.
- Slow-start ownership race handling.
- Server child crash and supervisor restart.
- Supervisor crash and DSM package recovery.
- Restart-limit behavior.
- Full NAS reboot persistence and auto-start.

## Persistence and delivery

- Enqueue, fetch, acknowledge.
- Restart with pending message; verify message persists.
- Restart after acknowledgement; verify acknowledgement persists.
- Duplicate message ID behavior.
- Queue ordering by sequence.
- Queue count/byte limits.
- Expiration and cleanup.
- Offline recipient / delayed delivery.
- Reboot during pending delivery.

## Authentication and pairing

- Existing Android P-256 identity enrolls successfully.
- Device ID equals SHA-256(SPKI).
- Pairing token is short-lived and one-time.
- Pairing rejects wrong token, expired token, wrong device ID, malformed key, invalid proof signature.
- Signed request succeeds with correct method/path/timestamp/nonce/sequence/body hash.
- Reject modified body, wrong method/path, stale timestamp, reused nonce, reused/non-monotonic sequence, unknown device, revoked device, invalid signature.
- Replay/sequence state survives restart.

## Audit integrity

- Verify current audit chain.
- Verify legacy rotated `audit-<timestamp>.jsonl` segments plus live `audit.jsonl` as one continuous chain.
- Verify persisted `HEAD` matches final event hash.
- Verify tampered disposable copy fails closed.
- Verify new events appended after predecessor history keep the chain valid.
- Verify rotation preserves continuity.

## Transport and exposure

- Confirm direct LAN access to relay port is unavailable.
- Confirm no public router forwarding.
- Confirm no public reverse proxy exposure.
- If Tailscale is enabled: confirm tailnet reachability, Tailscale Serve -> localhost mapping, ACL behavior, and explicit Funnel disabled state.
- Confirm transport authorization and Vera application authentication remain independent gates.

## Resource and failure behavior

- Idle CPU/RAM on DS216.
- Sustained message traffic CPU/RAM.
- File descriptor growth.
- Audit/log growth.
- Corrupt queue record handling.
- Corrupt device record handling.
- Corrupt config behavior.
- Occupied port behavior.
- Missing Node behavior.
- Unwritable data directory behavior.
- Support bundle generation and diagnostic usefulness.

## Upgrade / rollback

- Upgrade preserves config, devices, queues, replay/auth state, audit history, and valid runtime state.
- Schema/format migration is explicit and verified.
- Downgrade/rollback expectations are documented before attempting rollback.
- Uninstall/reinstall preservation semantics are documented before destructive testing.

## End-to-end acceptance

Minimum acceptance sequence:

1. NAS reboot.
2. Relay auto-starts.
3. Health is semantically green.
4. Audit chain verifies.
5. Private ingress is available without public exposure.
6. Phone pairs using existing P-256 identity.
7. Signed phone -> relay -> host message succeeds.
8. Host acknowledgement is persisted.
9. Signed host -> relay -> phone message succeeds.
10. Restart relay mid-flow and prove eventual delivery still works.
11. Record exact versions, hashes, config, binding, identities, and observed evidence.

Use PASS / FAIL / BLOCKED / NOT RUN with concrete evidence. Inference should never substitute for an observed pass.
