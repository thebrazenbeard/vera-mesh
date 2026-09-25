# VeraMesh chat continuation — 2026-09-22 V2

Status: `CURRENT WORKING SNAPSHOT — FRESHNESS REQUIRED BEFORE EFFECT`

## Canonical subject

Repository: `thebrazenbeard/vera-mesh`

Active branch: `work/veramesh-rdc-replacement-v1-20260922`

Draft PR: `#25 — Build persistent VeraMesh replacement surface for RDC`

Do not merge, deploy, install, rotate credentials, change firewall/Tailscale/provider state, expose a public endpoint, register a ChatGPT plugin, enable Lappy process execution, or replace the existing Lappy service without Patrick's exact live authority.

## Exact code-qualified head

Exact code head: `66eca84708bb16d85556560f18143e2a5ada2d0a`

At this exact head:
- VeraPort reference workflow #416: PASS.
- VeraRelay 0.4 snapshot workflow #99: PASS.
- Python 3.11 / 3.12: PASS.
- Go VeraPort interoperability tests: PASS.
- DS216 ARMv7 `CGO_ENABLED=0` gateway cross-build: PASS.
- Windows reference/service/install/restart qualification lane: PASS.
- VeraRelay preserved snapshot qualification: PASS.

Later documentation/evidence-only commits do not transfer this exact-head qualification automatically.

## Go gateway changes completed in this continuation

The Go DS216 gateway is no longer the V1 bootstrap state.

Completed:
- exact generic JSON integer preservation with `json.Decoder.UseNumber()`;
- strict rejection of trailing/multiple JSON values;
- stricter Windows/UNC path semantics;
- cached VeraPort session expiry enforcement;
- mandatory read-only `lane.list` data-plane probe before caching a session;
- multi-path DIRECT_STREAM / EDGE_STREAM pool;
- direct-first path semantics with RTT ordering within a mode;
- logical read-lane mirrors and read failover across authenticated current paths;
- no transparent replay of ordinary writes/process mutations after ambiguous transport failure;
- reserved request-field override rejection preserved;
- lock-order repair in controller close/invalidation path;
- runnable loopback-only Go HTTP/MCP gateway main;
- OAuth introspection configuration loader and verifier;
- RFC 9728 protected-resource metadata;
- exact public Host enforcement;
- per-tool OAuth scope enforcement;
- OpenAI security-scheme compatibility shim;
- logical read fence hardening across gateway restarts.

Logical read fences now use a random 31-bit gateway-incarnation epoch plus a 21-bit monotonic counter. Externally visible tokens remain <= 2^52-1 for exact JavaScript/JSON representation and fail closed on per-incarnation counter exhaustion.

No Go gateway has been installed or started on the NAS.

## Lappy state

Durable evidence remains:
- `evidence/lappy-existing-veraport-inspection-20260922.json`
- `evidence/lappy-existing-veraport-source-binding-20260922.json`

Bounded source reconstruction:
- seven inspected VeraPort implementation files on Lappy exactly match Git source at `07ff6643b7aec5e47106b6f58e2bf42ec38d820f` after deterministic LF -> CRLF normalization.
- This is a seven-file exact binding, not whole-package provenance.

Observed historical/live-at-inspection service:
- service `VeraPortAgent`;
- Running, Automatic, LocalSystem;
- loopback VeraPort `127.0.0.1:17444`;
- observed Tailscale listener `100.88.50.35:17444`;
- process execution disabled.

During this continuation Lappy was offline to Remote Desktop Commander, so no new semantic runtime or Tailscale-path qualification was performed. Do not run the fresh installer over the existing installation.

## Synology readback — current evidence

Durable evidence:
- `evidence/synology-readonly-inspection-20260922.json`
- `evidence/synology-runtime-owner-source-binding-20260922.json`

Observed 2026-09-22:
- host: `TheSimsVault`;
- DSM 7.2.2-72806 Update 9;
- Linux 3.10.108 armv7l;
- Python 3.11.15;
- Node.js 22.22.3;
- `/var/packages/VeraMesh/target` exists;
- Synology Package Center reports VeraMesh stopped;
- a live process exists:
  `/var/packages/python311/target/bin/python3.11 /var/packages/VeraMesh/target/bin/veramesh_edge.py serve`;
- loopback `127.0.0.1:17445` is listening;
- Tailscale IPv4/IPv6 listeners on 17445 are present;
- Tailscale Serve current config explicitly forwards TCP 17445 to `127.0.0.1:17445`;
- `127.0.0.1:17443` is also listening but its owner/semantics are not yet qualified.

Package-manager state and observed process/socket state therefore diverge.

### Synology installed-source binding

Five observed installed files exactly match `thebrazenbeard/vera-synology@553e3a637833c5469f6b99fbc4d597755a0c9a5e`:
- `veramesh_edge.py`;
- `veramesh_lifecycle.py`;
- `veramesh_start_readiness.py`;
- `veramesh_state.py`;
- `pkguser-veramesh.service`.

That source head declares package version `0.1.0-0017`.

Claim ceiling: this is an exact five-file source binding only. Live package metadata version and whole-package identity still require readback.

The first ownership probe incorrectly treated permission-sensitive private paths as literal absence and called unsupported `synosystemctl status`. The corrected probe is `tools/synology_runtime_owner_probe.sh` as of `22826574fd32ac524fde9f7e581a68372821af66` and later heads. It now reads package metadata, uses Synology's supported get-* verbs, attempts PID-to-unit binding, and reports private-path observations as unresolved when the non-root user cannot traverse them.

## Historical Synology qualification versus current state

Historical R6 evidence remains valid only for its exact subject:
- package 0.1.0-0016;
- R6 source binding `84ba5e85639181c202f2030900a17df97e00df36`;
- local edge/Tailscale Serve/TLS/P-256/lane.list PASS at that historical time.

Current readback must not inherit that PASS automatically. The current installed files partially bind to later source `553e3a...`, Package Center says stopped, while an edge process and forwarding sockets are live. Current semantic lifecycle state is therefore not yet qualified.

## Next safe executable frontier

1. Run the corrected read-only Synology ownership probe as the existing non-root SSH user.
2. Cross-bind live package INFO version, supported unit state, PID -> unit mapping, process identity, listeners, and Tailscale forwarding.
3. If private lifecycle/control-socket state remains inaccessible, perform only a package-user/root read-only semantic status probe; do not start/stop/restart the package.
4. Once current edge semantics are proven, decide whether the historical Python SPK should remain as EDGE_STREAM while the new Go MCP/OAuth gateway is staged separately.
5. Do not install the Go gateway, mutate Synology reverse proxy/Tailscale, configure OAuth, or expose a public endpoint without Patrick's exact authority.
6. When Lappy becomes reachable, semantically qualify its existing VeraPort identity and Tailscale path before migration.

## Target topology

`ChatGPT phone/web -> stable Synology HTTPS MCP endpoint -> OAuth -> Go VeraMesh controller -> authenticated VeraPort -> workstation`

The current Synology Python SPK is an EDGE_STREAM carrier, not the new public Go MCP gateway. VeraRelay remains a separate DURABLE_RELAY subject.

## Hostile-review stance

> A live process plus a Tailscale forwarding rule is not a healthy installed service. The strongest current evidence is that package-manager state, process state, and private lifecycle evidence disagree. Resolve that disagreement read-only before any package or network mutation.

> The new Go gateway is source/build-qualified, not NAS-installed or public-E2E-qualified. Do not turn green CI into a deployment claim.

## Authority / claim ceiling

No merge, NAS install/deploy, package start/stop/restart, public endpoint creation, reverse-proxy mutation, OAuth provider configuration, firewall/Tailscale mutation, credential rotation, controller-key transfer, ChatGPT plugin registration, Lappy process-execution enablement, destructive cleanup, or Lappy service replacement is authorized by this checkpoint.

Source, build/CI, installation, runtime, network reachability, semantic data-plane qualification, OAuth/public gateway availability, and ChatGPT end-to-end behavior remain separate states.

## Restore instruction

Treat this V2 checkpoint as a starting snapshot, not current truth. Fresh-check PR #25 exact head/CI and read the named evidence before effects.
