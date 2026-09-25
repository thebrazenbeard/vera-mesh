---
name: remote-computer
description: Work with the user's enrolled computer through VeraMesh when the task requires reading, searching, editing, or running commands on that computer.
---

Use the VeraMesh MCP tools only for work the user asked to perform on their enrolled computer.

The public tools intentionally hide VeraPort lanes and fencing tokens. Never invent or ask the user for either value.

For filesystem work:
- Start with the narrowest read or search that can identify the target.
- Use absolute paths.
- Prefer `replace_text` when changing a known exact fragment; its expected-count check prevents an accidental broad edit.
- Use `write_file` when replacing or creating complete text content.
- Use `append_file` only when append semantics are actually intended.
- Do not expand beyond workstation-allowed roots.

For process work:
- Prefer `run_process` for bounded one-shot commands.
- Use `start_process` only when continued output or input is needed.
- Use the returned public process handle for status, output, input, termination, and release.
- Call `release_process` when a managed interactive process is no longer needed.
- VeraMesh controls only processes it started; do not assume host-wide PID authority.

For mutations:
- Treat a successful tool result as committed even when `_veramesh_cleanup` reports a later lane-cleanup problem. Do not blindly replay the mutation.
- If the operation itself returns an error, report that exact error rather than claiming the filesystem or process changed.

For connectivity:
- `computer_info` describes the authenticated workstation/path state.
- A reachable HTTPS endpoint is not proof that the Lappy operation ran; VeraPort application authorization remains authoritative.
- Durable VeraRelay custody, when later enabled, is not proof of execution.

When the requested task can be completed with read-only tools, do not request write or process authority.
