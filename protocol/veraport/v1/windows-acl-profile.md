# VeraPort Windows production ACL and SCM qualification

Status: **PASS_EXACT** for the current LocalSystem service model.

Exact executable subject:
- head: `07ff6643b7aec5e47106b6f58e2bf42ec38d820f`
- tree: `41447e1cba477b77fdf7ca77b759f2fbe61e6cb5`

## Cryptography boundary

The runtime dependency is now `cryptography>=42,<49`.

The previous `<47` cap had no source-local API requirement excluding the 47/48 lines. VeraPort uses P-256 ECDSA, SPKI/PEM serialization/loading, X.509 test certificate construction, and DSS signature conversion. The exact candidate passed both whole-chain and Windows SCM qualification under `cryptography 48.0.1`.

49.x remains outside the declared range pending a fresh review.

## ACL model

The qualified Windows Service account is LocalSystem.

Sensitive VeraPort files and their private parent directories are protected from inherited/broad replacement. The only allowed principals are:
- `NT AUTHORITY\SYSTEM` / SID `S-1-5-18`;
- `BUILTIN\Administrators` / SID `S-1-5-32-544`.

Observed sensitive-file ACL:
- SYSTEM: Read/Synchronize;
- Administrators: FullControl.

Observed protected-directory ACL:
- SYSTEM: FullControl;
- Administrators: FullControl.

Observed owner: Built-in Administrators. DACL inheritance was protected.

The service validates these ACLs before constructing the listener.

## Defects found during qualification

Two real defects were found and repaired before PASS:

1. File-only ACL protection did not prevent replacement through a broadly writable parent directory. The repair protects/validates the private parent directories as well.
2. Under SCM, pywin32 invokes `SvcDoRun()` on a service worker thread. `asyncio.run()` selected the Windows Proactor loop, which failed at `signal.set_wakeup_fd`. The repair uses an explicit Selector event loop in SCM service mode. Windows `process.exec` uses `subprocess.run(shell=False)` in a worker thread so it remains compatible with that service loop.

## Actual SCM qualification

A temporary qualification service named `VeraPortAgentQualification` was registered under SCM, running as LocalSystem.

The exact candidate achieved:
- ACL validation: PASS;
- SCM install: PASS;
- SCM start: PASS;
- authenticated live filesystem round trip through the running service: PASS;
- SCM stop: PASS;
- SCM delete: PASS;
- post-delete listener: ABSENT.

The live round trip used loopback TLS, P-256 mutual VeraPort authentication, controller-bound routing, a VeraPort lane, and actual allowed-root file write/read. `process.exec` remained disabled.

## Cleanup

Readback after qualification confirmed:
- qualification service absent;
- production `VeraPortAgent` absent;
- `C:\ProgramData\VeraMesh` absent;
- qualification wrapper absent;
- no `pythonservice.exe` process;
- no VeraPort firewall rules;
- no Python Service event-source residue;
- pywin32 still importable;
- pywin32 helper relocation restored to its pre-qualification layout;
- exact Git checkout clean at the tested head/tree.

## Claim ceiling

This qualifies the **LocalSystem SCM lifecycle + protected file/directory ACL profile** for the exact candidate.

It does not authorize or claim production installation, non-loopback exposure, firewall/network changes, persistent production credentials, Synology deployment, ChatGPT/MCP production connection, or a future lower-privilege Windows service account.
