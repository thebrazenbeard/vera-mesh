# VeraMeshGateway Synology staging bundle

This directory defines the source-bound staging bundle for the Go VeraMesh public MCP/OAuth gateway.

It deliberately uses the package/runtime identity `VeraMeshGateway`, separate from the existing `VeraMesh` Python EDGE_STREAM package. The two are intended to coexist.

The staged runtime contract is:

- binary: `/var/packages/VeraMeshGateway/target/bin/veramesh-gateway`
- wrapper: `/var/packages/VeraMeshGateway/target/bin/run-veramesh-gateway.sh`
- private runtime directory: `/var/packages/VeraMeshGateway/var/gateway`
- controller config: `controller.json`
- OAuth config: `oauth.json`
- OAuth client secret: `oauth-client-secret`
- VeraPort credentials: paths below `credentials/` relative to `controller.json`
- HTTP bind: literal loopback `127.0.0.1:17446`
- public TLS termination/reverse proxy: outside this bundle and separately authorized

The checked-in controller example is intentionally read-only. It requests only `fs.read` and exposes only read operations plus lane management. Process execution remains disabled unless a later explicitly authorized configuration expands both the workstation and gateway capability ceilings.

No credential, OAuth secret, public hostname, provider registration, reverse-proxy rule, firewall rule, or Tailscale mutation is embedded in this bundle.

Building this bundle is not installation or deployment.
