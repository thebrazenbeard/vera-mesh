from __future__ import annotations

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
            "Persistent authenticated VeraPort workstation bridge. "
            "Tool discovery does not grant authority: controller policy, "
            "session capabilities, lane claims, fencing, and workstation "
            "local policy remain controlling."
        ),
    )

    @mcp.tool()
    async def machine_info() -> dict[str, Any]:
        """Read current authenticated VeraPort machine/session/path information."""
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
            lane_id,
            task_id,
            capabilities,
            claims,
            ttl_s,
        )

    @mcp.tool()
    async def lane_renew(
        lane_id: str,
        fencing_token: int,
        ttl_s: float = 300.0,
    ) -> dict[str, Any]:
        """Renew an existing fenced lane."""
        return await facade.lane_renew(
            lane_id,
            fencing_token,
            ttl_s,
        )

    @mcp.tool()
    async def lane_close(
        lane_id: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        """Close an existing fenced lane."""
        return await facade.lane_close(
            lane_id,
            fencing_token,
        )

    @mcp.tool()
    async def fs_read_text(
        lane_id: str,
        fencing_token: int,
        path: str,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        """Read text under a workstation-allowed root with lane read authority."""
        return await facade.fs_read_text(
            lane_id,
            fencing_token,
            path,
            encoding,
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
            """Write only when every VeraPort authority layer permits it."""
            return await facade.fs_write_text(
                lane_id,
                fencing_token,
                path,
                content,
                encoding,
            )

    return mcp


def main() -> None:
    config_path = os.environ.get("VERAPORT_CONTROLLER_CONFIG")
    if not config_path:
        raise SystemExit("VERAPORT_CONTROLLER_CONFIG is required")

    config = ControllerConfig.load(config_path)
    runtime = ControllerRuntime(config)
    mcp = build_mcp_server(runtime)

    host = os.environ.get("VERAPORT_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("VERAPORT_MCP_PORT", "17446"))
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        stateless_http=False,
    )


if __name__ == "__main__":
    main()
