# WorkBridge / VeraMesh integration V2

Source compatibility subject:

- WorkBridge repository: `thebrazenbeard/WorkBridgeMCP`
- WorkBridge PR: #5
- exact interface head: `0b80fe050d8f03da54ee14123737af26740c1605`
- VeraMesh successor base: PR #25 @ `60d233c8ccc25871b0666d00c53fa9be26c7cc74`

This adapter is an optional private workstation backend under the existing VeraMesh public OAuth gateway. It is separate from the Secure MCP Tunnel bootstrap path used by `veraport-mcp-stdio`.

Security invariants:

- VeraMesh public OAuth terminates at VeraMesh; the user OAuth token is never forwarded to WorkBridge.
- WorkBridge uses a separate bearer secret named by `bearer_token_env`.
- A WorkBridge backend can implement an already-authorized VeraMesh public tool, but cannot cause a tool or OAuth scope to become public.
- `allowed_roots` is mandatory in the VeraMesh WorkBridge upstream configuration and is an explicit integration-side filesystem ceiling in addition to WorkBridge's own configured roots.
- Read-only WorkBridge failures may fall back only to an independently supported VeraPort operation.
- After a WorkBridge mutation attempt, failure is returned; VeraMesh never transparently replays the mutation against VeraPort.
- Only semantics-preserving overlaps are mapped: UTF-8 read/write, stat, bounded directory pagination, and non-parent mkdir.
- WorkBridge process execution is not mapped into the VeraMesh public process surface.

This binding qualifies source compatibility only. It does not establish that the exact WorkBridge binary is installed, that WorkBridge and VeraPort runtime root sets are equivalent, that either backend is currently selected, or that a workstation effect occurred.
