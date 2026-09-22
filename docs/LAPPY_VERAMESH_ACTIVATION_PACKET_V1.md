# Lappy VeraMesh Activation Packet V1

Status: **SOURCE QUALIFICATION IN PROGRESS / LAPPY CONNECTION EFFECT AUTHORIZED / CHATGPT E2E NOT YET PROVEN**

## Current Lappy condition

The current Lappy already runs `VeraPortAgent` from `C:\ProgramData\VeraMesh\veraport.json` on loopback `127.0.0.1:17444`, with its existing workstation/TLS identity, state DB, allowed roots, and process execution disabled.

The currently enrolled controller principal previously observed on Lappy is:

`controller:ef3814b70f0883d2fbc4a8b7bd7b4eb982ac6cf74d9fd76734827d4561bc554b`

A bounded custody scan did not recover a private key proven to match that principal. Therefore filename/path resemblance is not accepted as credential identity.

## Existing-install mode — current Lappy

`tools/windows_activate_lappy_veramesh.ps1` detects the existing VeraPort installation and dispatches to:

`tools/windows_attach_existing_lappy_veramesh.ps1`

The attach packet preserves:

- the current VeraPort service configuration;
- workstation private identity;
- TLS identity;
- state DB;
- allowed roots;
- process policy;
- every pre-existing controller trust entry.

It first tries the historical controller-key candidate. The key is reused only if its derived EC P-256 controller principal is already present in the live trust and has `fs.read`.

If that exact credential cannot be recovered, the packet uses `veraport_agent.controller_recovery` to create a distinct local EC P-256 controller at:

`C:\ProgramData\VeraMesh\controller\chatgpt-readonly-controller.pem`

The recovery operation is fail-closed:

- the new controller receives exactly `fs.read`;
- existing controller entries are appended-to, never replaced or retired;
- a byte-for-byte backup of the pre-change trust JSON is written first;
- trust is replaced atomically;
- the old entries are verified still present after mutation;
- an interrupted generated-key state is resumable only when its recovery marker matches;
- an unrelated pre-existing generated-key file is never silently enrolled;
- private-key material is never written to Git, Bus, or receipts.

When a new read-only controller is enrolled, `VeraPortAgent` is deliberately restarted and must return to RUNNING before tunnel attachment continues.

The Secure MCP Tunnel controller itself still requests only `fs.read` and exposes only read/lane operations. A broader historical workstation trust ceiling is not promoted into ChatGPT authority.

## Secure MCP Tunnel runtime

Current existing-install Python source pin:

`c45295dbd1b66f2ebf023b7bf13118dcac72e35b`

OpenAI `tunnel-client` pin:

- release: `v0.0.14`
- Windows amd64 archive SHA-256: `784ab8da7b5a88f0109f1fd8aaf0a1c86067430b896dddf307ef7e3cc49fa1a5`

Intended route:

`ChatGPT -> OpenAI Secure MCP Tunnel -> tunnel-client -> veraport-mcp-stdio -> ControllerRuntime -> existing VeraPortAgent -> Lappy`

RDC is not a dependency.

## Provider material

The tunnel ID and restricted runtime API key are not stored in GitHub. The runtime key remains local under protected ProgramData storage and its value is excluded from receipts.

## Existing-install effects

The authorized attach path may:

- recover an already-enrolled controller credential;
- if recovery fails, append one new `fs.read` controller without deleting the predecessor;
- restart `VeraPortAgent` only when the trust set changed;
- create the isolated tunnel-side runtime and pinned `tunnel-client`;
- write controller/tunnel configuration and protected runtime-key storage;
- install/start `VeraMeshTunnelRuntime`;
- write local diagnostic receipts.

It does not:

- rotate the workstation identity or TLS identity;
- remove or downgrade existing controller trust;
- change existing allowed roots;
- enable process execution;
- change firewall or Tailscale configuration;
- merge a repository;
- claim ChatGPT registration before it actually occurs.

## Mandatory acceptance order

1. exact-head source/CI qualification;
2. recover or narrowly enroll the Lappy controller;
3. restart VeraPort only if trust changed and verify RUNNING;
4. `veraport-doctor --live --tunnel-status`;
5. authenticated VeraPort data-plane PASS;
6. managed tunnel runtime healthy;
7. select/associate the Secure MCP Tunnel in ChatGPT;
8. perform a read-only Lappy tool call;
9. only then call the ChatGPT/Lappy route current.

Source, CI, credential enrollment, service reload, tunnel health, ChatGPT registration, and successful tool effect remain distinct states.
