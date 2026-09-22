# VeraMesh RDC replacement evaluation — 2026-09-22

Status: SOURCE WORKING / NOT INSTALLED / NOT CURRENT CHATGPT ROUTE

## Objective

Replace the practical Remote Desktop Commander workflow with a zero-new-spend,
source-controlled VeraMesh path while preserving a stricter authority model:

`ChatGPT/App -> remote MCP surface -> VeraPort controller -> DIRECT_STREAM or VeraRelay EDGE_STREAM -> Lappy VeraPort service`

The chat being open on Lappy is intentionally irrelevant. The workstation bridge is a
persistent Windows service.

## Current external comparison

Research checked on 2026-09-22:

- Remote Desktop Commander: installed ChatGPT plugin plus a workstation agent. Useful
  surface includes filesystem reads/writes/search, process execution/control, device
  status/config, and product-specific helpers. If its foreground agent is stopped, the
  device becomes unreachable.
- `leonovee/winfs-mcp`: Windows filesystem/process/git-oriented MCP with allowed roots,
  hard bounds, atomic writes, process control, and a large local tool surface.
- `deploymenttheory/windows-mcp-server`: broad Windows UI/system automation including
  accessibility-tree UI control, screenshots, PowerShell, registry, filesystem,
  processes, services, network diagnostics, policy gating, and tamper-evident audit.
  Its released server is primarily a local stdio surface.
- `openhammer.dev`: deliberately small authenticated filesystem + shell MCP surface
  with workspace scoping.
- `venkey123456789/local-computer-full-access-mcp`: Windows-first filesystem, shell,
  drive, and process surface with local audit and an HTTP/tunnel option; authority is
  broad under the account running it.
- `modelcontextprotocol/servers` filesystem server: useful local allowed-directory
  filesystem reference, not an RDC-equivalent remote workstation fabric.

These projects are implementation references, not authority dependencies.

## VeraMesh differentiators that must not be traded away for parity

- cryptographic controller/workstation application identity above transport;
- session capability ceilings;
- per-lane capabilities and resource claims;
- fencing tokens and lease expiry;
- durable mutation request identity/idempotency;
- direct/edge route separation and ambiguous-mutation fail-closed behavior;
- allowed workstation roots;
- local opt-in for process authority;
- opaque process handles bound to the owning lane/fence;
- process watchdog termination when lane authority expires;
- transport/path reachability never grants application authority;
- VeraRelay live edge does not terminate VeraPort TLS or see plaintext commands.

## Current replacement surface

Implemented and CI-covered:

- machine/session/path info;
- lane open/list/renew/close;
- bounded text reads;
- version-bound ranged byte reads;
- file metadata;
- bounded directory listing;
- bounded path search;
- atomic text writes plus append;
- native directory creation, move/rename, and expected-count text replacement;
- bounded one-shot argv process execution;
- managed process start/list/status/output/input/terminate;
- automatic managed-process reap on lane close or application-session disconnect;
- direct TLS hot path;
- transparent VeraRelay/VeraMesh live edge carrying the same end-to-end TLS session;
- MCP Streamable HTTP adapter over the controller runtime;
- persistent Windows Service host for Lappy.

## Deliberate non-parity / remaining work

Not required for the first useful replacement cut:

- RDC product telemetry, prompts, feedback, or usage-account helpers;
- format-specific PDF/DOCX/XLSX convenience transforms.

Useful parity still missing:

- multi-file reads;
- host-wide process inventory/kill separate from VeraPort-managed children;
- optional recursive directory-tree convenience surface.

Potential later extension, informed by other Windows MCP projects:

- screenshot/UI observation and Windows accessibility-tree control.

These later features must remain capability-gated and must not bypass VeraPort claims,
fencing, process ownership, or local workstation policy.

## ChatGPT route constraint

ChatGPT cannot directly consume a local MCP listener. The source MCP adapter remains
loopback-only by default. A supported remote MCP route or Secure MCP Tunnel is a separate
installation/runtime effect. Source presence does not imply plugin registration or a
current ChatGPT route.

## Exact current qualification ceiling

A green source/CI run proves only the exact source under test. It does not prove:

- Lappy production installation;
- VeraRelay/Synology deployment;
- public/private tunnel setup;
- ChatGPT app/plugin registration;
- end-to-end operation from a live ChatGPT tool call;
- production cutover from RDC.

Those are separate gates.
