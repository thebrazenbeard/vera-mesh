# VeraMesh ChatGPT / RDC cutover contract — 2026-09-22

Status: SOURCE CANDIDATE / INSTALL AND CUTOVER NOT AUTHORIZED

## Objective

Replace the practical Remote Desktop Commander path without requiring a foreground
PowerShell window and without making ChatGPT itself a workstation authority.

Primary Plus/phone topology:

```text
ChatGPT phone/web
       |
       | public HTTPS MCP + OAuth
       v
VeraMesh Public Gateway on Synology
       |
       | private VeraPort application-authenticated stream
       v
Lappy VeraPort Windows service
       |
       v
allowed filesystem/process authority
```

The ChatGPT conversation may be closed on Lappy. No browser/app window or foreground
terminal on Lappy is part of the liveness contract.

The gateway hides VeraPort lane IDs and fencing tokens from ChatGPT. Public tools use
ergonomic file/process parameters; the gateway creates least-authority internal lanes
and maps public process handles to actor-bound internal process leases.

## ChatGPT route selection

The current personal Plus target is the published-plugin path: a stable public HTTPS
MCP resource server on the always-on Synology gateway with OAuth user authorization.

The previously built Secure MCP Tunnel / Lappy-side stdio route remains source-supported
as an optional future Business/Enterprise/Edu/API/Codex route. It is not the minimum
Plus cutover and is no longer the primary topology.

The public gateway:
- binds only to loopback behind a reviewed Synology HTTPS reverse proxy;
- publishes RFC 9728 protected-resource metadata;
- emits per-tool OAuth `securitySchemes` plus the compatibility `_meta` mirror;
- requires a verified OAuth resource-owner subject and declared scopes before tool calls;
- keeps OAuth token issuance outside VeraMesh and accepts an injected verifier for an
  established authorization server;
- enforces exact public Host/origin policy at the MCP transport;
- forwards authorized calls through VeraPort rather than importing workstation code.

Synology OAuth Service and Synology SSO Server are zero-new-spend authorization-server
candidates. Neither is accepted until live metadata/flow qualification proves the
required PKCE/client-registration or preconfigured-client behavior, resource binding,
subject, expiry, audience, and scopes.

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
