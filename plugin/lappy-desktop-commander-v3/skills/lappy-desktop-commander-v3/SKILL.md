---
name: lappy-desktop-commander-v3
description: Use when operating Patrick's Lappy through the self-hosted VeraMesh/VeraPort connector, including authorized filesystem work, searches, bounded commands, managed processes, and concurrent fenced lanes.
---

# Lappy Desktop Commander V3

Use Patrick's self-hosted VeraMesh/VeraPort connector only. The direct portable MCP server `lappy_veraport_direct` is the authoritative transport for this plugin. Its live `tools/list` is the client-visible schema; do not route through the hosted Remote Desktop Commander service or the stale V2 Apps SDK mapping.

## Connection and authority

- Establish reachability with machine/session information before claiming Lappy is available.
- Tool discovery is not authority. Respect workstation roots, controller capabilities, lane claims, fencing tokens, and process policy.
- The authoritative Vera workspace is `D:\\Vera` / `D:\\VERA`; treat `C:\\Vera` as obsolete unless explicitly historical.
- WorkBridge materials live under `C:\\ProgramData\\WorkBridgeMCP` when needed.

## Filesystem work

- Open the narrowest useful lane and claim before file operations.
- Prefer read/search/stat before mutation.
- Preserve unrelated work and use the smallest coherent edit.
- Verify mutations by readback or another authoritative observation.

## Process work

- Prefer `process.exec` for bounded one-shot commands.
- Use `process.start` only when a managed long-running process is actually needed.
- For managed processes, use status/output/input tools through the same owning lane and fence.
- Terminate only the process handle owned by the active lane/fence.
- Do not treat command dispatch as success; inspect exit/result/output before claiming completion.

## Concurrency contract

- VeraPort logical scheduling supports up to 32 fenced logical lanes when the live service reports `max_lanes=32`.
- Desktop Commander's bounded process-admission overlay permits at most 4 process launches to execute concurrently.
- The 32 logical-lane ceiling and 4 process-worker ceiling are different layers; never flatten or substitute one for the other.
- More than four logical lanes may remain active while process work queues behind the four-worker admission gate.
- Use distinct lane IDs and fencing tokens for non-colliding work.
- Keep heavyweight 4B/3B model stages serial unless memory evidence supports otherwise.

## Tool-surface verification

- `machine_info` capability/gateway readback and the ChatGPT-visible tool catalog are separate evidence.
- The direct MCP server's live `tools/list` is the source of truth for the client-visible schema.
- If VeraPort advertises process operations but the direct MCP surface does not expose them, classify the client schema as stale and do not claim process execution succeeded.
- Do not use the hosted Remote Desktop Commander service as a bypass.

## Protected effects

Do not merge or mutate canonical `main`, deploy/cut over unrelated production services, change credentials/permissions/trust, incur paid compute, destroy state, or publish private material without Patrick's explicit authority for that effect.

## Secrets

Never expose bearer tokens, runtime keys, tunnel IDs, capability URLs, private keys, session credentials, or other secret material.
