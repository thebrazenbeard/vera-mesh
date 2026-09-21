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


class LogicalReadLaneCloseIncomplete(LogicalReadLaneError):
    code = "LOGICAL_READ_LANE_CLOSE_INCOMPLETE"


@dataclass
class LogicalReadLane:
    lane_id: str
    task_id: str
    claims: tuple[dict[str, str], ...]
    fencing_token: int
    expires_at_ms: int
    mirrors: dict[str, tuple[str, int]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)


class MirroredReadLaneRouter:
    def __init__(
        self,
        *,
        live_endpoints: Callable[[], Iterable[Any]],
        now_ms: Callable[[], int],
        request_id_factory: Callable[[], str],
        max_path_age_ms: int,
        request_timeout_s: float = 5.0,
        fence_epoch_factory: Callable[[], int] | None = None,
    ) -> None:
        if request_timeout_s <= 0:
            raise ValueError("request_timeout_s must be positive")
        epoch_factory = fence_epoch_factory or (lambda: secrets.randbits(96) or 1)
        epoch = epoch_factory()
        if type(epoch) is not int or epoch <= 0:
            raise ValueError("fence_epoch_factory must return a positive integer")
        self.live_endpoints = live_endpoints
        self.now_ms = now_ms
        self.request_id_factory = request_id_factory
        self.max_path_age_ms = max_path_age_ms
        self.request_timeout_s = request_timeout_s
        self._lanes: dict[str, LogicalReadLane] = {}
        self._fence_epoch = epoch
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
        return lane is not None and lane.fencing_token == fencing_token

    def _next_fence(self) -> int:
        self._last_fence += 1
        return (self._fence_epoch << 32) | self._last_fence

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
            last_error = None
            for endpoint in self._ordered_current_endpoints():
                try:
                    await self._ensure_mirror(lane, endpoint)
                    return {
                        "protocol_version": "veraport-v1",
                        "request_id": None,
                        "ok": True,
                        "result": {
                            "lane_id": lane_id,
                            "fencing_token": lane.fencing_token,
                            "logical_read_lane": True,
                        },
                    }
                except StreamClosed as exc:
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
        async with lane.lock:
            lane = self._require(lane_id, fencing_token)
            if ttl_s <= 0:
                raise ValueError("ttl_s must be positive")
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
            return {
                "protocol_version": "veraport-v1",
                "request_id": None,
                "ok": True,
                "result": {
                    "lane_id": lane_id,
                    "fencing_token": fencing_token,
                    "logical_read_lane": True,
                },
            }

    async def close(
        self,
        *,
        lane_id: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        lane = self._require(lane_id, fencing_token)
        async with lane.lock:
            lane = self._require(lane_id, fencing_token)
            endpoints = {
                item.config.endpoint_id: item
                for item in self._ordered_current_endpoints()
            }
            unresolved: list[str] = []
            for endpoint_id, (session_id, mirror_fence) in tuple(lane.mirrors.items()):
                endpoint = endpoints.get(endpoint_id)
                if endpoint is None or endpoint.binding.session_id != session_id:
                    unresolved.append(f"{endpoint_id}: mirror session unavailable")
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
                except StreamClosed as exc:
                    unresolved.append(f"{endpoint_id}: {exc}")
                    continue
                if response.get("ok") is not True:
                    unresolved.append(
                        f"{endpoint_id}: close rejected {response.get('error')!r}"
                    )
                    continue
                lane.mirrors.pop(endpoint_id, None)

            if unresolved:
                raise LogicalReadLaneCloseIncomplete("; ".join(unresolved))

            if self._lanes.get(lane_id) is lane:
                self._lanes.pop(lane_id, None)
            return {
                "protocol_version": "veraport-v1",
                "request_id": None,
                "ok": True,
                "result": {
                    "lane_id": lane_id,
                    "closed": True,
                    "logical_read_lane": True,
                },
            }

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
            last_transport_error = None
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

    def _require(self, lane_id: str, fencing_token: int) -> LogicalReadLane:
        lane = self._lanes.get(lane_id)
        if lane is None:
            raise LogicalReadLaneNotFound(lane_id)
        if lane.fencing_token != fencing_token:
            raise LogicalReadLaneStaleFence(
                f"expected logical fence {lane.fencing_token}, got {fencing_token}"
            )
        if self.now_ms() >= lane.expires_at_ms:
            self._lanes.pop(lane_id, None)
            raise LogicalReadLaneExpired(lane_id)
        return lane

    async def _request(
        self,
        endpoint: Any,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                endpoint.channel.request(request),
                timeout=self.request_timeout_s,
            )
        except TimeoutError as exc:
            raise StreamClosed(
                f"{endpoint.config.endpoint_id}: request timed out"
            ) from exc

    async def _ensure_mirror(self, lane: LogicalReadLane, endpoint: Any) -> int:
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
        lane.mirrors[endpoint.config.endpoint_id] = (
            endpoint.binding.session_id,
            fence,
        )
        return fence

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
