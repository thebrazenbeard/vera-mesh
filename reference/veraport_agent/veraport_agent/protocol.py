from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, AsyncIterator

from .core import ClaimMode, ResourceClaim, VeraPortError, LaneRegistry
from .executor import LocalExecutor, PathOutsideRoots
from .state import AgentStateStore
from .stream import DEFAULT_MAX_FRAME_BYTES, FrameTooLarge, encode_frame_payload


DURABLE_MUTATING_OPERATIONS = frozenset({
    "lane.open",
    "lane.renew",
    "lane.close",
    "fs.write_text",
    "process.exec",
})


class VeraPortAgent:
    """Transport-neutral request dispatcher for the reference VeraPort agent."""

    def __init__(
        self,
        registry: LaneRegistry,
        executor: LocalExecutor,
        state_store: AgentStateStore | None = None,
        *,
        max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
    ) -> None:
        if type(max_frame_bytes) is not int or max_frame_bytes < 1:
            raise ValueError("max_frame_bytes must be a positive integer")
        self.registry = registry
        self.executor = executor
        self.state_store = state_store
        self.max_frame_bytes = max_frame_bytes
        self._lane_locks: dict[str, asyncio.Lock] = {}
        self._lane_lock_users: dict[str, int] = {}
        self._lane_locks_guard = asyncio.Lock()

    @asynccontextmanager
    async def _lane_operation(self, lane_id: str) -> AsyncIterator[None]:
        """Serialize workstation operations with close for one internal lane."""
        async with self._lane_locks_guard:
            lock = self._lane_locks.get(lane_id)
            if lock is None:
                lock = asyncio.Lock()
                self._lane_locks[lane_id] = lock
            self._lane_lock_users[lane_id] = (
                self._lane_lock_users.get(lane_id, 0) + 1
            )

        await lock.acquire()
        try:
            yield
        finally:
            lock.release()
            async with self._lane_locks_guard:
                remaining = self._lane_lock_users.get(lane_id, 1) - 1
                if remaining <= 0:
                    self._lane_lock_users.pop(lane_id, None)
                    if self._lane_locks.get(lane_id) is lock:
                        self._lane_locks.pop(lane_id, None)
                else:
                    self._lane_lock_users[lane_id] = remaining

    async def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("request_id")
        operation = request.get("operation")
        try:
            if request.get("protocol_version") != "veraport-v1":
                raise ValueError(
                    "protocol_version must be veraport-v1"
                )
            if not isinstance(request_id, str) or not request_id:
                raise ValueError("request_id is required")
            if not isinstance(operation, str) or not operation:
                raise ValueError("operation is required")

            durable_mutation = (
                self.state_store is not None
                and operation in DURABLE_MUTATING_OPERATIONS
            )
            request_sha256: str | None = None
            if durable_mutation:
                request_sha256 = self._request_sha256(request)
                begun = self.state_store.begin_request(
                    request_id, request_sha256
                )
                if begun.disposition == "REPLAY":
                    assert begun.response is not None
                    replayed = dict(begun.response)
                    replayed["replay"] = {
                        "durable_evidence": True,
                        "current_state_not_implied": True,
                    }
                    return replayed

            try:
                result = await self._dispatch(operation, request)
                response = {
                    "protocol_version": "veraport-v1",
                    "request_id": request_id,
                    "ok": True,
                    "result": result,
                }
            except Exception as exc:
                code = getattr(
                    exc, "code", exc.__class__.__name__.upper()
                )
                response = {
                    "protocol_version": "veraport-v1",
                    "request_id": request_id,
                    "ok": False,
                    "error": {"code": code, "message": str(exc)},
                }

            if durable_mutation:
                assert request_sha256 is not None
                self.state_store.complete_request(
                    request_id,
                    request_sha256,
                    response,
                )
            return response
        except Exception as exc:
            code = getattr(
                exc, "code", exc.__class__.__name__.upper()
            )
            return {
                "protocol_version": "veraport-v1",
                "request_id": request_id,
                "ok": False,
                "error": {"code": code, "message": str(exc)},
            }

    async def _dispatch(
        self,
        operation: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        if operation == "lane.open":
            lane_id = str(request["lane_id"])
            async with self._lane_operation(lane_id):
                claims = tuple(
                    ResourceClaim(
                        str(item["key"]),
                        ClaimMode(str(item["mode"])),
                    )
                    for item in request.get("claims", [])
                )
                lane = self.registry.open_lane(
                    lane_id=lane_id,
                    task_id=str(request["task_id"]),
                    capabilities=frozenset(
                        str(item)
                        for item in request.get(
                            "capabilities", []
                        )
                    ),
                    claims=claims,
                    ttl_s=float(request.get("ttl_s", 300.0)),
                )
                return self._lane_json(lane)

        if operation == "lane.renew":
            lane_id = str(request["lane_id"])
            async with self._lane_operation(lane_id):
                lane = self.registry.renew(
                    lane_id,
                    int(request["fencing_token"]),
                    ttl_s=float(request.get("ttl_s", 300.0)),
                )
                return self._lane_json(lane)

        if operation == "lane.close":
            lane_id = str(request["lane_id"])
            async with self._lane_operation(lane_id):
                lane = self.registry.close(
                    lane_id,
                    int(request["fencing_token"]),
                )
                return {
                    "lane_id": lane.lane_id,
                    "closed": True,
                }

        if operation == "lane.list":
            result: dict[str, Any] = {
                "lanes": [
                    self._lane_json(lane)
                    for lane in self.registry.snapshot()
                ]
            }
            if self.state_store is not None:
                result["request_ledger"] = (
                    self.state_store.request_ledger_health()
                )
            return result

        if operation == "fs.read_text":
            lane_id = str(request["lane_id"])
            async with self._lane_operation(lane_id):
                content = await self.executor.read_text(
                    lane_id=lane_id,
                    fencing_token=int(request["fencing_token"]),
                    path=str(request["path"]),
                    encoding=str(
                        request.get("encoding", "utf-8")
                    ),
                )
                return {"content": content}

        if operation == "fs.read_bytes_chunk":
            lane_id = str(request["lane_id"])
            offset = request.get("offset", 0)
            max_bytes = request.get("max_bytes", 65_536)
            async with self._lane_operation(lane_id):
                chunk = await self.executor.read_bytes_chunk(
                    lane_id=lane_id,
                    fencing_token=int(request["fencing_token"]),
                    path=str(request["path"]),
                    offset=offset,
                    max_bytes=max_bytes,
                )
                return self._wire_safe_chunk_result(
                    request_id=str(request["request_id"]),
                    offset=chunk.offset,
                    data=chunk.data,
                    size=chunk.size,
                )

        if operation == "fs.write_text":
            lane_id = str(request["lane_id"])
            async with self._lane_operation(lane_id):
                await self.executor.write_text(
                    lane_id=lane_id,
                    fencing_token=int(request["fencing_token"]),
                    path=str(request["path"]),
                    content=str(request["content"]),
                    encoding=str(
                        request.get("encoding", "utf-8")
                    ),
                )
                return {"written": True}

        if operation == "process.exec":
            argv = request.get("argv")
            if not isinstance(argv, list):
                raise ValueError("argv must be a list")
            lane_id = str(request["lane_id"])
            async with self._lane_operation(lane_id):
                result = await self.executor.run_process(
                    lane_id=lane_id,
                    fencing_token=int(request["fencing_token"]),
                    argv=[str(item) for item in argv],
                    cwd=str(request["cwd"]),
                    timeout_s=float(
                        request.get("timeout_s", 60.0)
                    ),
                )
                return asdict(result)

        raise ValueError(f"unknown operation: {operation}")

    def _wire_safe_chunk_result(
        self,
        *,
        request_id: str,
        offset: int,
        data: bytes,
        size: int,
    ) -> dict[str, Any]:
        def make_result(length: int) -> dict[str, Any]:
            payload = data[:length]
            next_offset = offset + length
            return {
                "encoding": "base64",
                "offset": offset,
                "length": length,
                "next_offset": next_offset,
                "eof": next_offset >= size,
                "size": size,
                "content_b64": base64.b64encode(payload).decode("ascii"),
                "chunk_sha256": hashlib.sha256(payload).hexdigest(),
            }

        def fits(length: int) -> bool:
            response = {
                "protocol_version": "veraport-v1",
                "request_id": request_id,
                "ok": True,
                "result": make_result(length),
            }
            try:
                encode_frame_payload(
                    response,
                    max_frame_bytes=self.max_frame_bytes,
                )
                return True
            except FrameTooLarge:
                return False

        if not fits(0):
            raise FrameTooLarge(
                "response metadata exceeds configured frame policy"
            )
        if not data:
            return make_result(0)

        low = 0
        high = len(data)
        while low < high:
            mid = (low + high + 1) // 2
            if fits(mid):
                low = mid
            else:
                high = mid - 1
        if low == 0:
            raise FrameTooLarge(
                "configured frame policy cannot carry one chunk byte"
            )
        return make_result(low)

    @staticmethod
    def _request_sha256(request: dict[str, Any]) -> str:
        canonical = json.dumps(
            request,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _lane_json(lane) -> dict[str, Any]:
        return {
            "lane_id": lane.lane_id,
            "task_id": lane.task_id,
            "capabilities": sorted(lane.capabilities),
            "claims": [
                {"key": claim.key, "mode": claim.mode.value}
                for claim in lane.claims
            ],
            "fencing_token": lane.fencing_token,
            "expires_at_monotonic": lane.expires_at,
        }
