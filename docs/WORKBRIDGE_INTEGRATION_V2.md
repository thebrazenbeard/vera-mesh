# WorkBridge / VeraMesh integration V2

Source compatibility subject:

- WorkBridge repository: `thebrazenbeard/WorkBridgeMCP`
- WorkBridge PR: #5
- current PR head: `92c66159d9b610a94f59066ab2bb6416f4513093`
- interface-bearing ancestor: `0b80fe050d8f03da54ee14123737af26740c1605`
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

Currentness note: the WorkBridge movement from `0b80fe050d8f03da54ee14123737af26740c1605` to `92c66159d9b610a94f59066ab2bb6416f4513093` changes exactly `.github/workflows/ci.yml`, `scripts/Install-WorkBridgeLappy.ps1`, `scripts/Test-WorkBridgeBinary.ps1`, and `scripts/Test-WorkBridgeHttpBinary.ps1`. The MCP/runtime interface consumed by this adapter is unchanged. Current WorkBridge exact-head push run `35797927305` and PR run `35797931646` pass; independent exact-head review remains a separate gate.
