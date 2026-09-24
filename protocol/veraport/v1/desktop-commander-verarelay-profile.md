# VeraRelay Desktop Commander transparent profile V1

Status: `SOURCE_CANDIDATE / LIVE_BYTE_TRANSPARENCY_TESTED`

## Purpose

VeraRelay may carry the Desktop Commander MCP stream only as an opaque bidirectional byte transport. It is not a Desktop Commander policy layer and does not interpret tool calls.

Reference command:

```text
verarelay-desktop-commander --listen-host 127.0.0.1 --listen-port 17447 --upstream-host 127.0.0.1 --upstream-port <desktop-commander-mcp-port>
```

## Invariant

For every admitted connection, request bytes sent downstream must reach the upstream Desktop Commander endpoint unchanged, and response bytes must return unchanged. This includes `tools/call` requests for `start_process` with arbitrary command strings.

The relay MUST NOT:

- rename tools;
- inspect or rewrite `command` strings;
- replace Desktop Commander process semantics with VeraPort/WorkBridge grant semantics;
- strip tools from `tools/list`;
- reinterpret results/errors/notifications;
- replay a mutation after an ambiguous transport failure.

Connection count, chunk size, and connection timeout are transport resource bounds only. They do not alter the MCP payload.

## Source

`reference/veraport_agent/veraport_agent/desktop_commander_relay.py` is a thin named carrier over the existing byte-transparent VeraMesh live-edge implementation. Its tests send a real Desktop Commander-shaped `start_process` JSON-RPC frame containing a PowerShell command and require exact request and response bytes.

Durable VeraRelay mailbox/custody semantics remain separate from this live stream. If durable store-and-forward is later used for MCP calls, it must preserve the same opaque payload invariant and must not automatically replay ambiguous mutations.
