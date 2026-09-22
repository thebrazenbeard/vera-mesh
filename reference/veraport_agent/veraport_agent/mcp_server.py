from __future__ import annotations

import ipaddress
import os
from typing import Any

from .controller_config import ControllerConfig
from .controller_runtime import ControllerRuntime


class VeraPortMCPFacade:
    """Tool facade. Tool discovery does not expand underlying authority."""

    def __init__(self, runtime: ControllerRuntime) -> None:
        self.runtime = runtime

    async def machine_info(self) -> dict[str, Any]:
        return await self.runtime.machine_info()

    async def lane_list(self) -> dict[str, Any]:
        return await self.runtime.list_lanes()

    async def lane_open(
        self,
        lane_id: str,
        task_id: str,
        capabilities: list[str],
        claims: list[dict[str, str]],
        ttl_s: float = 300.0,
    ) -> dict[str, Any]:
        extra = set(capabilities) - set(
            self.runtime.config.requested_capabilities
        )
        if extra:
            raise PermissionError(
                "lane capability exceeds controller session ceiling: "
                + repr(sorted(extra))
            )
        return await self.runtime.open_lane(
            lane_id=lane_id,
            task_id=task_id,
            capabilities=capabilities,
            claims=claims,
            ttl_s=ttl_s,
        )

    async def lane_renew(
        self,
        lane_id: str,
        fencing_token: int,
        ttl_s: float = 300.0,
    ) -> dict[str, Any]:
        return await self.runtime.renew_lane(
            lane_id=lane_id,
            fencing_token=fencing_token,
            ttl_s=ttl_s,
        )

    async def lane_close(
        self,
        lane_id: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        return await self.runtime.close_lane(
            lane_id=lane_id,
            fencing_token=fencing_token,
        )

    async def fs_read_text(
        self,
        lane_id: str,
        fencing_token: int,
        path: str,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        return await self.runtime.read_text(
            lane_id=lane_id,
            fencing_token=fencing_token,
            path=path,
            encoding=encoding,
        )

    async def fs_read_bytes(
        self,
        lane_id: str,
        fencing_token: int,
        path: str,
        offset: int = 0,
        max_bytes: int | None = None,
        expected_file_version: str | None = None,
    ) -> dict[str, Any]:
        return await self.runtime.read_bytes(
            lane_id=lane_id,
            fencing_token=fencing_token,
            path=path,
            offset=offset,
            max_bytes=max_bytes,
            expected_file_version=expected_file_version,
        )

    async def _read_operation(
        self,
        operation: str,
        lane_id: str,
        fencing_token: int,
        **body: Any,
    ) -> dict[str, Any]:
        await self.runtime.ensure_started()
        if self.runtime._read_router.owns(lane_id, fencing_token):
            return await self.runtime._read_router.call_read_operation(
                operation=operation,
                lane_id=lane_id,
                fencing_token=fencing_token,
                body=body,
            )
        return await self.runtime.gateway.call_operation(
            operation,
            lane_id=lane_id,
            fencing_token=fencing_token,
            **body,
        )

    async def fs_stat(
        self,
        lane_id: str,
        fencing_token: int,
        path: str,
    ) -> dict[str, Any]:
        return await self._read_operation(
            "fs.stat", lane_id, fencing_token, path=path
        )

    async def fs_list_dir(
        self,
        lane_id: str,
        fencing_token: int,
        path: str,
        offset: int = 0,
        max_entries: int = 200,
    ) -> dict[str, Any]:
        return await self._read_operation(
            "fs.list_dir",
            lane_id,
            fencing_token,
            path=path,
            offset=offset,
            max_entries=max_entries,
        )

    async def fs_search(
        self,
        lane_id: str,
        fencing_token: int,
        root: str,
        query: str,
        offset: int = 0,
        max_results: int = 100,
        max_entries: int = 10_000,
        max_depth: int = 12,
        case_sensitive: bool = False,
    ) -> dict[str, Any]:
        return await self._read_operation(
            "fs.search",
            lane_id,
            fencing_token,
            root=root,
            query=query,
            offset=offset,
            max_results=max_results,
            max_entries=max_entries,
            max_depth=max_depth,
            case_sensitive=case_sensitive,
        )

    async def fs_write_text(
        self,
        lane_id: str,
        fencing_token: int,
        path: str,
        content: str,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        return await self.runtime.write_text(
            lane_id=lane_id,
            fencing_token=fencing_token,
            path=path,
            content=content,
            encoding=encoding,
        )

    async def fs_append_text(
        self,
        lane_id: str,
        fencing_token: int,
        path: str,
        content: str,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        return await self._operation(
            "fs.append_text",
            lane_id,
            fencing_token,
            path=path,
            content=content,
            encoding=encoding,
        )

    async def fs_mkdir(
        self,
        lane_id: str,
        fencing_token: int,
        path: str,
        parents: bool = True,
    ) -> dict[str, Any]:
        return await self._operation(
            "fs.mkdir",
            lane_id,
            fencing_token,
            path=path,
            parents=parents,
        )

    async def fs_move(
        self,
        lane_id: str,
        fencing_token: int,
        source: str,
        destination: str,
    ) -> dict[str, Any]:
        return await self._operation(
            "fs.move",
            lane_id,
            fencing_token,
            source=source,
            destination=destination,
        )

    async def fs_replace_text(
        self,
        lane_id: str,
        fencing_token: int,
        path: str,
        old_string: str,
        new_string: str,
        expected_count: int = 1,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        return await self._operation(
            "fs.replace_text",
            lane_id,
            fencing_token,
            path=path,
            old_string=old_string,
            new_string=new_string,
            expected_count=expected_count,
            encoding=encoding,
        )

    async def _operation(
        self,
        operation: str,
        lane_id: str,
        fencing_token: int,
        **body: Any,
    ) -> dict[str, Any]:
        await self.runtime.ensure_started()
        return await self.runtime.gateway.call_operation(
            operation,
            lane_id=lane_id,
            fencing_token=fencing_token,
            **body,
        )

    async def process_exec(
        self,
        lane_id: str,
        fencing_token: int,
        argv: list[str],
        cwd: str,
        timeout_s: float = 60.0,
    ) -> dict[str, Any]:
        return await self._operation(
            "process.exec",
            lane_id,
            fencing_token,
            argv=argv,
            cwd=cwd,
            timeout_s=timeout_s,
        )

    async def process_start(
        self,
        lane_id: str,
        fencing_token: int,
        argv: list[str],
        cwd: str,
        max_runtime_s: float = 900.0,
    ) -> dict[str, Any]:
        return await self._operation(
            "process.start",
            lane_id,
            fencing_token,
            argv=argv,
            cwd=cwd,
            max_runtime_s=max_runtime_s,
        )

    async def process_list(
        self,
        lane_id: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        return await self._operation(
            "process.list", lane_id, fencing_token
        )

    async def process_status(
        self,
        lane_id: str,
        fencing_token: int,
        process_handle: str,
    ) -> dict[str, Any]:
        return await self._operation(
            "process.status",
            lane_id,
            fencing_token,
            process_handle=process_handle,
        )

    async def process_output(
        self,
        lane_id: str,
        fencing_token: int,
        process_handle: str,
        stdout_offset: int = 0,
        stderr_offset: int = 0,
        max_bytes: int = 16_384,
    ) -> dict[str, Any]:
        return await self._operation(
            "process.output",
            lane_id,
            fencing_token,
            process_handle=process_handle,
            stdout_offset=stdout_offset,
            stderr_offset=stderr_offset,
            max_bytes=max_bytes,
        )

    async def process_terminate(
        self,
        lane_id: str,
        fencing_token: int,
        process_handle: str,
        grace_s: float = 2.0,
    ) -> dict[str, Any]:
        return await self._operation(
            "process.terminate",
            lane_id,
            fencing_token,
            process_handle=process_handle,
            grace_s=grace_s,
        )


def build_mcp_server(runtime: ControllerRuntime):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "MCP runtime is not installed; install the project with the mcp extra"
        ) from exc

    facade = VeraPortMCPFacade(runtime)
    mcp = FastMCP(
        "VeraMesh VeraPort",
        instructions=(
            "Authenticated VeraMesh workstation bridge with VeraRelay-capable "
            "edge routing. Tool discovery does not grant authority: controller "
            "policy, session capabilities, lane claims, fencing, and workstation "
            "local policy remain controlling."
        ),
    )

    @mcp.tool()
    async def machine_info() -> dict[str, Any]:
        """Read authenticated machine/session/path information."""
        return await facade.machine_info()

    @mcp.tool()
    async def lane_list() -> dict[str, Any]:
        """List lanes visible to the authenticated VeraPort session."""
        return await facade.lane_list()

    @mcp.tool()
    async def lane_open(
        lane_id: str,
        task_id: str,
        capabilities: list[str],
        claims: list[dict[str, str]],
        ttl_s: float = 300.0,
    ) -> dict[str, Any]:
        """Open a fenced lane inside the current session capability ceiling."""
        return await facade.lane_open(
            lane_id, task_id, capabilities, claims, ttl_s
        )

    @mcp.tool()
    async def lane_renew(
        lane_id: str,
        fencing_token: int,
        ttl_s: float = 300.0,
    ) -> dict[str, Any]:
        """Renew an existing fenced lane."""
        return await facade.lane_renew(
            lane_id, fencing_token, ttl_s
        )

    @mcp.tool()
    async def lane_close(
        lane_id: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        """Close a fenced lane and reap its managed processes."""
        return await facade.lane_close(lane_id, fencing_token)

    @mcp.tool()
    async def fs_read_text(
        lane_id: str,
        fencing_token: int,
        path: str,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        """Read bounded text under a workstation-allowed root."""
        return await facade.fs_read_text(
            lane_id, fencing_token, path, encoding
        )

    if "fs.read_bytes" in runtime.config.gateway_operations:
        @mcp.tool()
        async def fs_read_bytes(
            lane_id: str,
            fencing_token: int,
            path: str,
            offset: int = 0,
            max_bytes: int | None = None,
            expected_file_version: str | None = None,
        ) -> dict[str, Any]:
            """Read a bounded byte range under existing fs.read authority."""
            return await facade.fs_read_bytes(
                lane_id, fencing_token, path, offset,
                max_bytes, expected_file_version,
            )

    if "fs.stat" in runtime.config.gateway_operations:
        @mcp.tool()
        async def fs_stat(
            lane_id: str,
            fencing_token: int,
            path: str,
        ) -> dict[str, Any]:
            """Read bounded metadata without following the final symlink."""
            return await facade.fs_stat(
                lane_id, fencing_token, path
            )

    if "fs.list_dir" in runtime.config.gateway_operations:
        @mcp.tool()
        async def fs_list_dir(
            lane_id: str,
            fencing_token: int,
            path: str,
            offset: int = 0,
            max_entries: int = 200,
        ) -> dict[str, Any]:
            """List a directory deterministically with bounded pagination."""
            return await facade.fs_list_dir(
                lane_id, fencing_token, path, offset, max_entries
            )

    if "fs.search" in runtime.config.gateway_operations:
        @mcp.tool()
        async def fs_search(
            lane_id: str,
            fencing_token: int,
            root: str,
            query: str,
            offset: int = 0,
            max_results: int = 100,
            max_entries: int = 10_000,
            max_depth: int = 12,
            case_sensitive: bool = False,
        ) -> dict[str, Any]:
            """Search paths below a claimed root with explicit hard bounds."""
            return await facade.fs_search(
                lane_id, fencing_token, root, query, offset,
                max_results, max_entries, max_depth, case_sensitive,
            )

    if "fs.write_text" in runtime.config.gateway_operations:
        @mcp.tool()
        async def fs_write_text(
            lane_id: str,
            fencing_token: int,
            path: str,
            content: str,
            encoding: str = "utf-8",
        ) -> dict[str, Any]:
            """Atomically write text only when every authority layer permits it."""
            return await facade.fs_write_text(
                lane_id, fencing_token, path, content, encoding
            )

    if "fs.append_text" in runtime.config.gateway_operations:
        @mcp.tool()
        async def fs_append_text(
            lane_id: str,
            fencing_token: int,
            path: str,
            content: str,
            encoding: str = "utf-8",
        ) -> dict[str, Any]:
            """Append text under fs.write authority without enabling process execution."""
            return await facade.fs_append_text(
                lane_id, fencing_token, path, content, encoding
            )

    if "fs.mkdir" in runtime.config.gateway_operations:
        @mcp.tool()
        async def fs_mkdir(
            lane_id: str,
            fencing_token: int,
            path: str,
            parents: bool = True,
        ) -> dict[str, Any]:
            """Create a directory only inside a claimed workstation root."""
            return await facade.fs_mkdir(
                lane_id, fencing_token, path, parents
            )

    if "fs.move" in runtime.config.gateway_operations:
        @mcp.tool()
        async def fs_move(
            lane_id: str,
            fencing_token: int,
            source: str,
            destination: str,
        ) -> dict[str, Any]:
            """Move or rename a path when both ends are within write claims."""
            return await facade.fs_move(
                lane_id, fencing_token, source, destination
            )

    if "fs.replace_text" in runtime.config.gateway_operations:
        @mcp.tool()
        async def fs_replace_text(
            lane_id: str,
            fencing_token: int,
            path: str,
            old_string: str,
            new_string: str,
            expected_count: int = 1,
            encoding: str = "utf-8",
        ) -> dict[str, Any]:
            """Atomically replace an exact expected number of text matches."""
            return await facade.fs_replace_text(
                lane_id,
                fencing_token,
                path,
                old_string,
                new_string,
                expected_count,
                encoding,
            )

    if "process.exec" in runtime.config.gateway_operations:
        @mcp.tool()
        async def process_exec(
            lane_id: str,
            fencing_token: int,
            argv: list[str],
            cwd: str,
            timeout_s: float = 60.0,
        ) -> dict[str, Any]:
            """Run one bounded process without a shell."""
            return await facade.process_exec(
                lane_id, fencing_token, argv, cwd, timeout_s
            )

    if "process.start" in runtime.config.gateway_operations:
        @mcp.tool()
        async def process_start(
            lane_id: str,
            fencing_token: int,
            argv: list[str],
            cwd: str,
            max_runtime_s: float = 900.0,
        ) -> dict[str, Any]:
            """Start a VeraPort-managed process with an opaque handle and watchdog."""
            return await facade.process_start(
                lane_id, fencing_token, argv, cwd, max_runtime_s
            )

    if "process.list" in runtime.config.gateway_operations:
        @mcp.tool()
        async def process_list(
            lane_id: str,
            fencing_token: int,
        ) -> dict[str, Any]:
            """List only VeraPort-managed processes owned by this lane/fence."""
            return await facade.process_list(
                lane_id, fencing_token
            )

    if "process.status" in runtime.config.gateway_operations:
        @mcp.tool()
        async def process_status(
            lane_id: str,
            fencing_token: int,
            process_handle: str,
        ) -> dict[str, Any]:
            """Read status for a process owned by this lane/fence."""
            return await facade.process_status(
                lane_id, fencing_token, process_handle
            )

    if "process.output" in runtime.config.gateway_operations:
        @mcp.tool()
        async def process_output(
            lane_id: str,
            fencing_token: int,
            process_handle: str,
            stdout_offset: int = 0,
            stderr_offset: int = 0,
            max_bytes: int = 16_384,
        ) -> dict[str, Any]:
            """Read bounded stdout/stderr chunks from a managed process."""
            return await facade.process_output(
                lane_id, fencing_token, process_handle,
                stdout_offset, stderr_offset, max_bytes,
            )

    if "process.terminate" in runtime.config.gateway_operations:
        @mcp.tool()
        async def process_terminate(
            lane_id: str,
            fencing_token: int,
            process_handle: str,
            grace_s: float = 2.0,
        ) -> dict[str, Any]:
            """Terminate only a process owned by this lane/fence."""
            return await facade.process_terminate(
                lane_id, fencing_token, process_handle, grace_s
            )

    return mcp


def require_loopback_mcp_host(host: str) -> str:
    normalized = host.strip()
    if normalized.lower() == "localhost":
        return normalized
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError as exc:
        raise ValueError(
            "initial VeraPort MCP bind must be localhost or a literal loopback address"
        ) from exc
    if not address.is_loopback:
        raise ValueError(
            "initial VeraPort MCP bind must remain loopback-only; use a separately reviewed secure tunnel for remote exposure"
        )
    return normalized


def main() -> None:
    config_path = os.environ.get("VERAPORT_CONTROLLER_CONFIG")
    if not config_path:
        raise SystemExit("VERAPORT_CONTROLLER_CONFIG is required")

    config = ControllerConfig.load(config_path)
    runtime = ControllerRuntime(config)
    mcp = build_mcp_server(runtime)

    host = require_loopback_mcp_host(
        os.environ.get("VERAPORT_MCP_HOST", "127.0.0.1")
    )
    port = int(os.environ.get("VERAPORT_MCP_PORT", "17446"))
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        stateless_http=False,
    )


if __name__ == "__main__":
    main()
