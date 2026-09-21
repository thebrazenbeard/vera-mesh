from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable


class GatewayError(RuntimeError):
    code = "GATEWAY_ERROR"


class GatewayOperationDenied(GatewayError):
    code = "GATEWAY_OPERATION_DENIED"


READ_OPERATIONS = frozenset({"lane.list", "fs.read_text"})
MUTATING_OPERATIONS = frozenset({
    "lane.open",
    "lane.renew",
    "lane.close",
    "fs.write_text",
    "process.exec",
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
    ToolDescriptor("write_text", "fs.write_text", True),
    ToolDescriptor("run_process", "process.exec", True),
)


class VeraPortGateway:
    """Thin tool facade over an already-running HotSessionPool.

    It owns no workstation connection and no execution state. Its job is
    request validation, stable request-ID creation, and projection into the
    persistent session pool.
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
        operation_timeout_s: float = 15.0,
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
        if max_path_age_ms < 1:
            raise ValueError("max_path_age_ms must be positive")
        if operation_timeout_s <= 0:
            raise ValueError("operation_timeout_s must be positive")
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.request_id_factory = request_id_factory or (lambda: uuid.uuid4().hex)
        self.max_path_age_ms = max_path_age_ms
        self.operation_timeout_s = operation_timeout_s

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

    async def renew_lane(self, *, lane_id: str, fencing_token: int, ttl_s: float = 300.0) -> dict[str, Any]:
        return await self._call("lane.renew", {
            "lane_id": lane_id,
            "fencing_token": fencing_token,
            "ttl_s": ttl_s,
        })

    async def close_lane(self, *, lane_id: str, fencing_token: int) -> dict[str, Any]:
        return await self._call("lane.close", {
            "lane_id": lane_id,
            "fencing_token": fencing_token,
        })

    async def read_text(self, *, lane_id: str, fencing_token: int, path: str, encoding: str = "utf-8") -> dict[str, Any]:
        return await self._call("fs.read_text", {
            "lane_id": lane_id,
            "fencing_token": fencing_token,
            "path": path,
            "encoding": encoding,
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

    async def _call(self, operation: str, body: dict[str, Any]) -> dict[str, Any]:
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
            operation_timeout_s=self.operation_timeout_s,
        )
