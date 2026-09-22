# VeraMesh ChatGPT / RDC cutover contract — 2026-09-22

Status: SOURCE CANDIDATE / INSTALL AND CUTOVER NOT AUTHORIZED

## Objective

Replace the practical Remote Desktop Commander path without requiring a foreground
PowerShell window and without making ChatGPT itself a workstation authority.

Target topology:

```text
ChatGPT
  |
  | Secure MCP Tunnel
  v
OpenAI tunnel runtime on Lappy
  |
  | stdio: veraport-mcp-stdio
  v
VeraPort ControllerRuntime
  |\
  | \__ EDGE_STREAM -> VeraMesh Python SPK live edge -> Lappy VeraPort
  |
  \____ DIRECT_STREAM -------------------------------> Lappy VeraPort
                                                         |
                                                         v
                                              allowed filesystem/process
```

The ChatGPT conversation may be closed on Lappy. No browser/app window on Lappy is
part of the liveness contract.

## OpenAI tunnel-client source binding used for this integration

Repository: `openai/tunnel-client`

Inspected exact commit:
`cce7a8226654c432ce53c7e05e17a9825a34b6e3`

The inspected client provides:
- Windows amd64/arm64 support;
- `runtimes connect` for a managed detached local runtime;
- persisted process identity rather than trusting a bare PID;
- `runtimes status <alias> --json` with explicit `process_running`,
  `healthy`, and `ready` fields;
- `runtimes stop` for local runtime teardown;
- runtime API-key references using `env:NAME` or `file:/path`;
- explicit profile/state roots;
- local stdio MCP targets.

VeraMesh does not vendor or silently modify tunnel-client. A different tunnel-client
version is a different runtime subject and must be compatibility-checked.

## VeraMesh tunnel service role

`VeraMeshTunnelRuntime` is a Windows SCM wrapper around the official managed-runtime
interface. It does not reimplement the tunnel.

Fixed service config:
`C:\ProgramData\VeraMesh\tunnel-runtime.json`

The service:
1. validates its source-controlled config shape and required local files;
2. validates the LocalSystem ACL profile for config, tunnel runtime key, VeraPort
   controller config/identity material, and state/profile directories;
3. verifies pinned SHA-256 identities for both `tunnel-client.exe` and the
   `veraport-mcp-stdio` launcher before privileged execution;
4. invokes `tunnel-client runtimes connect` with an existing tunnel ID;
5. passes the runtime key only as `file:<protected path>`, never as a literal argv
   value and never through `CONTROL_PLANE_API_KEY` / `OPENAI_API_KEY`;
6. supplies `VERAPORT_CONTROLLER_CONFIG` and stdio mode to the spawned VeraPort MCP;
7. verifies `process_running=true` and `healthy=true`;
8. periodically checks health and reconnects an unhealthy managed runtime;
9. invokes `runtimes stop` during SCM shutdown.

`ready` is recorded separately. A tunnel can be locally running/healthy before the
ChatGPT-side connector/app has completed every readiness condition.

## Why managed stdio is preferred for this cut

The tunnel runtime launches `veraport-mcp-stdio` directly. This avoids:
- a public inbound listener on Lappy;
- a second local HTTP listener solely for ChatGPT ingress;
- exposing the tunnel runtime API key to VeraPort through environment inheritance;
- tying VeraPort liveness to a foreground PowerShell process.

VeraPort itself still owns controller/workstation authentication, capability ceilings,
lane claims, fencing, mutation idempotency, and operation policy. Tunnel possession is
not VeraPort authorization.

## VeraMesh Synology edge vs VeraRelay

These are separate components and runtimes.

**VeraMesh Synology SPK** is the live `EDGE_STREAM` implementation. Its package scripts
run Python 3.11 from the installed Synology Python package, and its live edge forwards
opaque VeraPort TLS bytes without terminating VeraPort application security. The
production-verified R6 SPK is historical runtime evidence; later source heads require
their own rebuild/install/readback before inheriting that qualification.

**VeraRelay 0.4** is the separate Node.js 22 + SQLite durable authenticated courier.
It is a `DURABLE_RELAY` / sealed-mailbox role, not the interactive live proxy. Queue
custody never means the Lappy operation executed.

Preferred path semantics:
1. `DIRECT_STREAM` — live VeraPort connection.
2. `EDGE_STREAM` — live VeraPort connection through the VeraMesh Python SPK.
3. `DURABLE_RELAY` — Node.js VeraRelay only after its separate source/runtime
   qualification; asynchronous custody only.

The preserved historical VeraRelay predecessor is not accepted as the new durable
runtime until its authorization/idempotency/audit defects are repaired or superseded.

## RDC replacement boundary

The first cut now natively covers the everyday RDC surface needed for VeraMesh work:
- machine/session/path state;
- bounded text and ranged-byte reads;
- stat, listing, path search;
- write, append, directory creation, move/rename, exact-count text replacement;
- bounded one-shot argv execution;
- lane-owned managed process start/list/status/output/input/terminate;
- automatic process cleanup when lane/session authority ends;
- persistent Windows VeraPort service;
- direct and VeraMesh-SPK live edge paths;
- separate Node.js VeraRelay durable-courier role retained outside the interactive hot path;
- ChatGPT MCP adapter with read-only tool annotations;
- managed Secure MCP Tunnel runtime source support.

RDC-specific telemetry, onboarding prompts, feedback, PDF convenience generation, and
similar product helpers are not replacement requirements.

Host-wide arbitrary PID kill/list is deliberately not copied into the initial cut.
VeraMesh controls processes it started. Broader host-process authority requires a
separate capability and threat review.

## Zero-new-spend gate

All VeraMesh source work and current public-repository CI are being kept on the
zero-new-spend path.

This document does **not** claim that use of OpenAI Secure MCP Tunnel, runtime API keys,
or any future hosted component is free for this account. Current product eligibility and
billing/usage terms must be read back before cutover. If that route would create a new
charge, the cutover remains blocked and an already-owned/public endpoint route must be
used instead.

No paid service may be enabled merely because this source supports it.

## Acceptance states

These states are separate:

1. `SOURCE_READY` — exact branch source complete and reviewed.
2. `CI_PASS` — exact source passes Linux/Windows test subjects.
3. `LAPPY_QUALIFIED` — exact source/build passes real work-laptop SCM/runtime tests.
4. `INSTALLED` — production VeraPort + tunnel runtime services installed.
5. `CHATGPT_CONNECTED` — ChatGPT developer-mode app/tunnel is connected to the
   installed VeraMesh MCP surface.
6. `END_TO_END_ACCEPTED` — live ChatGPT calls prove read/write/process operations,
   reconnect, service restart, lane/fence enforcement, and VeraMesh SPK edge fallback.
7. `RDC_RETIRED` — RDC is no longer required for the accepted workflows.

No earlier state implies a later one.

## Protected-effect boundary

This branch does not:
- install either Windows service on Lappy;
- create or modify an OpenAI tunnel;
- create a runtime/API key;
- register a ChatGPT developer-mode app;
- change firewall/Tailscale/network policy;
- deploy/update the VeraMesh Synology SPK;
- deploy/activate VeraRelay Node.js durable-courier runtime;
- merge PR #25;
- uninstall or retire RDC.

Those effects require separate live authority and readback.
