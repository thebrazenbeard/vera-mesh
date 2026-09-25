# VeraMesh architecture reconsideration — 2026-09-22

Status: CURRENT DESIGN DIRECTION / SOURCE-ONLY / NO DEPLOYMENT EFFECT

## Trigger

This reconsideration incorporates:

- the working VeraPort reference implementation and PR #25;
- the production-verified VeraMesh Synology Python edge;
- the preserved VeraRelay Node.js/SQLite snapshot;
- current ChatGPT plugin and custom-MCP product constraints;
- current OpenAI public plugin requirements;
- Remote Desktop Commander parity work;
- `gabrielekarra/mcp-anything@a7a45a537709ca6c9e79c3739508fef7b3ba90af`;
- `atom2ueki/mcp-server-synology@12ee56270abc538e31f3b38e49afb7c1891c12ed`;
- `meonji-mon/MCP-SynoLink@8705c86928c4d91dbf37e8b31b09e1c2dda7aa45`;
- `cmeans/mcp-synology@76be3887cc45122f78cf47b25541c09df658dfc1`.

The goal is still a zero-new-spend practical replacement for RDC from ChatGPT,
including phone use, without depending on a foreground terminal on Lappy.

## Corrected component model

### Lappy: VeraPort workstation authority

Lappy should run only the persistent VeraPort workstation service required for
filesystem/process authority.

It owns:
- workstation application private identity;
- local controller trust;
- allowed filesystem roots;
- process opt-in policy;
- lane/capability/fencing enforcement;
- durable mutation idempotency;
- managed-process ownership and cleanup.

It should not need:
- a ChatGPT window;
- an RDC PowerShell process;
- a local MCP server for the normal phone path;
- a Secure MCP Tunnel runtime for the normal Plus path.

### Synology: always-on VeraMesh gateway

The Synology should be the always-on control/gateway host.

It already has a proven Python 3.11 VeraMesh SPK execution substrate.

The target gateway owns:
- the VeraPort controller private identity;
- current path/session health to Lappy;
- the remote MCP/plugin server;
- plugin-user authorization;
- request logging/metrics appropriate for plugin review;
- optional direct use of DSM APIs for NAS-specific tools;
- private-network reachability to Lappy.

The normal live path becomes:

```text
ChatGPT phone/web
       |
       | public, authenticated HTTPS MCP
       v
VeraMesh Plugin Gateway on Synology
       |
       | VeraPort application-authenticated private stream
       v
Lappy VeraPort Windows service
       |
       v
allowed filesystem/process authority
```

This avoids:
- ChatGPT -> Lappy tunnel -> controller -> Synology -> Lappy loops;
- keeping an MCP process alive on Lappy;
- making the Synology live edge a mandatory extra hop when the controller itself
  already runs on Synology.

### VeraMesh Synology Python edge: retained, not mandatory

The existing VeraMesh SPK live proxy remains useful for:
- alternate controllers;
- conformance testing;
- explicit EDGE_STREAM routes;
- tailnet routing/failover experiments.

It is not the mandatory hot path for a Synology-hosted controller.

Historical R6 production runtime evidence remains bound only to its exact installed
source/package subject.

### VeraRelay: durable courier only

VeraRelay is separate from the Python SPK.

Preserved snapshot evidence:
- runtime family: Node.js >=22;
- package metadata still says `vera-relay@0.3.0`;
- the preserved 0.4 candidate archive passed 31/31 tests on Node 22.23.2 in CI;
- SQLite paths exercise WAL/FULL synchronous/foreign keys, atomic pairing, atomic
  message/nonce state, idempotency, audit integrity, and queue authentication.

The 0.4 design/candidate label MUST NOT be promoted to a released 0.4.0 runtime claim
until package/version/source/install evidence says so.

VeraRelay's role is:
- offline custody;
- durable sealed mailbox;
- receipts/reconciliation;
- asynchronous fallback when Lappy is unavailable.

It is not:
- the interactive live proxy;
- evidence that a queued request executed;
- a replacement for VeraPort authorization.

## ChatGPT product reality

Private developer-mode full-MCP write actions are currently not the practical target
for the user's personal Plus plan.

Therefore Secure MCP Tunnel is no longer the primary RDC replacement route.

The Plus-compatible target is a publicly published plugin using a stable public HTTPS
MCP endpoint. Public plugin submission requires the endpoint to remain publicly
reachable for review/domain verification.

The Secure MCP Tunnel implementation in this branch is retained only as an optional
future Business/Enterprise/Edu/API/Codex integration subject until there is a reason to
keep or remove it. It is not part of the minimum Plus cutover.

## Public gateway security

A public PC-control endpoint MUST NOT rely on:
- URL secrecy;
- IP allowlisting alone;
- a static custom API key from ChatGPT;
- Funnel/HTTPS alone;
- the model deciding whether a caller is authorized.

The production plugin gateway must support:
- OAuth 2.1 end-user authorization and scopes;
- per-tool MCP security metadata;
- bearer-token validation on every protected call;
- OpenAI-managed mTLS verification when deployed at the TLS termination layer where
  that evidence is available;
- read/write/destructive annotations matching actual behavior;
- a read-only authenticated profile tool for account identity;
- endpoint/domain verification required by plugin submission.

The OAuth provider should be an established, reviewed implementation rather than a
home-grown authorization server unless no viable provider exists.

## Public exposure

Tailscale Funnel is useful for development because it supplies a stable-looking public
HTTPS URL and is available on all current Tailscale plans. It remains a beta public
tunneling mechanism, and OpenAI public-plugin guidance requires a stable public endpoint
rather than a temporary tunnel.

Therefore:
- Funnel MAY be used for local/review rehearsal when explicitly authorized;
- Funnel MUST NOT be assumed acceptable for public marketplace submission;
- final exposure remains replaceable: DSM reverse proxy/DDNS, another already-owned
  stable HTTPS origin, or another zero-new-spend path that satisfies plugin review.

No Funnel/public exposure is authorized by this document.

## External projects: adopt ideas, not authority

### mcp-anything

Useful:
- SKILL.md generation patterns;
- eval-case/conformance generation;
- grouped ergonomic tool design;
- scanner coverage for Python/socket/protocol APIs;
- package-generation ideas.

Do not use as the workstation authority/runtime wrapper because its generated
`python_call` model can directly import and execute target functions. That would bypass
VeraPort's explicit application identity, capability ceiling, claims, fencing,
idempotency, and managed-process ownership.

It also currently targets a newer FastMCP/MCP dependency family than the existing
VeraPort adapter. Any SDK migration must be deliberate, tested, and independent from
the authority model.

### cmeans/mcp-synology

Best architectural reference among the reviewed Synology MCP projects for:
- modular tool registration;
- read/write/admin permission tiers;
- 2FA/remembered-device auth lifecycle;
- OS keyring credential handling;
- per-NAS identity/instructions;
- structured error behavior;
- DSM File Station search/task cleanup;
- strong test/type/coverage discipline.

Potential source/reference for DSM-client functionality only.

### atom2ueki/mcp-server-synology

Best breadth/currentness reference for:
- File Station;
- Download Station;
- system/disk/volume/network health;
- iSCSI/SAN;
- Container Manager;
- NFS/users and other DSM-specific operations;
- current Streamable HTTP operational experience.

Its own remote HTTP documentation explicitly says it has no application-level
authentication. That makes its HTTP server unsuitable as the public VeraMesh PC-control
gateway without an independent authorization boundary.

Potential source/reference for DSM API coverage, not public-gateway security.

### MCP-SynoLink

Useful as a small/simple comparison implementation. It is older and materially less
complete than the two projects above, so it should not become a dependency unless a
specific implementation detail proves uniquely valuable.

## What VeraMesh should keep custom

Keep custom:
- VeraPort identities/handshake;
- capability ceilings;
- lane/resource claims;
- fencing;
- request idempotency;
- process ownership/watchdogs;
- cross-path failover semantics;
- public plugin gateway authorization binding to VeraPort authority;
- exact source/runtime/effect qualification.

Do not custom-rebuild without need:
- generic DSM API client behavior already well covered by mature projects;
- generic MCP code generation;
- Synology File Station search/pagination semantics;
- basic plugin SKILL/eval scaffolding.

## Minimum production topology

Required:
1. Lappy VeraPort Windows service.
2. Synology VeraMesh Plugin Gateway using Python 3.11.
3. Private Synology -> Lappy VeraPort reachability.
4. Stable public HTTPS MCP exposure.
5. OAuth 2.1 user binding and per-tool authorization.
6. Public plugin package/metadata/evals and review acceptance.

Optional:
- VeraMesh SPK live edge as alternate EDGE_STREAM.
- VeraRelay durable fallback.
- Secure MCP Tunnel for other OpenAI products/plans.

## Qualification sequence

1. SOURCE_GATEWAY_READY
2. CROSS_PLATFORM_CI_PASS
3. SYNOLOGY_PACKAGE_BUILD_PASS
4. SYNOLOGY_PRIVATE_GATEWAY_RUNTIME_PASS
5. LAPPY_VERAPORT_RUNTIME_PASS
6. PRIVATE_NAS_TO_LAPPY_E2E_PASS
7. PUBLIC_HTTPS_AUTH_GATE_PASS
8. PLUGIN_REVIEW_SUBJECT_READY
9. PLUGIN_ACCEPTED_AND_INSTALLABLE_ON_TARGET_ACCOUNT
10. PHONE_END_TO_END_ACCEPTED
11. RDC_RETIRED

No earlier state implies a later state.
