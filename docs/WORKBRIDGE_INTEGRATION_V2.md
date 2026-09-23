# WorkBridge / VeraMesh integration V2

Source compatibility subject:

- WorkBridge repository: `thebrazenbeard/WorkBridgeMCP`
- WorkBridge candidate: PR #5 at `e89a0b43717a4c9a4aabfd1d7e1c967a375f65c4`
- WorkBridge repair: draft PR #6 at `ab5ca2dd263bdb35e9fcecb297bf094e3a43017e`, based on PR #5; code-bearing ancestor `613c3df0e7bd1d43b123d249e3aaca4366852546`
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

The real-binary integration gate in `.github/workflows/veramesh-ci.yml` checks out WorkBridge at exact commit `ab5ca2dd263bdb35e9fcecb297bf094e3a43017e`, builds it, starts authenticated loopback HTTP, and exercises read/stat/list through the VeraMesh client while checking that write is absent in a read-only profile. Local Windows Go tests against that exact WorkBridge source passed, including the real-binary test; the built binary SHA-256 was `3853be9cb3f0af1ad833e29e1eb022906a0979e3531534147573ae8daccfc62a`. WorkBridge exact-head CI `35846554913` passed Ubuntu tests, Windows tests, and Windows binary build. The GitHub security-agent job failed before review because its requested model was unsupported, so it is not an independent review result.
