from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

from .controller_runtime import ControllerConfig, ControllerRuntime


class VeraPortMCPAdapter:
    """Thin ChatGPT/MCP projection over one persistent ControllerRuntime."""

    def __init__(self, runtime: ControllerRuntime) -> None:
        self.runtime = runtime

    async def _gateway(self):
        await self.runtime.ensure_current()
        assert self.runtime.gateway is not None
        return self.runtime.gateway

    async def machine_info(self) -> dict[str, Any]:
        await self.runtime.ensure_current()
        return self.runtime.machine_info()

    async def lane_list(self) -> dict[str, Any]:
        return await (await self._gateway()).list_lanes()

    async def lane_open(self, lane_id: str, task_id: str, capabilities: list[str],
                        claims: list[dict[str, str]], ttl_s: float = 300.0) -> dict[str, Any]:
        return await (await self._gateway()).open_lane(
            lane_id=lane_id, task_id=task_id, capabilities=capabilities,
            claims=claims, ttl_s=ttl_s,
        )

    async def lane_renew(self, lane_id: str, fencing_token: int,
                         ttl_s: float = 300.0) -> dict[str, Any]:
        return await (await self._gateway()).renew_lane(
            lane_id=lane_id, fencing_token=fencing_token, ttl_s=ttl_s,
        )

    async def lane_close(self, lane_id: str, fencing_token: int) -> dict[str, Any]:
        return await (await self._gateway()).close_lane(
            lane_id=lane_id, fencing_token=fencing_token,
        )

    async def fs_read_text(self, lane_id: str, fencing_token: int, path: str,
                           encoding: str = "utf-8") -> dict[str, Any]:
        return await (await self._gateway()).read_text(
            lane_id=lane_id, fencing_token=fencing_token, path=path, encoding=encoding,
        )

    async def fs_write_text(self, lane_id: str, fencing_token: int, path: str,
                            content: str, encoding: str = "utf-8") -> dict[str, Any]:
        return await (await self._gateway()).write_text(
            lane_id=lane_id, fencing_token=fencing_token, path=path,
            content=content, encoding=encoding,
        )

    async def process_exec(self, lane_id: str, fencing_token: int, argv: list[str],
                           cwd: str, timeout_s: float = 60.0) -> dict[str, Any]:
        return await (await self._gateway()).run_process(
            lane_id=lane_id, fencing_token=fencing_token, argv=argv,
            cwd=cwd, timeout_s=timeout_s,
        )

    async def process_start(self, lane_id: str, fencing_token: int,
                            argv: list[str], cwd: str) -> dict[str, Any]:
        return await (await self._gateway()).call_operation(
            "process.start", lane_id=lane_id, fencing_token=fencing_token,
            argv=argv, cwd=cwd,
        )

    async def process_list(self, lane_id: str, fencing_token: int) -> dict[str, Any]:
        return await (await self._gateway()).call_operation(
            "process.list", lane_id=lane_id, fencing_token=fencing_token,
        )

    async def process_status(self, lane_id: str, fencing_token: int,
                             process_handle: str) -> dict[str, Any]:
        return await (await self._gateway()).call_operation(
            "process.status", lane_id=lane_id, fencing_token=fencing_token,
            process_handle=process_handle,
        )

    async def process_output(self, lane_id: str, fencing_token: int,
                             process_handle: str) -> dict[str, Any]:
        return await (await self._gateway()).call_operation(
            "process.output", lane_id=lane_id, fencing_token=fencing_token,
            process_handle=process_handle,
        )

    async def process_terminate(self, lane_id: str, fencing_token: int,
                                process_handle: str, grace_s: float = 2.0) -> dict[str, Any]:
        return await (await self._gateway()).call_operation(
            "process.terminate", lane_id=lane_id, fencing_token=fencing_token,
            process_handle=process_handle, grace_s=grace_s,
        )


_RUNTIME: ControllerRuntime | None = None
_ADAPTER: VeraPortMCPAdapter | None = None
_RUNTIME_LOCK = asyncio.Lock()


async def adapter_from_env() -> VeraPortMCPAdapter:
    global _RUNTIME, _ADAPTER
    if _ADAPTER is not None:
        return _ADAPTER
    async with _RUNTIME_LOCK:
        if _ADAPTER is not None:
            return _ADAPTER
        path = os.environ.get("VERAPORT_MCP_CONFIG")
        if not path:
            raise RuntimeError("VERAPORT_MCP_CONFIG is required")
        _RUNTIME = ControllerRuntime(ControllerConfig.load(Path(path)))
        await _RUNTIME.start()
        _ADAPTER = VeraPortMCPAdapter(_RUNTIME)
        return _ADAPTER


def build_server():
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("VeraPort")

    @mcp.tool()
    async def machine_info() -> dict[str, Any]:
        """Current controller/session/path information. Presence grants no authority."""
        return await (await adapter_from_env()).machine_info()

    @mcp.tool()
    async def lane_list() -> dict[str, Any]:
        return await (await adapter_from_env()).lane_list()

    @mcp.tool()
    async def lane_open(lane_id: str, task_id: str, capabilities: list[str],
                        claims: list[dict[str, str]], ttl_s: float = 300.0) -> dict[str, Any]:
        return await (await adapter_from_env()).lane_open(
            lane_id, task_id, capabilities, claims, ttl_s
        )

    @mcp.tool()
    async def lane_renew(lane_id: str, fencing_token: int,
                         ttl_s: float = 300.0) -> dict[str, Any]:
        return await (await adapter_from_env()).lane_renew(lane_id, fencing_token, ttl_s)

    @mcp.tool()
    async def lane_close(lane_id: str, fencing_token: int) -> dict[str, Any]:
        return await (await adapter_from_env()).lane_close(lane_id, fencing_token)

    @mcp.tool()
    async def fs_read_text(lane_id: str, fencing_token: int, path: str,
                           encoding: str = "utf-8") -> dict[str, Any]:
        return await (await adapter_from_env()).fs_read_text(
            lane_id, fencing_token, path, encoding
        )

    @mcp.tool()
    async def fs_write_text(lane_id: str, fencing_token: int, path: str,
                            content: str, encoding: str = "utf-8") -> dict[str, Any]:
        return await (await adapter_from_env()).fs_write_text(
            lane_id, fencing_token, path, content, encoding
        )

    @mcp.tool()
    async def process_exec(lane_id: str, fencing_token: int, argv: list[str],
                           cwd: str, timeout_s: float = 60.0) -> dict[str, Any]:
        return await (await adapter_from_env()).process_exec(
            lane_id, fencing_token, argv, cwd, timeout_s
        )

    @mcp.tool()
    async def process_start(lane_id: str, fencing_token: int,
                            argv: list[str], cwd: str) -> dict[str, Any]:
        return await (await adapter_from_env()).process_start(
            lane_id, fencing_token, argv, cwd
        )

    @mcp.tool()
    async def process_list(lane_id: str, fencing_token: int) -> dict[str, Any]:
        return await (await adapter_from_env()).process_list(lane_id, fencing_token)

    @mcp.tool()
    async def process_status(lane_id: str, fencing_token: int,
                             process_handle: str) -> dict[str, Any]:
        return await (await adapter_from_env()).process_status(
            lane_id, fencing_token, process_handle
        )

    @mcp.tool()
    async def process_output(lane_id: str, fencing_token: int,
                             process_handle: str) -> dict[str, Any]:
        return await (await adapter_from_env()).process_output(
            lane_id, fencing_token, process_handle
        )

    @mcp.tool()
    async def process_terminate(lane_id: str, fencing_token: int,
                                process_handle: str, grace_s: float = 2.0) -> dict[str, Any]:
        return await (await adapter_from_env()).process_terminate(
            lane_id, fencing_token, process_handle, grace_s
        )

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17446)
    args = parser.parse_args()
    server = build_server()
    server.run(
        transport="streamable-http",
        host=args.host,
        port=args.port,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
