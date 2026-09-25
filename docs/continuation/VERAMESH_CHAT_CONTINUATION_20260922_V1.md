# VeraMesh chat continuation — 2026-09-22 V1

Status: `STARTING_SNAPSHOT — FRESHNESS REQUIRED BEFORE EFFECT`

## Canonical subject

Repository: `thebrazenbeard/vera-mesh`

Active branch: `work/veramesh-rdc-replacement-v1-20260922`

Draft PR: `#25 — Build persistent VeraMesh replacement surface for RDC`

Do not merge, deploy, install, rotate credentials, change firewall/Tailscale/provider state, expose a public endpoint, register a ChatGPT plugin, or destructively replace an existing workstation installation without Patrick's exact live authority.

The chat/session is not durable authority. Fresh-check GitHub before acting.

## Last fully verified code head

Exact code head: `6869ab669e23f8b887ee219994efc93794ed8ca3`

At that exact head:

- VeraPort reference workflow run `#381`: PASS.
- VeraRelay snapshot workflow run `#82`: PASS.
- Python 3.11: PASS.
- Python 3.12: PASS.
- Windows tests: PASS.
- Windows service entrypoint registration/removal smoke: PASS.
- Full portable Windows VeraPort service install + authenticated qualification + SCM restart + requalification + removal: PASS.
- Go VeraPort interoperability tests: PASS.
- DS216 ARMv7 `CGO_ENABLED=0` cross-build: PASS.
- VeraRelay preserved snapshot qualification: PASS.

Later evidence-only capture commit: `77f96d52a0f381256a36e4a0ee5baabeecc2732e`.

## Work-laptop qualification already complete

Two real user-run qualifications are durable:

1. `evidence/work-laptop-veraport-local-qualification-20260922.json`
   - qualified source: `13a16019f3ec45acd576eb22f75138e6820d6262`
   - PASS
   - real loopback TLS/application authentication
   - exact P-256 controller/workstation principals
   - data-plane verification
   - read-only `fs.read` capability ceiling
   - lane/fence open/read/close
   - sentinel-content match
   - no service install, firewall change, ProgramData write, or persistent identity

2. `evidence/work-laptop-veraport-process-qualification-20260922.json`
   - qualified source: `494914f97ad8d6e261e246e641d45e25f3394787`
   - PASS
   - real loopback TLS/application authentication
   - exact temporary `process.exec` ceiling
   - argv-vector execution of harmless cmd echo
   - return code 0
   - captured stdout token match
   - clean lane close
   - no service install or persistent identity

The work laptop is finished for now.

## Lappy state discovered at handoff

Durable evidence:
`evidence/lappy-existing-veraport-inspection-20260922.json`

This is a READ-ONLY INSPECTION snapshot, not a semantic qualification PASS.

Observed host:
- computer: `LAPPY`
- Windows 11 Home, build family `10.0.26200`

Observed VeraPort service:
- `VeraPortAgent` exists
- state: `Running`
- start mode: `Auto`
- account: `LocalSystem`
- service host: user-local Python 3.11 `pythonservice.exe`
- fixed class: `veraport_agent.windows_service.VeraPortWindowsService`

Observed config:
- `C:\ProgramData\VeraMesh\veraport.json`
- VeraPort binds only `127.0.0.1:17444`
- allowed roots: `C:\Users\patri`, `C:\Temp`
- process execution: disabled
- non-loopback VeraPort listener: disabled
- state DB: `C:\ProgramData\VeraMesh\state\agent.sqlite3`

Observed listeners:
- VeraPort Python service owns `127.0.0.1:17444`
- Tailscale owns `100.88.50.35:17444`
- Tailscale also owns `fd7a:115c:a1e0::e901:32df:17444`

This strongly suggests an existing Tailscale-facing edge path, but inspection alone does not qualify forwarding behavior or application authentication through that path.

The live SQLite state DB existed and was locked by the running process, so its hash was unavailable. Do not treat that as corruption.

Existing protected directories were observed with Administrators ownership and explicit SYSTEM/Administrators ACLs.

CRITICAL: do not run the fresh persistent installer over Lappy. The installer intentionally refuses existing service/root state. Reconcile/migrate the existing installation instead of clobbering it.

## Windows persistent installer now available

`tools/windows_install_veraport_service.ps1`

Current installer source pin:
`7ddd0451605357dbe5129702b8d8325a2d966076`

Properties:
- no Git install
- no system Python install
- pinned official Python 3.11.9 embeddable runtime
- pinned Windows runtime dependencies
- controller/workstation identity separation
- persistent VeraPort service under ProgramData
- Automatic SCM startup
- authenticated qualification before restart
- SCM restart
- authenticated qualification after restart
- same workstation principal required across restart
- no firewall mutation
- controller export remains staged until separately transferred
- fail-closed on existing installation state

This installer has passed the full disposable Windows CI path at exact code head `6869ab66...`, but Lappy already has an older live installation and therefore needs migration/reconciliation first.

## Current public-gateway architecture

Target path remains:

`ChatGPT phone/web -> stable HTTPS MCP/plugin endpoint on Synology -> OAuth resource-server boundary -> VeraMesh controller -> authenticated VeraPort stream -> workstation`

Synology remains the intended always-on gateway. No public endpoint, OAuth provider, plugin registration, tunnel, credential rotation, or firewall/Tailscale mutation has been performed.

The existing Synology Python 3.11 VeraMesh SPK remains an EDGE_STREAM transparent VeraPort edge, not VeraRelay.

VeraRelay remains a separate Node.js/SQLite DURABLE_RELAY concept/snapshot. Do not conflate it with the live Python edge or the Go public gateway.

## Go DS216 public gateway progress

Module: `gateway/veramesh-go`

Pinned official SDK:
`github.com/modelcontextprotocol/go-sdk v1.8.0`

The Go dependency graph is locked in `go.mod` / `go.sum`.

Implemented and tested source:
- pure-Go VeraPort TLS/P-256/framing client
- controller config loader
- persistent session controller
- public tool-policy parity with canonical JSON
- actor-bound public facade
- OAuth token-introspection verifier
- OAuth-scoped MCP tool server
- OpenAI compatibility shim that mirrors required `securitySchemes` to top level while retaining `_meta.securitySchemes`
- hostile tests for malformed/trailing MCP JSON and OAuth boundary
- ARMv7 no-CGO cross-build

Important remaining source gaps:
- `cmd/veramesh-gateway/main.go` is still a bootstrap; without `-version` it intentionally exits 64 and says the MCP service is not runnable.
- Go `ReadFrame` still uses ordinary `json.Unmarshal`; exact large JSON integers can become unsafe if decoded through generic `map[string]any`. Convert generic protocol decoding to `json.Decoder.UseNumber()` or typed integer fields before production.
- Go controller currently treats successful VeraPort Dial/application handshake as a live client and does not perform the Python controller's explicit `lane.list` data-plane probe before accepting the endpoint.
- Go controller does not currently reject/reconnect an already cached session merely because `binding.ExpiresAtMS` has elapsed before the next call.
- Go controller deliberately does not replay failed requests; preserve that invariant.
- Go controller currently selects the first dialable configured endpoint rather than implementing the Python mirrored-read/direct-edge path semantics.

Do not promote the Go gateway to runtime qualification until these are closed or explicitly narrowed.

## Public MCP / OAuth / plugin state

Python reference path already contains:
- public facade
- MCP server
- stateless Streamable HTTP
- RFC 9728 resource metadata
- strict host/origin controls
- OAuth metadata qualification
- RFC 7662 introspection verifier
- public gateway runtime
- plugin package renderer and skill/evals

Go path now has corresponding core MCP/OAuth pieces but not a runnable public `main`.

OAuth provider remains UNRESOLVED. Synology OAuth Service and Synology SSO Server were candidates; no provider has been accepted, configured, or changed.

No ChatGPT plugin has been registered or installed from this project.

## Important product/architecture correction

Do not restore the old assumption that Secure MCP Tunnel is the primary Plus/phone route.

The current target is the stable Synology public HTTPS MCP/plugin gateway. Secure MCP Tunnel remains an optional future route only.

## Existing Synology evidence

Prior durable evidence records a working Python edge/SPK path:
- package `0.1.0-0016`
- installed R6 source binding `84ba5e85639181c202f2030900a17df97e00df36`
- SPK SHA-256 `c8a634554e7e9ffdf89803818ea569f79379a3d0bda609b70cec3c0333b90b5f`
- local edge `127.0.0.1:17445`
- Tailscale Serve on 17445
- workstation TLS 1.3 through edge
- direct-vs-edge VeraPort certificate fingerprint match
- P-256 application mutual auth PASS
- authenticated read-only `lane.list` PASS

Later source hardening/UI heads are not covered by that R6 installed-runtime evidence.

## Next runnable frontier on Lappy

Start with read-only reconciliation, not installation.

1. Fresh-check this branch, PR #25, and current CI.
2. Read `evidence/lappy-existing-veraport-inspection-20260922.json`.
3. Read current `tools/windows_inspect_existing_veraport.ps1`, persistent installer, service qualifier, and current Windows service source.
4. Bind the installed Lappy package hashes/config/identity to the closest reconstructible Git source if possible. If exact source cannot be proven, classify it as historical installed runtime rather than inventing a binding.
5. Qualify the existing running VeraPort service semantically with its existing controller identity before changing anything.
6. Then qualify the observed Tailscale path `100.88.50.35:17444` from an independent controller side if available. Do not call the observed listener a forwarding PASS until this succeeds.
7. Only after the existing runtime is understood, design/execute a controlled migration to the current persistent runtime. Preserve the existing workstation identity if technically and cryptographically valid; do not rotate merely for convenience.
8. Process execution on Lappy is currently disabled. Enabling it is a protected authority expansion and needs Patrick's explicit live direction.
9. After Lappy persistence/network qualification, return to Go gateway hardening and wire a runnable Synology public MCP executable.

## Hostile-review stance to preserve

Use an explicit in-chat hostile reviewer periodically. Its job is to attack the current design and propose the smallest change that defeats the objection.

Immediate hostile position:

> The highest risk is no longer “can VeraPort run?” That is qualified. The risk is accidentally destroying or bypassing a functioning historical Lappy installation while chasing the new architecture. Reconstruct and qualify the live installation first; migration should be evidence-preserving and reversible.

## Authority / claim ceiling

No merge, production deploy, public endpoint creation, OAuth registration/configuration, firewall/Tailscale mutation, credential rotation, controller-key transfer, plugin registration, destructive cleanup, or replacement of Lappy's current service is authorized by this checkpoint.

Source, CI/build, installation, runtime, network reachability, public-gateway availability, and ChatGPT end-to-end behavior remain separate qualifications.

## Restore instruction

Treat this checkpoint as a starting snapshot, not current truth. Fresh-check the exact branch/PR/CI first. Do not rely on conversation memory when Git/evidence says otherwise.
