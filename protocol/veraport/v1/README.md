# VeraPort V1

VeraPort is a VeraMesh application protocol for persistent, parallel, capability-bounded workstation access. A "port" is a logical execution lane, not a TCP/UDP port.

One authenticated VeraMesh session may own many concurrent VeraPort lanes. Each lane binds a task, narrowed capabilities, resource claims, an expiring lease, and a fencing token. Independent lanes may execute concurrently. Overlapping claims reject when either side is a writer.

VeraRelay remains a blind durable courier/control plane. It does not execute commands and does not gain workstation authority.

Initial operations:
- `lane.open`
- `lane.renew`
- `lane.close`
- `lane.list`
- `fs.read_text`
- `fs.write_text`
- `process.exec`

Initial resource namespaces are `fs:<canonical-path>` and `cwd:<canonical-path>`. Parent claims cover descendants. Read/read overlap is permitted.

A lane can only narrow authority from its authenticated session ceiling. VLAN placement, transport membership, relay custody, pairing, or message possession does not create execution authority.

`process.exec` is deliberately treated as broad host execution authority. A `cwd:` claim constrains the selected working directory for collision/accounting purposes; it is **not** an OS sandbox for the child process. The reference agent therefore excludes `process.exec` from its default session ceiling and only enables it when the workstation operator starts the agent with the explicit local `--allow-process-exec` grant.

`reference/veraport_agent` is a local reference implementation. Its JSONL stdio adapter demonstrates multiplexed concurrent requests without exposing a network listener. The authenticated network/MCP bridge is the next implementation stage.
