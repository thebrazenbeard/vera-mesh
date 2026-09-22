from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .controller_runtime import ControllerRuntime
from .public_gateway import PublicWorkstationFacade
from .public_tool_policy import (
    PUBLIC_TOOL_BY_NAME,
    PUBLIC_TOOLS,
    PublicToolPolicy,
    oauth_challenge,
)


TOOL_DESCRIPTIONS = {
    "computer_info": "Read authenticated workstation identity and live VeraPort path health.",
    "read_file": "Read bounded text from an absolute path inside workstation-allowed roots.",
    "read_bytes": "Read a version-bound byte range from an absolute allowed path.",
    "stat_path": "Read metadata for an absolute allowed path without following the final symlink.",
    "list_directory": "List one allowed directory with deterministic bounded pagination.",
    "search_files": "Search path names below an allowed root with explicit depth/result/scan bounds.",
    "search_content": "Search bounded UTF-8-decoded file content below an allowed root.",
    "write_file": "Atomically write text to an absolute allowed path.",
    "append_file": "Append text to an absolute allowed path.",
    "make_directory": "Create a directory inside workstation-allowed roots.",
    "move_path": "Move or rename a path when source and destination are both authorized.",
    "replace_text": "Atomically replace an exact expected number of text matches.",
    "run_process": "Run one bounded argv-vector process without an implicit shell.",
    "start_process": "Start a VeraPort-managed process and return an opaque public handle.",
    "list_processes": "List managed processes owned by the authenticated gateway actor.",
    "process_status": "Read status for one managed public process handle.",
    "process_output": "Read bounded stdout/stderr chunks for one managed process.",
    "process_input": "Send bounded UTF-8 input to one managed process.",
    "terminate_process": "Terminate one managed process owned by the authenticated actor.",
    "release_process": "Release a managed process lease; a still-running child is terminated.",
}


@dataclass
class PublicMCPBundle:
    server: Any
    facades: dict[str, PublicWorkstationFacade]

    async def close(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for actor, facade in tuple(self.facades.items()):
            results[actor] = await facade.close()
        return results


def _tool_input_schema(name: str) -> dict[str, Any]:
    try:
        from mcp.server.fastmcp.utilities.func_metadata import func_metadata
    except ImportError as exc:
        raise RuntimeError(
            "public MCP gateway requires mcp>=1.27.2,<2"
        ) from exc

    fn = getattr(PublicWorkstationFacade, name)
    metadata = func_metadata(
        fn,
        skip_names=["self"],
        structured_output=False,
    )
    return metadata.arg_model.model_json_schema(by_alias=True)


def _tool_descriptor(policy: PublicToolPolicy):
    try:
        from mcp import types
    except ImportError as exc:
        raise RuntimeError(
            "public MCP gateway requires mcp>=1.27.2,<2"
        ) from exc

    schemes = policy.security_schemes
    return types.Tool(
        name=policy.name,
        title=policy.name.replace("_", " ").title(),
        description=TOOL_DESCRIPTIONS[policy.name],
        inputSchema=_tool_input_schema(policy.name),
        annotations=types.ToolAnnotations(**policy.annotations),
        _meta={"securitySchemes": schemes},
        # OpenAI public plugins require the standard field in addition to the
        # compatibility _meta mirror. MCP 1.x Tool permits extension fields.
        securitySchemes=schemes,
    )


def _supported(runtime: ControllerRuntime, policy: PublicToolPolicy) -> bool:
    if policy.name == "computer_info":
        return True
    operations = runtime.config.gateway_operations
    if not {"lane.open", "lane.close"}.issubset(operations):
        return False
    if not set(policy.capabilities).issubset(
        runtime.config.requested_capabilities
    ):
        return False
    if policy.operation == "gateway.release_process":
        return (
            "process.start" in operations
            and "process.terminate" in operations
        )
    if policy.name in {
        "list_processes",
        "process_status",
        "process_output",
        "process_input",
        "terminate_process",
    }:
        return (
            "process.start" in operations
            and policy.operation in operations
        )
    return policy.operation in operations


def build_public_mcp_protocol(
    runtime: ControllerRuntime,
    *,
    resource_metadata_url: str,
    issuer_url: str,
    token_getter: Callable[[], Any] | None = None,
) -> PublicMCPBundle:
    try:
        from mcp import types
        from mcp.server.auth.middleware.auth_context import (
            get_access_token,
        )
        from mcp.server.lowlevel import Server
    except ImportError as exc:
        raise RuntimeError(
            "public MCP gateway requires mcp>=1.27.2,<2"
        ) from exc

    if not resource_metadata_url.startswith("https://"):
        raise ValueError("resource_metadata_url must use https")
    if not issuer_url.startswith("https://"):
        raise ValueError("issuer_url must use https")

    get_token = token_getter or get_access_token
    server = Server(
        "VeraMesh Public Workstation Gateway",
        version="1",
        instructions=(
            "Control one authenticated VeraPort workstation through scoped "
            "filesystem and managed-process tools. Internal lanes and fencing "
            "tokens are never public tool parameters."
        ),
    )
    facades: dict[str, PublicWorkstationFacade] = {}
    descriptors = tuple(
        _tool_descriptor(policy)
        for policy in PUBLIC_TOOLS
        if _supported(runtime, policy)
    )
    descriptor_by_name = {tool.name: tool for tool in descriptors}

    @server.list_tools()
    async def list_tools():
        # Tool discovery is intentionally possible before account linking so
        # ChatGPT can see each tool's OAuth policy and trigger authorization.
        return list(descriptors)

    @server.call_tool(validate_input=True)
    async def call_tool(
        name: str,
        arguments: dict[str, Any] | None,
    ):
        descriptor = descriptor_by_name.get(name)
        policy = PUBLIC_TOOL_BY_NAME.get(name)
        if descriptor is None or policy is None:
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text="Unknown or unavailable VeraMesh tool.",
                    )
                ],
                isError=True,
            )

        token = get_token()
        subject = None if token is None else getattr(token, "subject", None)
        granted = set() if token is None else set(getattr(token, "scopes", ()))
        missing = set(policy.scopes) - granted
        if (
            token is None
            or not isinstance(subject, str)
            or not subject
            or missing
        ):
            if token is None or not subject:
                error = "invalid_token"
                description = "Link your VeraMesh account to continue."
            else:
                error = "insufficient_scope"
                description = (
                    "Additional VeraMesh permission required: "
                    + ", ".join(sorted(missing))
                )
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=description,
                    )
                ],
                isError=True,
                _meta=oauth_challenge(
                    resource_metadata_url,
                    error=error,
                    description=description,
                ),
            )

        actor_id = issuer_url.rstrip("/") + "|" + subject
        facade = facades.get(actor_id)
        if facade is None:
            facade = PublicWorkstationFacade(
                runtime,
                actor_id=actor_id,
            )
            facades[actor_id] = facade

        method = getattr(facade, name)
        try:
            result = await method(**(arguments or {}))
        except Exception as exc:
            code = getattr(exc, "code", exc.__class__.__name__)
            message = str(exc)
            if len(message) > 4096:
                message = message[:4096] + "..."
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=json.dumps(
                            {"error": {"code": code, "message": message}},
                            sort_keys=True,
                        ),
                    )
                ],
                isError=True,
            )

        return [
            types.TextContent(
                type="text",
                text=json.dumps(result, sort_keys=True),
            )
        ]

    return PublicMCPBundle(server=server, facades=facades)
