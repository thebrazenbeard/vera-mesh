from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable


class GatewayError(RuntimeError):
    code = "GATEWAY_ERROR"


class GatewayOperationDenied(GatewayError):
    code = "GATEWAY_OPERATION_DENIED"


READ_OPERATIONS = frozenset({
    "lane.list",
    "fs.read_text",
    "fs.read_bytes",
    "fs.stat",
    "fs.list_dir",
    "fs.search",
    "process.list",
    "process.status",
    "process.output",
})
MUTATING_OPERATIONS = frozenset({
    "lane.open",
    "lane.renew",
    "lane.close",
    "fs.write_text",
    "fs.append_text",
    "fs.mkdir",
    "fs.move",
    "fs.replace_text",
    "process.exec",
    "process.start",
    "process.terminate",
})
ALL_OPERATIONS = READ_OPERATIONS | MUTATING_OPERATIONS


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    operation: str
    mutating: bool


TOOL_DESCRIPTORS = (
    ToolDescriptor("list_lanes", "lane.list", False),
    ToolDescriptor("open_lane", "lane.open", True),
    ToolDescriptor("renew_lane", "lane.renew", True),
    ToolDescriptor("close_lane", "lane.close", True),
    ToolDescriptor("read_text", "fs.read_text", False),
    ToolDescriptor("read_bytes", "fs.read_bytes", False),
    ToolDescriptor("stat", "fs.stat", False),
    ToolDescriptor("list_dir", "fs.list_dir", False),
    ToolDescriptor("search", "fs.search", False),
    ToolDescriptor("write_text", "fs.write_text", True),
    ToolDescriptor("append_text", "fs.append_text", True),
    ToolDescriptor("make_directory", "fs.mkdir", True),
    ToolDescriptor("move_path", "fs.move", True),
    ToolDescriptor("replace_text", "fs.replace_text", True),
    ToolDescriptor("run_process", "process.exec", True),
    ToolDescriptor("start_process", "process.start", True),
    ToolDescriptor("list_processes", "process.list", False),
    ToolDescriptor("process_status", "process.status", False),
    ToolDescriptor("process_output", "process.output", False),
    ToolDescriptor("terminate_process", "process.terminate", True),
)


class VeraPortGateway:
    """Thin tool facade over an already-running HotSessionPool.

    Tool discoverability never grants authority. allowed_operations is a
    controller-side ceiling and workstation/session/lane policy can narrow it
    further.
    """

    def __init__(
        self,
        pool: Any,
        *,
        workstation_principal: str,
        controller_principal: str,
        allowed_operations: Iterable[str],
        now_ms: Callable[[], int] | None = None,
        request_id_factory: Callable[[], str] | None = None,
        max_path_age_ms: int = 5_000,
        request_timeout_s: float = 5.0,
    ) -> None:
        if not workstation_principal:
            raise ValueError("workstation_principal is required")
        if not controller_principal:
            raise ValueError("controller_principal is required")
        allowed = frozenset(allowed_operations)
        unknown = allowed - ALL_OPERATIONS
        if unknown:
            raise ValueError(f"unknown gateway operations: {sorted(unknown)}")
        self.pool = pool
        self.workstation_principal = workstation_principal
        self.controller_principal = controller_principal
        self.allowed_operations = allowed
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        if max_path_age_ms < 1:
            raise ValueError("max_path_age_ms must be positive")
        if request_timeout_s <= 0:
            raise ValueError("request_timeout_s must be positive")
        self.request_id_factory = request_id_factory or (lambda: uuid.uuid4().hex)
        self.max_path_age_ms = max_path_age_ms
        self.request_timeout_s = request_timeout_s

    async def call_operation(self, operation: str, **body: Any) -> dict[str, Any]:
        return await self._call(operation, body)

    async def list_lanes(self) -> dict[str, Any]:
        return await self._call("lane.list", {})

    async def open_lane(
        self,
        *,
        lane_id: str,
        task_id: str,
        capabilities: list[str],
        claims: list[dict[str, str]],
        ttl_s: float = 300.0,
    ) -> dict[str, Any]:
        return await self._call("lane.open", {
            "lane_id": lane_id,
            "task_id": task_id,
            "capabilities": capabilities,
            "claims": claims,
            "ttl_s": ttl_s,
        })

    async def renew_lane(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        ttl_s: float = 300.0,
    ) -> dict[str, Any]:
        return await self._call("lane.renew", {
            "lane_id": lane_id,
            "fencing_token": fencing_token,
            "ttl_s": ttl_s,
        })

    async def close_lane(
        self,
        *,
        lane_id: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        return await self._call("lane.close", {
            "lane_id": lane_id,
            "fencing_token": fencing_token,
        })

    async def read_text(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        path: str,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        return await self._call("fs.read_text", {
            "lane_id": lane_id,
            "fencing_token": fencing_token,
            "path": path,
            "encoding": encoding,
        })

    async def read_bytes(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        path: str,
        offset: int = 0,
        max_bytes: int | None = None,
        expected_file_version: str | None = None,
    ) -> dict[str, Any]:
        return await self._call("fs.read_bytes", {
            "lane_id": lane_id,
            "fencing_token": fencing_token,
            "path": path,
            "offset": offset,
            "max_bytes": max_bytes,
            "expected_file_version": expected_file_version,
        })

    async def write_text(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        path: str,
        content: str,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        return await self._call("fs.write_text", {
            "lane_id": lane_id,
            "fencing_token": fencing_token,
            "path": path,
            "content": content,
            "encoding": encoding,
        })

    async def run_process(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        argv: list[str],
        cwd: str,
        timeout_s: float = 60.0,
    ) -> dict[str, Any]:
        return await self._call("process.exec", {
            "lane_id": lane_id,
            "fencing_token": fencing_token,
            "argv": argv,
            "cwd": cwd,
            "timeout_s": timeout_s,
        })

    async def _call(
        self,
        operation: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        if operation not in self.allowed_operations:
            raise GatewayOperationDenied(operation)
        request_id = self.request_id_factory()
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id_factory must return a non-empty string")
        request = {
            "protocol_version": "veraport-v1",
            "request_id": request_id,
            "operation": operation,
            **body,
        }
        return await self.pool.request(
            self.workstation_principal,
            self.controller_principal,
            request,
            now_ms=self.now_ms(),
            max_path_age_ms=self.max_path_age_ms,
            request_timeout_s=self.request_timeout_s,
        )
