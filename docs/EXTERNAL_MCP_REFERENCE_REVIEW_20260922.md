# VeraMesh external MCP reference review — 2026-09-22

Status: REVIEWED REFERENCE MATERIAL / NO EXTERNAL REPOSITORY HAS AUTHORITY

This review records exact public heads supplied for consideration during VeraMesh PR #25. Ideas may be adopted; external repositories do not become VeraMesh authority merely because they were reviewed.

## Exact subjects

- `anywave/lattice-consult-mcp@675faae4d8ebd32071b656a2344fbd54843c8295`
- `rafalr100/synology-mcp@d095c1c38da106983d6fbece26d000bf95ab061a`
- `RafalManka/synology-mcp@7deb084f1fd5796643332b57f3ecb53e1ac45b0b`
- `lefty3382/synology-mcp@2f1bce5373341f3d53bc005ac676ccb41f9f8250`
- `filamind-app/filamind-ai@620b1c74fd57e8059983e2519d4f8c3db0f476c7`

## Adopt now

### Least-authority tiers

`lefty3382/synology-mcp` exposes health/read/write tiers and registers only the tools permitted by the selected tier. VeraMesh retains a stronger multi-boundary form of that idea: a public OAuth scope, gateway operation allow-list, requested VeraPort capability, and workstation policy must all permit an operation. The Synology gateway staging example remains read-only by default.

### Bounded DSM session recovery for future DSM-native tools

`lefty3382/synology-mcp` treats DSM error codes 106, 107, and 119 as session-loss conditions, reauthenticates, and retries once while excluding ordinary permission errors. `rafalr100/synology-mcp` likewise caches a DSM session and retries once on an invalid SID. If VeraMesh later gains DSM-native tools, use bounded one-retry session recovery and keep permission failures fail-closed. This review does not authorize DSM credentials or add DSM APIs.

### Credential and logging hygiene

`rafalr100/synology-mcp` explicitly suppresses HTTP-client logging that could expose DSM passwords/session IDs and supports persistent DSM trusted-device tokens. Any future VeraMesh DSM client should preserve equivalent or stronger secret-redaction rules.

### Native package-user isolation

`filamind-app/filamind-ai` reinforces useful DSM package boundaries: a dedicated package user/group, a private configuration directory, and 0600 secret material. VeraMeshGateway staging already separates its package identity from the existing VeraMesh edge; future installer work should preserve private package-user custody for OAuth and VeraPort controller credentials.

### Executable build identity

`filamind-app/filamind-ai` carries build metadata into its package and records on-device executable verification. VeraMesh uses a stricter variant: the gateway binary exposes machine-readable build identity, and the bundle builder cross-checks Go VCS metadata against exact Git HEAD.

## Useful reference, not adopted as authority

### RafalManka/synology-mcp

Useful evidence that an MCP service can run as a small self-contained binary behind a DSM reverse proxy. Its static bearer-token boundary is not sufficient for VeraMesh's public ChatGPT target, which requires OAuth resource metadata, per-tool security schemes/scopes, and runtime OAuth challenges.

Its x86_64-musl/container target also does not establish compatibility with the DS216 armada38x/ARMv7 target.

### rafalr100/synology-mcp

Useful breadth reference for DSM Web API tool coverage and 2FA/trusted-device handling. It is primarily stdio/local-client oriented and therefore does not replace VeraMesh's public HTTP/OAuth boundary.

### lefty3382/synology-mcp

Useful for permission tiers, graceful secondary-backend degradation, Streamable HTTP operation, and bounded DSM session renewal. It does not replace VeraPort's application identity, capability ceilings, fencing, mutation idempotency, or actor-bound process ownership.

### filamind-app/filamind-ai

Useful for real DSM SPK layout, package-user isolation, build metadata, and explicit on-device verification. Its target hardware/builds are x86-class and do not establish DS216 ARMv7 compatibility. Its broad local tool runtime is not a substitute for VeraPort authority.

### anywave/lattice-consult-mcp

Useful to BT2/hostile-review orchestration as a pattern for parallel heterogeneous-model consultation with convergence/divergence reporting and graceful provider failure. It is stdio/provider-API oriented, not a VeraMesh transport or Synology deployment dependency. Its recent MCP-major-version pin is also a reminder that SDK major drift must be explicit; VeraMesh pins its Go MCP SDK dependency.

## Explicit non-adoptions

VeraMesh does not adopt from these references:

- a static bearer token as public PC-control authorization;
- unauthenticated public MCP;
- Docker as a requirement on DS216;
- administrator DSM authority as a prerequisite for workstation control;
- broad shell execution that bypasses VeraPort;
- raw multi-provider model output as a runtime dependency;
- DS216 compatibility inferred from a different Synology model.

## OpenAI public-plugin compatibility check

As checked on 2026-09-22, current OpenAI plugin guidance requires:
- portable root `plugin.json`;
- root `mcp.json` with the Agent Plugins MCP schema;
- remote MCP entries using `type: streamable-http`;
- OAuth protected-resource metadata;
- per-tool `securitySchemes`;
- runtime tool errors carrying `_meta["mcp/www_authenticate"]` to trigger account linking.

VeraMesh source now targets those contracts. They remain source/CI claims until a real public endpoint and provider are separately authorized and qualified.

References:
- https://developers.openai.com/plugins/build/plugins
- https://developers.openai.com/plugins/build/auth

## Hostile review

> Five examples agreeing that "MCP on a NAS works" is not evidence that VeraMesh's security model works. The useful evidence is narrower: package mechanics, session-recovery failure modes, least-authority presentation, and static-binary deployment patterns. VeraPort authorization and ChatGPT OAuth interoperability still require VeraMesh's own exact-head tests and live qualification.
