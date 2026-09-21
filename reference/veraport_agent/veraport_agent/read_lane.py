from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .stream import StreamClosed
from .synchrony import select_path


class LogicalReadLaneError(RuntimeError):
    code = "LOGICAL_READ_LANE_ERROR"


class LogicalReadLaneNotFound(LogicalReadLaneError):
    code = "LOGICAL_READ_LANE_NOT_FOUND"


class LogicalReadLaneStaleFence(LogicalReadLaneError):
    code = "LOGICAL_READ_LANE_STALE_FENCE"


class LogicalReadLaneExpired(LogicalReadLaneError):
    code = "LOGICAL_READ_LANE_EXPIRED"


class LogicalReadLaneCloseUnresolved(LogicalReadLaneError):
    code = "LOGICAL_READ_LANE_CLOSE_UNRESOLVED"


@dataclass
class LogicalReadLane:
    lane_id: str
    task_id: str
    claims: tuple[dict[str, str], ...]
    fencing_token: int
    expires_at_ms: int
    mirrors: dict[str, tuple[str, int]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False


class MirroredReadLaneRouter:
    def __init__(
        self,
        *,
        live_endpoints: Callable[[], Iterable[Any]],
        now_ms: Callable[[], int],
        request_id_factory: Callable[[], str],
        max_path_age_ms: int,
        operation_timeout_s: float = 15.0,
        fence_generation: int | None = None,
    ) -> None:
        if max_path_age_ms < 1:
            raise ValueError("max_path_age_ms must be positive")
        if operation_timeout_s <= 0:
            raise ValueError("operation_timeout_s must be positive")
        self.live_endpoints = live_endpoints
        self.now_ms = now_ms
        self.request_id_factory = request_id_factory
        self.max_path_age_ms = max_path_age_ms
        self.operation_timeout_s = operation_timeout_s
        self._lanes: dict[str, LogicalReadLane] = {}
        generation = secrets.randbits(63) if fence_generation is None else int(fence_generation)
        if generation <= 0:
            generation = 1
        self._fence_generation = generation
        self._last_fence = 0

    @staticmethod
    def supports_open(
        *,
        capabilities: list[str],
        claims: list[dict[str, str]],
        **_: Any,
    ) -> bool:
        return (
            set(capabilities) == {"fs.read"}
            and bool(claims)
            and all(
                isinstance(item, dict)
                and item.get("mode") == "read"
                and isinstance(item.get("key"), str)
                and item["key"].startswith("fs:")
                for item in claims
            )
        )

    def owns(self, lane_id: str, fencing_token: int) -> bool:
        lane = self._lanes.get(lane_id)
        return (
            lane is not None
            and not lane.closed
            and lane.fencing_token == fencing_token
        )

    def _next_fence(self) -> int:
        self._last_fence += 1
        return (self._fence_generation << 64) | self._last_fence

    async def open(
        self,
        *,
        lane_id: str,
        task_id: str,
        capabilities: list[str],
        claims: list[dict[str, str]],
        ttl_s: float = 300.0,
    ) -> dict[str, Any]:
        if not self.supports_open(capabilities=capabilities, claims=claims):
            raise LogicalReadLaneError("logical mirroring is read-only")
        if not lane_id or not task_id:
            raise ValueError("lane_id and task_id are required")
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        now = self.now_ms()
        current = self._lanes.get(lane_id)
        if current is not None and not current.closed and now < current.expires_at_ms:
            raise LogicalReadLaneError(f"logical lane already active: {lane_id}")
        lane = LogicalReadLane(
            lane_id=lane_id,
            task_id=task_id,
            claims=tuple(dict(item) for item in claims),
            fencing_token=self._next_fence(),
            expires_at_ms=now + int(ttl_s * 1000),
        )
        self._lanes[lane_id] = lane
        try:
            last_error: Exception | None = None
            for endpoint in self._ordered_current_endpoints():
                try:
                    await self._ensure_mirror(lane, endpoint)
                    return self._success(
                        "logical-open-",
                        {
                            "lane_id": lane_id,
                            "fencing_token": lane.fencing_token,
                            "logical_read_lane": True,
                        },
                    )
                except (StreamClosed, TimeoutError) as exc:
                    last_error = exc
            if last_error is not None:
                raise last_error
            raise LogicalReadLaneError("no endpoint could materialize read lane")
        except Exception:
            if self._lanes.get(lane_id) is lane:
                self._lanes.pop(lane_id, None)
            raise

    async def renew(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        ttl_s: float = 300.0,
    ) -> dict[str, Any]:
        lane = self._require(lane_id, fencing_token)
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        async with lane.lock:
            lane = self._require(lane_id, fencing_token)
            lane.expires_at_ms = self.now_ms() + int(ttl_s * 1000)
            endpoints = {
                item.config.endpoint_id: item
                for item in self._ordered_current_endpoints()
            }
            for endpoint_id, (session_id, mirror_fence) in tuple(lane.mirrors.items()):
                endpoint = endpoints.get(endpoint_id)
                if endpoint is None or endpoint.binding.session_id != session_id:
                    lane.mirrors.pop(endpoint_id, None)
                    continue
                try:
                    response = await self._request(
                        endpoint,
                        {
                            "protocol_version": "veraport-v1",
                            "request_id": "logical-renew-" + self.request_id_factory(),
                            "operation": "lane.renew",
                            "lane_id": lane.lane_id,
                            "fencing_token": mirror_fence,
                            "ttl_s": ttl_s,
                        },
                    )
                except (StreamClosed, TimeoutError):
                    lane.mirrors.pop(endpoint_id, None)
                    continue
                if response.get("ok") is not True:
                    raise LogicalReadLaneError(
                        f"{endpoint_id}: mirror lane.renew failed: "
                        + repr(response.get("error"))
                    )
            return self._success(
                "logical-renew-result-",
                {
                    "lane_id": lane_id,
                    "fencing_token": fencing_token,
                    "logical_read_lane": True,
                },
            )

    async def close(self, *, lane_id: str, fencing_token: int) -> dict[str, Any]:
        lane = self._require(lane_id, fencing_token)
        async with lane.lock:
            lane = self._require(lane_id, fencing_token)
            endpoints = {
                item.config.endpoint_id: item
                for item in self._ordered_current_endpoints()
            }
            unresolved: list[dict[str, str]] = []
            for endpoint_id, (session_id, mirror_fence) in tuple(lane.mirrors.items()):
                endpoint = endpoints.get(endpoint_id)
                if endpoint is None or endpoint.binding.session_id != session_id:
                    unresolved.append(
                        {"endpoint_id": endpoint_id, "reason": "MIRROR_SESSION_UNREACHABLE"}
                    )
                    continue
                try:
                    response = await self._request(
                        endpoint,
                        {
                            "protocol_version": "veraport-v1",
                            "request_id": "logical-close-" + self.request_id_factory(),
                            "operation": "lane.close",
                            "lane_id": lane.lane_id,
                            "fencing_token": mirror_fence,
                        },
                    )
                except (StreamClosed, TimeoutError):
                    unresolved.append(
                        {"endpoint_id": endpoint_id, "reason": "CLOSE_OUTCOME_UNKNOWN"}
                    )
                    continue
                if response.get("ok") is True:
                    lane.mirrors.pop(endpoint_id, None)
                    continue
                error = response.get("error")
                code = error.get("code") if isinstance(error, dict) else None
                if code == "LANE_NOT_FOUND":
                    lane.mirrors.pop(endpoint_id, None)
                    continue
                unresolved.append(
                    {"endpoint_id": endpoint_id, "reason": "APPLICATION_CLOSE_FAILED"}
                )

            if unresolved:
                return {
                    "protocol_version": "veraport-v1",
                    "request_id": "logical-close-result-" + self.request_id_factory(),
                    "ok": False,
                    "error": {
                        "code": LogicalReadLaneCloseUnresolved.code,
                        "message": "one or more read-lane mirrors remain unresolved",
                    },
                    "result": {
                        "lane_id": lane_id,
                        "closed": False,
                        "logical_read_lane": True,
                        "unresolved": unresolved,
                    },
                }

            lane.closed = True
            if self._lanes.get(lane_id) is lane:
                self._lanes.pop(lane_id, None)
            return self._success(
                "logical-close-result-",
                {"lane_id": lane_id, "closed": True, "logical_read_lane": True},
            )

    async def read_text(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        path: str,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        lane = self._require(lane_id, fencing_token)
        async with lane.lock:
            lane = self._require(lane_id, fencing_token)
            last_transport_error: Exception | None = None
            for endpoint in self._ordered_current_endpoints():
                try:
                    mirror_fence = await self._ensure_mirror(lane, endpoint)
                    response = await self._request(
                        endpoint,
                        {
                            "protocol_version": "veraport-v1",
                            "request_id": "logical-read-" + self.request_id_factory(),
                            "operation": "fs.read_text",
                            "lane_id": lane.lane_id,
                            "fencing_token": mirror_fence,
                            "path": path,
                            "encoding": encoding,
                        },
                    )
                except (StreamClosed, TimeoutError) as exc:
                    last_transport_error = exc
                    continue
                return response
            if last_transport_error is not None:
                raise last_transport_error
            raise LogicalReadLaneError("no current endpoint for read")

    def _require(self, lane_id: str, fencing_token: int) -> LogicalReadLane:
        lane = self._lanes.get(lane_id)
        if lane is None or lane.closed:
            raise LogicalReadLaneNotFound(lane_id)
        if lane.fencing_token != fencing_token:
            raise LogicalReadLaneStaleFence(
                f"expected logical fence {lane.fencing_token}, got {fencing_token}"
            )
        if self.now_ms() >= lane.expires_at_ms:
            lane.closed = True
            self._lanes.pop(lane_id, None)
            raise LogicalReadLaneExpired(lane_id)
        return lane

    async def _ensure_mirror(self, lane: LogicalReadLane, endpoint: Any) -> int:
        if lane.closed or self._lanes.get(lane.lane_id) is not lane:
            raise LogicalReadLaneNotFound(lane.lane_id)
        existing = lane.mirrors.get(endpoint.config.endpoint_id)
        if existing is not None and existing[0] == endpoint.binding.session_id:
            return existing[1]
        remaining_ms = lane.expires_at_ms - self.now_ms()
        if remaining_ms <= 0:
            raise LogicalReadLaneExpired(lane.lane_id)
        response = await self._request(
            endpoint,
            {
                "protocol_version": "veraport-v1",
                "request_id": "logical-open-" + self.request_id_factory(),
                "operation": "lane.open",
                "lane_id": lane.lane_id,
                "task_id": lane.task_id,
                "capabilities": ["fs.read"],
                "claims": [dict(item) for item in lane.claims],
                "ttl_s": remaining_ms / 1000.0,
            },
        )
        if response.get("ok") is not True:
            raise LogicalReadLaneError(
                f"{endpoint.config.endpoint_id}: mirror lane.open failed: "
                + repr(response.get("error"))
            )
        if lane.closed or self._lanes.get(lane.lane_id) is not lane:
            raise LogicalReadLaneNotFound(lane.lane_id)
        result = response.get("result")
        if not isinstance(result, dict) or type(result.get("fencing_token")) is not int:
            raise LogicalReadLaneError(
                f"{endpoint.config.endpoint_id}: missing mirror fencing token"
            )
        fence = result["fencing_token"]
        lane.mirrors[endpoint.config.endpoint_id] = (
            endpoint.binding.session_id,
            fence,
        )
        return fence

    async def _request(self, endpoint: Any, request: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.wait_for(
            endpoint.channel.request(request),
            timeout=self.operation_timeout_s,
        )

    def _success(self, prefix: str, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocol_version": "veraport-v1",
            "request_id": prefix + self.request_id_factory(),
            "ok": True,
            "result": result,
        }

    def _ordered_current_endpoints(self) -> tuple[Any, ...]:
        remaining = list(self.live_endpoints())
        ordered = []
        now = self.now_ms()
        while remaining:
            decision = select_path(
                tuple(item.path for item in remaining),
                now_ms=now,
                max_age_ms=self.max_path_age_ms,
            )
            if decision.selected is None:
                break
            endpoint = next(
                item
                for item in remaining
                if item.path.path_id == decision.selected.path_id
            )
            if now < endpoint.binding.expires_at_ms:
                ordered.append(endpoint)
            remaining = [item for item in remaining if item is not endpoint]
        return tuple(ordered)
