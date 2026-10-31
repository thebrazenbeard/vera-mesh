# VeraMesh — authenticated edge gateway and workstation access

**Status:** source-available reference implementation and protocols; not a claim of deployment or production qualification.  
**License:** the original material is **proprietary, source-available, not open source**. Private noncommercial evaluation has limited permission; commercial use, redistribution, hosted-service use, and commercial derivatives require written permission. Read [LICENSE](LICENSE) and [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md). Third-party components retain their own terms.

VeraMesh separates **device identity, authenticated transport, routing, durable delivery, workstation execution, and authorization** so applications can use remote capabilities without inheriting permission merely from network connectivity. It provides a Go MCP/OAuth workstation gateway, a VeraPort logical-lane protocol and client/reference implementations, and relay/edge protocol and conformance material.

The design can serve model-driven clients or ordinary software. **A model name, provider, prompt, relay, network membership, or transport session is not an authority grant.** The current gateway uses an OAuth resource-owner subject and explicit scopes; VeraPort uses cryptographic controller/workstation identities and workstation-local capability ceilings. Protected effects still require their owning authorization mechanisms. This repository does **not** establish a universal host-owned immutable authorization service.

## What is implemented

| Surface | Reusable contract | Boundary |
| --- | --- | --- |
| [Go gateway](gateway/veramesh-go/) | Loopback HTTP MCP endpoint, OAuth token introspection, protected-resource discovery, per-tool scopes, actor-scoped controller facades | Requires separately configured HTTPS reverse proxy, authorization server and VeraPort credentials |
| [VeraPort V1](protocol/veraport/v1/) | Mutually authenticated hot-session handshake, multiplexed request IDs, capability-narrowed expiring lanes, resource claims and durable fencing/idempotency option | Reference byte stream is loopback-tested; production direct/edge transport and installations are separately qualified |
| [Relay protocol V1](protocol/v1/) | Envelope, pairing, signature, receipt and replay semantics with conformance vectors | Relay custody or delivery receipt is not workstation execution proof |
| [Optional WorkBridge backend](docs/WORKBRIDGE_INTEGRATION_V2.md) | Semantics-preserving tool substitution below the existing gateway policy | Not a second authorization source; no public scope/tool expansion and no process execution mapping |

VeraPort paths are **direct-first**: `DIRECT_STREAM`, then `EDGE_STREAM`, with `DURABLE_RELAY` specified for recovery/store-and-forward. The controller bootstrap currently accepts direct and edge stream endpoints; its config rejects `DURABLE_RELAY` as unimplemented. A *VeraPort* is a logical execution lane, not a TCP/UDP port.

### Public MCP surface

The HTTP gateway exposes an MCP Streamable HTTP resource at the dedicated path configured as `expected_resource` (for example, `https://mesh.example.test/mcp`) and OAuth protected-resource metadata at `/.well-known/oauth-protected-resource`.

Supported public tool families are conditional on configured operations and capability ceilings:

| OAuth scope | Examples | Default posture |
| --- | --- | --- |
| `computer.profile` | `computer_info` | Identity/path-health read |
| `computer.read` | `read_file`, `read_bytes`, `stat_path`, `list_directory`, `search_files`, `search_content` | Read-only, allowed-root bounded |
| `computer.write` | `write_file`, `append_file`, `make_directory`, `move_path`, `replace_text` | Requires explicit write grants |
| `computer.process` | `run_process`, `start_process`, managed-process status/input/termination | Powerful; disabled by the checked-in read-only example |

For example, an authenticated MCP client with `computer.read` may call the `read_file` tool with arguments:

```json
{"path":"/srv/example-workspace/notes.txt","encoding":"utf-8"}
```

That path is illustrative. It must fall inside roots authorized on the actual workstation; the gateway cannot grant new roots. The tool name and arguments are MCP tool data, **not** a standalone unauthenticated REST endpoint. Discovery may be available without a bearer token; execution checks token subject and required scopes.

## Source quickstart

Requires Go compatible with [`gateway/veramesh-go/go.mod`](gateway/veramesh-go/go.mod). From a fresh clone:

```sh
git clone https://github.com/thebrazenbeard/vera-mesh.git
cd vera-mesh/gateway/veramesh-go
go test ./...
go vet ./...
go run ./cmd/veramesh-gateway -build-info
go build -o veramesh-gateway ./cmd/veramesh-gateway
```

These commands build/test local **source**, without pairing a workstation or opening a network listener. The GitHub workflow also tests Python protocol fixtures and Go gateway packages; running a command locally or observing CI is not a live-host acceptance test.

For an authenticated deployment, provision **your own** P-256 controller key, trusted TLS CA, workstation public key, enrolled workstation and HTTPS OAuth introspection service. Do not commit private keys or token values. The repository supplies [controller and OAuth configuration shapes](packaging/synology-gateway/) as examples. A minimal *read-only* controller configuration looks like:

```json
{
  "schema": "VERAPORT_CONTROLLER_MCP_CONFIG_V1",
  "controller_key": "credentials/controller-key.pem",
  "tls_ca": "credentials/workstation-ca.pem",
  "workstation_public_key": "credentials/workstation-public.pem",
  "requested_capabilities": ["fs.read"],
  "gateway_operations": ["lane.open", "lane.close", "fs.read_text", "fs.stat", "fs.list_dir"],
  "endpoints": [{
    "endpoint_id": "workstation-direct",
    "mode": "DIRECT_STREAM",
    "host": "workstation.example.test",
    "port": 7444,
    "server_hostname": "workstation.example.test",
    "durable_idempotency": false
  }]
}
```

The controller key and CA paths are resolved relative to this JSON file. Replace example hosts, ports, certificates, and policy with values owned by the installing operator; `example.test` does not identify a running service.

A corresponding *shape* for `oauth.json` is:

```json
{
  "schema": "VERAMESH_OAUTH_INTROSPECTION_V1",
  "introspection_endpoint": "https://auth.example.test/oauth2/introspect",
  "client_id": "veramesh-gateway",
  "client_secret_env": "VERAMESH_OAUTH_CLIENT_SECRET",
  "expected_issuer": "https://auth.example.test",
  "expected_resource": "https://mesh.example.test/mcp",
  "client_auth_method": "client_secret_basic",
  "timeout_s": 5
}
```

After provisioning an actual secret in the named environment variable, verify the config and run the gateway behind a separately reviewed HTTPS reverse proxy that preserves the public Host and exact `/mcp` and metadata paths:

```sh
./veramesh-gateway -controller-config ./controller.json -oauth-config ./oauth.json -check-config
./veramesh-gateway -listen 127.0.0.1:17446 -controller-config ./controller.json -oauth-config ./oauth.json
```

`-check-config` validates configuration **without listening or dialing the workstation/WorkBridge**; it is not connectivity, identity enrollment, reverse-proxy, or authorization-server validation. The executable rejects non-loopback listen addresses.

## Optional WorkBridge interoperability

The optional `-workbridge-config` expects `VERAMESH_WORKBRIDGE_UPSTREAM_V1` with a dedicated HTTPS or literal-loopback-HTTP MCP endpoint, a separate `bearer_token_env`, and mandatory `allowed_roots`. The bearer is sent only to that exact endpoint; redirects are rejected. The public OAuth token is **not** forwarded upstream.

Only overlapping read-text, stat, directory-list, write-text and non-parent mkdir semantics are translated where supported. The WorkBridge roots and VeraMesh `allowed_roots` both constrain access. **Read-only** upstream failures may fall back to an independently supported VeraPort operation. Once a mutation has been attempted, the gateway returns the failure rather than replays it through another backend. WorkBridge process execution is never mapped to the public process tools.

The [CI integration lane](.github/workflows/veramesh-ci.yml) builds **`thebrazenbeard/WorkBridgeMCP` at exact source commit `d9e8881ca6ffa17ea5b98a6af8c0ef2b2d171f15`** and exercises read/stat/list against a real loopback server in a read-only test configuration. That is a source-compatibility input, not an automatically interchangeable version promise. **`thebrazenbeard/workbridge` (lowercase) is a separate repository and must be qualified separately.** No claim here establishes a deployed binary, NAS/Windows equivalence, or a verified physical effect.

## Security and qualification limits

- Network carriers (including private overlays) provide reachability, not VeraMesh identity, consent or operational authority.
- Hot-session proof binds an exact workstation and fresh challenges; lane claims, leases and capability ceilings still apply. Managed process execution is an explicit high-risk workstation grant, not an OS sandbox.
- Receipt of a relay envelope or MCP response is not independent proof of a protected effect. Uncertain mutation outcomes must not be blindly replayed.
- Source tests do not prove a proxy, host, service, credential, tunnel or node is installed or healthy. See [security model](docs/SECURITY_MODEL.md) and [validation matrix](docs/VALIDATION_MATRIX.md) for real-system acceptance requirements.
- No authority, identity, permissions, scope or consent should be transferred between distinct client/model adapters merely because they share this transport.

For deeper contracts, see [architecture](docs/ARCHITECTURE.md), [VeraPort V1](protocol/veraport/v1/README.md), [VeraMesh security](docs/SECURITY_MODEL.md), and [WorkBridge integration](docs/WORKBRIDGE_INTEGRATION_V2.md).
