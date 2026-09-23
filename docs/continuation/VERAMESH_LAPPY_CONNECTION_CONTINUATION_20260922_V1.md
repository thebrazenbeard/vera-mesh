# VERAMESH LAPPY CONNECTION CONTINUATION — 2026-09-22 V1

## Current VeraMesh subject

Repository: `thebrazenbeard/vera-mesh`
PR: #25
Branch: `work/veramesh-rdc-replacement-v1-20260922`
Exact head at checkpoint: `60d233c8ccc25871b0666d00c53fa9be26c7cc74`

This branch replaces RDC as the Lappy connection path. Do not use Remote Desktop Commander as a reachability or control dependency for this lane.

## Qualified live path

Topology:

`TheSimsVault -> VeraMesh edge -> Tailscale -> Lappy VeraPort`

Observed endpoint facts:
- NAS local edge: `127.0.0.1:17445`
- Lappy VeraPort tailnet endpoint: `100.88.50.35:17444`
- TLS: 1.3
- ALPN: `veraport/1`
- direct and edge-presented certificate identity matched
- VeraMesh edge control status: RUNNING / READY
- package version observed earlier: `0.1.0-0017`

The live edge-retarget connector reported `changed:false`: the NAS was already pointing at Lappy correctly.

## VeraPort application authentication

A fresh authenticated application session was established from TheSimsVault through the VeraMesh edge to Lappy without RDC.

Qualified:
- existing enrolled controller identity reused
- workstation application signature verified
- granted capability classes: `fs.read`, `fs.write`
- `lane.list`: PASS
- observed lane count: 0
- process execution was not granted or changed
- no service/identity mutation occurred

Credential placement established for this path:
- controller private key exists on NAS under `/volume1/homes/psims85/.veramesh/lappy/`
- workstation public key exists in the same directory
- do not expose key contents in chat or Git

## Non-RDC path probe

Read-only tailnet probe observed:
- SMB 445: reachable
- VeraPort 17444: reachable
- SSH 22: not reachable
- WinRM 5985/5986: not reachable
- RDP 3389: not reachable
- WorkBridge default 8765: not reachable at that time

This is why WorkBridge installation on Lappy is the next operational step.

## Companion WorkBridge checkpoint

Repository: `thebrazenbeard/WorkBridgeMCP`

Continuation branch:
`state/workbridge-lappy-install-continuation-20260922-v1`

Primary continuation:
`state/continuation/WORKBRIDGE_LAPPY_INSTALL_CONTINUATION_20260922_V1.md`

Machine-readable continuation:
`state/continuation/WORKBRIDGE_LAPPY_INSTALL_CONTINUATION_20260922_V1.json`

Current WorkBridge repair PR at checkpoint:
- PR #5
- branch `repair/workbridge-current-main-v1-20260922`
- exact head `92c66159d9b610a94f59066ab2bb6416f4513093`
- exact-head CI PASS run `35797931646`

## Live authorization

The user explicitly authorized:

> installing and starting WorkBridgeMCP on Lappy, preserving the existing VeraPort identity and keeping process execution disabled unless separately authorized.

That authority does not authorize:
- replacing VeraPort identity
- enabling process execution
- widening firewall/Tailscale/public exposure
- unrelated merge/deploy/provider changes

## Immediate continuation

1. Fresh-check VeraMesh PR #25 and WorkBridge PR #5.
2. Resume the authorized WorkBridge installation on Lappy using the current qualified installer from PR #5.
3. Qualify WorkBridge runtime on Lappy.
4. Only after WorkBridge runtime PASS, wire VeraMesh to WorkBridge.
5. Keep WorkBridge loopback-only on Lappy; use VeraMesh as the controlled gateway.
6. Preserve VeraPort identity and keep process execution disabled.
7. Keep source/build/install/runtime/network/E2E qualification separate.
