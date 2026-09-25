# OpenAI Responses + VeraMesh Operator V1

Status: **SOURCE CANDIDATE / NOT DEPLOYED / NO API CALL PERFORMED**

## Why this path exists

ChatGPT developer-mode MCP-app availability is a product/account surface and is not a reliable foundation for a personal workstation-control architecture.

The OpenAI Responses API has a separate supported path for private MCP servers: an MCP tool definition can name an existing Secure MCP Tunnel directly with `tunnel_id`. The private MCP server stays behind the existing outbound-only tunnel; no public Cloudflare bridge and no ChatGPT workspace MCP registration are required.

Target route:

```text
operator client
    -> OpenAI Responses API
        -> Secure MCP Tunnel by tunnel_id
            -> existing VeraMeshTunnelRuntime on Lappy
                -> veraport-mcp-stdio
                    -> ControllerRuntime
                        -> VeraPortAgent
                            -> Lappy
```

This is an API application path. API usage and billing are separate from a ChatGPT subscription. The repository code does not create an API key, perform a billable API call, enable workstation process authority, or install anything.

## Client

`tools/openai_responses_veramesh_operator.py`

The client is dependency-free Python and reads:

- `OPENAI_API_KEY` from the process environment;
- `VERAMESH_TUNNEL_ID` from the process environment, an explicit CLI value, or only the `tunnel_id` field of a VeraMesh `tunnel-runtime.json` supplied with `--tunnel-config`;
- `OPENAI_MODEL` optionally, defaulting to the model currently used in OpenAI's Secure MCP Tunnel examples when no override is supplied.

The client never writes the API key or tunnel ID to disk and redacts both from HTTP error text and dry-run output.

## Stateless continuation

V1 sends `store:false` on every Responses request.

Instead of relying on `previous_response_id`, it keeps the conversation only in process memory and replays the complete sequence of returned Responses output items plus subsequent inputs. That includes encrypted reasoning items, MCP discovery/call items, and MCP approval items. This follows OpenAI's documented stateless continuation pattern.

Closing the process discards the local transcript.

## Capability profiles

The model-visible MCP surface is narrowed with `allowed_tools`.

`read` is the default and exposes lane management plus filesystem reads/searches.

`filesystem` adds filesystem writes/moves/replacements.

`build` additionally exposes VeraPort-managed process execution, interactive-process I/O, status/output, and termination.

These profiles do not grant underlying workstation authority. VeraPort capability ceilings, lane claims, fencing, allowed roots, and local process policy remain authoritative.

## Approvals

Default:

```text
require_approval = always
```

Every MCP tool call therefore stops at an explicit local approval prompt that displays the exact tool and arguments.

Optional `--auto-approve-readonly` asks the Responses API to skip approval only for a fixed read-only allowlist. It never auto-approves filesystem writes, process start/exec/input/terminate, lane mutation, or another mutating tool.

There is deliberately no `--yes` or auto-approve-all mode in V1.

## First acceptance sequence

The first live acceptance should stay read-only:

1. Verify the existing `VeraMeshTunnelRuntime` remains healthy.
2. Supply an OpenAI API key with the required API/tunnel access through environment state; do not commit it.
3. Supply the existing tunnel ID through environment state or local protected tunnel config.
4. Run `--doctor` first; it performs no API request.
5. Run a single `--profile read --prompt "Use machine_info to identify the connected workstation and report only non-secret status."` request.
6. Inspect and explicitly approve the `machine_info` call.
7. Require evidence that the returned MCP call came through the expected VeraMesh surface.
8. Only after read-only acceptance consider `filesystem` and then `build` profiles.

## Protected effects still outside this branch

This branch does not authorize or perform:

- API key creation;
- billable OpenAI API execution;
- VeraPort process-authority enablement;
- Lappy service installation/replacement;
- credentials/provider/permission changes;
- Secure MCP Tunnel creation/modification;
- merge or direct `main` mutation.

## Claim ceiling

`RESPONSES_API_VERAMESH_OPERATOR_SOURCE_READY_FOR_SECRET_FREE_MOCK_QUALIFICATION`

A source/test PASS does not prove live API connectivity. Live connectivity requires a separately authorized read-only acceptance call using real local credentials and the existing tunnel.
