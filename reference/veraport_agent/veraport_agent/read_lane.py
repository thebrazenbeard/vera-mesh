from __future__ import annotations

import asyncio
import os
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


class LogicalReadLaneClosing(LogicalReadLaneError):
    code = "LOGICAL_READ_LANE_CLOSING"


class LogicalReadLaneCloseUncertain(LogicalReadLaneError):
    code = "LOGICAL_READ_LANE_CLOSE_UNCERTAIN"


@dataclass
class LogicalReadLane:
    lane_id: str
    task_id: str
    claims: tuple[dict[str, str], ...]
    fencing_token: int
    expires_at_ms: int
    mirrors: dict[str, tuple[str, int]] = field(default_factory=dict)
    closing: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)


class MirroredReadLaneRouter:
    """Read-only logical lanes mirrored into exact VeraPort application sessions.

    The logical fencing token contains a cryptographically random controller-lifetime
    generation in its high bits. A controller restart therefore cannot recreate an
    old (lane_id, fencing_token) pair merely because the process-local counter reset.
    """

    def __init__(
        self,
        *,
        live_endpoints: Callable[[], Iterable[Any]],
        now_ms: Callable[[], int],
        request_id_factory: Callable[[], str],
        max_path_age_ms: int,
        operation_timeout_s: float = 10.0,
        boot_nonce: int | None = None,
    ) -> None:
        if operation_timeout_s <= 0:
            raise ValueError("operation_timeout_s must be positive")
        self.live_endpoints = live_endpoints
        self.now_ms = now_ms
        self.request_id_factory = request_id_factory
        self.max_path_age_ms = max_path_age_ms
        self.operation_timeout_s = operation_timeout_s
        generated = int.from_bytes(os.urandom(8), "big") if boot_nonce is None else int(boot_nonce)
        self._boot_nonce = generated or 1
        if self._boot_nonce < 0 or self._boot_nonce >= (1 << 64):
            raise ValueError("boot_nonce must fit unsigned 64 bits")
        self._lanes: dict[str, LogicalReadLane] = {}
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

    def owns(self, lane_id: str, fencing_token: object) -> bool:
        lane = self._lanes.get(lane_id)
        return (
            lane is not None
            and type(fencing_token) is int
            and lane.fencing_token == fencing_token
        )

    def _next_fence(self) -> int:
        self._last_fence += 1
        return (self._boot_nonce << 64) | self._last_fence

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
        if current is not None and now < current.expires_at_ms:
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
            async with lane.lock:
                last_error: Exception | None = None
                for endpoint in self._ordered_current_endpoints():
                    try:
                        await self._ensure_mirror(lane, endpoint)
                        return self._ok(
                            "logical-open-" + self.request_id_factory(),
                            {
                                "lane_id": lane_id,
                                "fencing_token": lane.fencing_token,
                                "logical_read_lane": True,
                            },
                        )
                    except StreamClosed as exc:
                        last_error = exc
                if last_error is not None:
                    raise last_error
                raise LogicalReadLaneError("no endpoint could materialize read lane")
        except Exception:
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
            if lane.closing:
                raise LogicalReadLaneClosing(lane_id)
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
                    if response.get("ok") is not True:
                        lane.mirrors.pop(endpoint_id, None)
                except StreamClosed:
                    lane.mirrors.pop(endpoint_id, None)
            return self._ok(
                "logical-renew-" + self.request_id_factory(),
                {
                    "lane_id": lane_id,
                    "fencing_token": fencing_token,
                    "logical_read_lane": True,
                },
            )

    async def close(
        self,
        *,
        lane_id: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        lane = self._require(lane_id, fencing_token, allow_closing=True)
        async with lane.lock:
            lane = self._require(lane_id, fencing_token, allow_closing=True)
            lane.closing = True
            endpoints = {
                item.config.endpoint_id: item
                for item in self._ordered_current_endpoints()
            }
            unresolved: list[dict[str, str]] = []
            closed_ids: list[str] = []
            for endpoint_id, (session_id, mirror_fence) in tuple(lane.mirrors.items()):
                endpoint = endpoints.get(endpoint_id)
                if endpoint is None or endpoint.binding.session_id != session_id:
                    unresolved.append(
                        {"endpoint_id": endpoint_id, "reason": "SESSION_NOT_CURRENT"}
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
                except StreamClosed:
                    unresolved.append(
                        {"endpoint_id": endpoint_id, "reason": "TRANSPORT_UNKNOWN"}
                    )
                    continue
                if response.get("ok") is not True:
                    unresolved.append(
                        {"endpoint_id": endpoint_id, "reason": "APPLICATION_ERROR"}
                    )
                    continue
                lane.mirrors.pop(endpoint_id, None)
                closed_ids.append(endpoint_id)

            request_id = "logical-close-result-" + self.request_id_factory()
            if unresolved:
                return {
                    "protocol_version": "veraport-v1",
                    "request_id": request_id,
                    "ok": False,
                    "error": {
                        "code": LogicalReadLaneCloseUncertain.code,
                        "message": "one or more mirror closures remain unproven",
                    },
                    "result": {
                        "lane_id": lane_id,
                        "closed": False,
                        "logical_read_lane": True,
                        "closed_mirrors": sorted(closed_ids),
                        "unresolved_mirrors": unresolved,
                    },
                }

            self._lanes.pop(lane_id, None)
            return self._ok(
                request_id,
                {
                    "lane_id": lane_id,
                    "closed": True,
                    "logical_read_lane": True,
                    "closed_mirrors": sorted(closed_ids),
                    "unresolved_mirrors": [],
                },
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
            if lane.closing:
                raise LogicalReadLaneClosing(lane_id)
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
                except StreamClosed as exc:
                    last_transport_error = exc
                    continue
                return response
            if last_transport_error is not None:
                raise last_transport_error
            raise LogicalReadLaneError("no current endpoint for read")

    def _require(
        self,
        lane_id: str,
        fencing_token: int,
        *,
        allow_closing: bool = False,
    ) -> LogicalReadLane:
        lane = self._lanes.get(lane_id)
        if lane is None:
            raise LogicalReadLaneNotFound(lane_id)
        if type(fencing_token) is not int or lane.fencing_token != fencing_token:
            raise LogicalReadLaneStaleFence(
                f"expected logical fence {lane.fencing_token}, got {fencing_token}"
            )
        if self.now_ms() >= lane.expires_at_ms:
            self._lanes.pop(lane_id, None)
            raise LogicalReadLaneExpired(lane_id)
        if lane.closing and not allow_closing:
            raise LogicalReadLaneClosing(lane_id)
        return lane

    async def _ensure_mirror(self, lane: LogicalReadLane, endpoint: Any) -> int:
        if lane.closing:
            raise LogicalReadLaneClosing(lane.lane_id)
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
        result = response.get("result")
        if not isinstance(result, dict) or type(result.get("fencing_token")) is not int:
            raise LogicalReadLaneError(
                f"{endpoint.config.endpoint_id}: missing mirror fencing token"
            )
        fence = result["fencing_token"]
        if lane.closing:
            raise LogicalReadLaneClosing(lane.lane_id)
        lane.mirrors[endpoint.config.endpoint_id] = (endpoint.binding.session_id, fence)
        return fence

    async def _request(self, endpoint: Any, request: dict[str, Any]) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                endpoint.channel.request(request),
                timeout=self.operation_timeout_s,
            )
        except TimeoutError as exc:
            raise StreamClosed(
                f"{endpoint.config.endpoint_id}: operation deadline exceeded"
            ) from exc

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

    @staticmethod
    def _ok(request_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocol_version": "veraport-v1",
            "request_id": request_id,
            "ok": True,
            "result": result,
        }
