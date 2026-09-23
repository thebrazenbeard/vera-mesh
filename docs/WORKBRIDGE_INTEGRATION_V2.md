# WorkBridge / VeraMesh integration V2

Source compatibility subject:

- WorkBridge repository: `thebrazenbeard/WorkBridgeMCP`
- WorkBridge candidate: PR #5 at `e89a0b43717a4c9a4aabfd1d7e1c967a375f65c4`
- WorkBridge repair: draft PR #6 currently at `db56871f74f641a0819a601fe166e3241352edaf`, based on PR #5; current code-bearing native-stderr repair `d9e8881ca6ffa17ea5b98a6af8c0ef2b2d171f15`
- VeraMesh successor base: PR #25 @ `60d233c8ccc25871b0666d00c53fa9be26c7cc74`

This adapter is an optional private workstation backend under the existing VeraMesh public OAuth gateway. It is separate from the Secure MCP Tunnel bootstrap path used by `veraport-mcp-stdio`.

Security invariants:

- VeraMesh public OAuth terminates at VeraMesh; the user OAuth token is never forwarded to WorkBridge.
- WorkBridge uses a separate bearer secret named by `bearer_token_env`.
- The VeraMesh client sends that secret only to the exact configured MCP endpoint and rejects HTTP redirects. A redirect to another local service cannot receive the bearer secret.
- A WorkBridge backend can implement an already-authorized VeraMesh public tool, but cannot cause a tool or OAuth scope to become public.
- `allowed_roots` is mandatory in the VeraMesh WorkBridge upstream configuration and is an explicit integration-side filesystem ceiling in addition to WorkBridge's own configured roots.
- Read-only WorkBridge failures may fall back only to an independently supported VeraPort operation.
- After a WorkBridge mutation attempt, failure is returned; VeraMesh never transparently replays the mutation against VeraPort.
- Only semantics-preserving overlaps are mapped: UTF-8 read/write, stat, bounded directory pagination, and non-parent mkdir.
- WorkBridge process execution is not mapped into the VeraMesh public process surface.

This binding qualifies source compatibility only. It does not establish that the exact WorkBridge binary is installed, that WorkBridge and VeraPort runtime root sets are equivalent, that either backend is currently selected, or that a workstation effect occurred.

The real-binary integration gate in `.github/workflows/veramesh-ci.yml` now checks out WorkBridge at exact code-bearing commit `d9e8881ca6ffa17ea5b98a6af8c0ef2b2d171f15`, builds it, starts authenticated loopback HTTP, and exercises read/stat/list through the VeraMesh client while checking that write is absent in a read-only profile. This pin moved because live Lappy execution exposed a Windows PowerShell native-stderr defect in the predecessor installer runner. Fresh VeraMesh CI is required against the new pin; predecessor CI remains historical evidence only.
