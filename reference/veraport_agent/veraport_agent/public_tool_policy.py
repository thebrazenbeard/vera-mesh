from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


SCOPES = {
    "computer.profile": (
        "Read authenticated workstation identity and path health."
    ),
    "computer.read": (
        "Read/search filesystem content inside workstation-allowed roots."
    ),
    "computer.write": (
        "Modify filesystem content inside workstation-allowed roots."
    ),
    "computer.process": (
        "Start, inspect, interact with, and terminate VeraPort-managed processes."
    ),
}


@dataclass(frozen=True)
class PublicToolPolicy:
    name: str
    operation: str
    scopes: tuple[str, ...]
    capabilities: tuple[str, ...]
    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool

    @property
    def annotations(self) -> dict[str, bool]:
        return {
            "readOnlyHint": self.read_only,
            "destructiveHint": self.destructive,
            "idempotentHint": self.idempotent,
            "openWorldHint": self.open_world,
        }

    @property
    def security_schemes(self) -> list[dict[str, Any]]:
        return [{
            "type": "oauth2",
            "scopes": list(self.scopes),
        }]

    def descriptor_extensions(self) -> dict[str, Any]:
        schemes = self.security_schemes
        return {
            "securitySchemes": schemes,
            "_meta": {
                "securitySchemes": schemes,
            },
            "annotations": self.annotations,
        }


def _p(
    name: str,
    operation: str,
    scopes: tuple[str, ...],
    capabilities: tuple[str, ...],
    *,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
    open_world: bool,
) -> PublicToolPolicy:
    return PublicToolPolicy(
        name=name,
        operation=operation,
        scopes=scopes,
        capabilities=capabilities,
        read_only=read_only,
        destructive=destructive,
        idempotent=idempotent,
        open_world=open_world,
    )


PUBLIC_TOOLS = (
    _p("computer_info", "machine.info", ("computer.profile",), (),
       read_only=True, destructive=False, idempotent=True, open_world=False),
    _p("read_file", "fs.read_text", ("computer.read",), ("fs.read",),
       read_only=True, destructive=False, idempotent=True, open_world=False),
    _p("read_bytes", "fs.read_bytes", ("computer.read",), ("fs.read",),
       read_only=True, destructive=False, idempotent=True, open_world=False),
    _p("stat_path", "fs.stat", ("computer.read",), ("fs.read",),
       read_only=True, destructive=False, idempotent=True, open_world=False),
    _p("list_directory", "fs.list_dir", ("computer.read",), ("fs.read",),
       read_only=True, destructive=False, idempotent=True, open_world=False),
    _p("search_files", "fs.search", ("computer.read",), ("fs.read",),
       read_only=True, destructive=False, idempotent=True, open_world=False),
    _p("search_content", "fs.search_content", ("computer.read",), ("fs.read",),
       read_only=True, destructive=False, idempotent=True, open_world=False),
    _p("write_file", "fs.write_text", ("computer.write",), ("fs.write",),
       read_only=False, destructive=True, idempotent=True, open_world=False),
    _p("append_file", "fs.append_text", ("computer.write",), ("fs.write",),
       read_only=False, destructive=False, idempotent=False, open_world=False),
    _p("make_directory", "fs.mkdir", ("computer.write",), ("fs.write",),
       read_only=False, destructive=False, idempotent=True, open_world=False),
    _p("move_path", "fs.move", ("computer.write",), ("fs.write",),
       read_only=False, destructive=True, idempotent=False, open_world=False),
    _p("replace_text", "fs.replace_text",
       ("computer.read", "computer.write"), ("fs.read", "fs.write"),
       read_only=False, destructive=True, idempotent=False, open_world=False),
    _p("run_process", "process.exec", ("computer.process",), ("process.exec",),
       read_only=False, destructive=True, idempotent=False, open_world=True),
    _p("start_process", "process.start", ("computer.process",),
       ("process.exec", "process.inspect", "process.interact", "process.control"),
       read_only=False, destructive=True, idempotent=False, open_world=True),
    _p("list_processes", "process.list", ("computer.process",), ("process.inspect",),
       read_only=True, destructive=False, idempotent=True, open_world=False),
    _p("process_status", "process.status", ("computer.process",), ("process.inspect",),
       read_only=True, destructive=False, idempotent=True, open_world=False),
    _p("process_output", "process.output", ("computer.process",), ("process.inspect",),
       read_only=True, destructive=False, idempotent=True, open_world=False),
    _p("process_input", "process.input", ("computer.process",), ("process.interact",),
       read_only=False, destructive=True, idempotent=False, open_world=True),
    _p("terminate_process", "process.terminate", ("computer.process",),
       ("process.control", "process.inspect"),
       read_only=False, destructive=True, idempotent=True, open_world=False),
    _p("release_process", "gateway.release_process", ("computer.process",),
       ("process.control",),
       read_only=False, destructive=True, idempotent=True, open_world=False),
)

PUBLIC_TOOL_BY_NAME = {tool.name: tool for tool in PUBLIC_TOOLS}


def public_policy_document() -> dict[str, Any]:
    return {
        "schema": "VERAMESH_PUBLIC_TOOL_POLICY_V1",
        "principle": (
            "public_tools_are_ergonomic_facades_over_veraport_authority"
        ),
        "oauth_scopes": dict(SCOPES),
        "tools": [
            {
                "name": tool.name,
                "operation": tool.operation,
                "scopes": list(tool.scopes),
                "capabilities": list(tool.capabilities),
                "annotations": tool.annotations,
            }
            for tool in PUBLIC_TOOLS
        ],
        "invariants": [
            (
                "lane_id and fencing_token are internal gateway state and are "
                "not public tool parameters"
            ),
            (
                "OAuth scope checks narrow but never replace VeraPort "
                "controller/session/lane/workstation policy"
            ),
            (
                "a public filesystem call claims only the requested path or "
                "search root"
            ),
            (
                "a public managed-process handle is mapped to one hidden "
                "VeraPort lane/fence owned by one authenticated actor"
            ),
            (
                "successful mutations remain successful even if later lane "
                "cleanup fails; cleanup evidence is reported separately"
            ),
            (
                "public tool metadata must be generated from this catalog "
                "rather than maintained independently"
            ),
            (
                "release_process closes the hidden process lane and terminates "
                "the managed child if it is still running"
            ),
        ],
    }


def oauth_challenge(
    resource_metadata_url: str,
    *,
    error: str = "insufficient_scope",
    description: str = "Authorization is required for this VeraMesh tool.",
) -> dict[str, list[str]]:
    for name, value in {
        "resource_metadata_url": resource_metadata_url,
        "error": error,
        "description": description,
    }.items():
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} is required")
        if any(ch in value for ch in ('"', "\r", "\n")):
            raise ValueError(f"{name} contains unsafe challenge characters")
    challenge = (
        f'Bearer resource_metadata="{resource_metadata_url}", '
        f'error="{error}", error_description="{description}"'
    )
    return {"mcp/www_authenticate": [challenge]}
