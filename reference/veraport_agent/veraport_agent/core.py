from __future__ import annotations

import os
import time
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from typing import Callable


class VeraPortError(RuntimeError):
    code = "VERAPORT_ERROR"


class CapabilityDenied(VeraPortError):
    code = "CAPABILITY_DENIED"


class CollisionBlocked(VeraPortError):
    code = "COLLISION_BLOCKED"


class LaneNotFound(VeraPortError):
    code = "LANE_NOT_FOUND"


class StaleFence(VeraPortError):
    code = "STALE_FENCE"


class LaneExpired(VeraPortError):
    code = "LANE_EXPIRED"


class ClaimMode(StrEnum):
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True, order=True)
class ResourceClaim:
    key: str
    mode: ClaimMode

    def __post_init__(self) -> None:
        if not self.key or ":" not in self.key:
            raise ValueError("resource claim key must be a namespaced non-empty string")


@dataclass(frozen=True)
class Lane:
    lane_id: str
    task_id: str
    capabilities: frozenset[str]
    claims: tuple[ResourceClaim, ...]
    fencing_token: int
    expires_at: float


class LaneRegistry:
    """Concurrent logical execution lanes with capability narrowing and fencing.

    Claims are intentionally explicit.  A lane may only use capabilities present
    in the session ceiling, and conflicting resource claims cannot coexist.
    Fencing tokens prevent a stale lane holder from resuming after expiry/reopen.
    """

    def __init__(
        self,
        session_capabilities: set[str] | frozenset[str],
        *,
        max_lanes: int = 32,
        fence_allocator: Callable[[], int] | None = None,
    ) -> None:
        if max_lanes < 1:
            raise ValueError("max_lanes must be positive")
        self._session_capabilities = frozenset(session_capabilities)
        self._max_lanes = max_lanes
        self._lanes: dict[str, Lane] = {}
        self._last_fence = 0
        self._fence_allocator = fence_allocator
        self._lock = RLock()

    @property
    def session_capabilities(self) -> frozenset[str]:
        return self._session_capabilities

    def open_lane(
        self,
        *,
        lane_id: str,
        task_id: str,
        capabilities: set[str] | frozenset[str],
        claims: tuple[ResourceClaim, ...] = (),
        ttl_s: float = 300.0,
        now: float | None = None,
    ) -> Lane:
        if not lane_id.strip() or not task_id.strip():
            raise ValueError("lane_id and task_id are required")
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        requested = frozenset(capabilities)
        if not requested.issubset(self._session_capabilities):
            extra = sorted(requested - self._session_capabilities)
            raise CapabilityDenied(f"lane requested capabilities outside session ceiling: {extra}")
        claim_tuple = tuple(sorted(set(claims)))
        current_time = time.monotonic() if now is None else now

        with self._lock:
            self._reap_expired_locked(current_time)
            if lane_id in self._lanes:
                raise CollisionBlocked(f"lane id already active: {lane_id}")
            if len(self._lanes) >= self._max_lanes:
                raise CollisionBlocked("max active lanes reached")
            for existing in self._lanes.values():
                conflict = self._first_conflict(claim_tuple, existing.claims)
                if conflict is not None:
                    raise CollisionBlocked(
                        f"resource collision with lane {existing.lane_id}: {conflict}"
                    )
            if self._fence_allocator is None:
                self._last_fence += 1
                fencing_token = self._last_fence
            else:
                fencing_token = self._fence_allocator()
                if fencing_token <= self._last_fence:
                    raise RuntimeError("fence allocator did not advance monotonically")
                self._last_fence = fencing_token
            lane = Lane(
                lane_id=lane_id,
                task_id=task_id,
                capabilities=requested,
                claims=claim_tuple,
                fencing_token=fencing_token,
                expires_at=current_time + ttl_s,
            )
            self._lanes[lane_id] = lane
            return lane

    def renew(self, lane_id: str, fencing_token: int, *, ttl_s: float = 300.0, now: float | None = None) -> Lane:
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        current_time = time.monotonic() if now is None else now
        with self._lock:
            lane = self._require_lane_locked(lane_id, fencing_token, current_time)
            updated = Lane(
                lane_id=lane.lane_id,
                task_id=lane.task_id,
                capabilities=lane.capabilities,
                claims=lane.claims,
                fencing_token=lane.fencing_token,
                expires_at=current_time + ttl_s,
            )
            self._lanes[lane_id] = updated
            return updated

    def close(self, lane_id: str, fencing_token: int, *, now: float | None = None) -> Lane:
        current_time = time.monotonic() if now is None else now
        with self._lock:
            lane = self._require_lane_locked(lane_id, fencing_token, current_time)
            del self._lanes[lane_id]
            return lane

    def authorize(
        self,
        lane_id: str,
        fencing_token: int,
        capability: str,
        *,
        resource_key: str | None = None,
        resource_mode: ClaimMode | None = None,
        now: float | None = None,
    ) -> Lane:
        current_time = time.monotonic() if now is None else now
        with self._lock:
            lane = self._require_lane_locked(lane_id, fencing_token, current_time)
            if capability not in lane.capabilities:
                raise CapabilityDenied(f"lane lacks capability: {capability}")
            if resource_key is not None:
                if resource_mode is None:
                    raise ValueError("resource_mode is required with resource_key")
                if not any(self._claim_covers(claim, resource_key, resource_mode) for claim in lane.claims):
                    raise CapabilityDenied(
                        f"lane lacks {resource_mode.value} claim covering resource: {resource_key}"
                    )
            return lane

    def snapshot(self, *, now: float | None = None) -> tuple[Lane, ...]:
        current_time = time.monotonic() if now is None else now
        with self._lock:
            self._reap_expired_locked(current_time)
            return tuple(sorted(self._lanes.values(), key=lambda lane: lane.lane_id))

    def reap_expired(self, *, now: float | None = None) -> tuple[Lane, ...]:
        current_time = time.monotonic() if now is None else now
        with self._lock:
            return self._reap_expired_locked(current_time)

    def _require_lane_locked(self, lane_id: str, fencing_token: int, now: float) -> Lane:
        lane = self._lanes.get(lane_id)
        if lane is None:
            raise LaneNotFound(lane_id)
        if lane.fencing_token != fencing_token:
            raise StaleFence(f"expected fence {lane.fencing_token}, got {fencing_token}")
        if now >= lane.expires_at:
            del self._lanes[lane_id]
            raise LaneExpired(lane_id)
        return lane

    def _reap_expired_locked(self, now: float) -> tuple[Lane, ...]:
        expired = tuple(lane for lane in self._lanes.values() if now >= lane.expires_at)
        for lane in expired:
            self._lanes.pop(lane.lane_id, None)
        return tuple(sorted(expired, key=lambda lane: lane.lane_id))

    @classmethod
    def _first_conflict(
        cls,
        left: tuple[ResourceClaim, ...],
        right: tuple[ResourceClaim, ...],
    ) -> str | None:
        for a in left:
            for b in right:
                if cls._resources_overlap(a.key, b.key) and ClaimMode.WRITE in {a.mode, b.mode}:
                    return f"{a.key}({a.mode.value}) vs {b.key}({b.mode.value})"
        return None

    @staticmethod
    def _resources_overlap(left: str, right: str) -> bool:
        l_namespace, l_value = left.split(":", 1)
        r_namespace, r_value = right.split(":", 1)
        if l_namespace != r_namespace:
            return False
        l_value = l_value.rstrip("/")
        r_value = r_value.rstrip("/")
        if os.name == "nt":
            l_value = l_value.casefold()
            r_value = r_value.casefold()
        return (
            l_value == r_value
            or l_value.startswith(r_value + "/")
            or r_value.startswith(l_value + "/")
        )

    @classmethod
    def _claim_covers(cls, claim: ResourceClaim, resource_key: str, requested_mode: ClaimMode) -> bool:
        if requested_mode is ClaimMode.WRITE and claim.mode is not ClaimMode.WRITE:
            return False
        claim_namespace, claim_value = claim.key.split(":", 1)
        resource_namespace, resource_value = resource_key.split(":", 1)
        if claim_namespace != resource_namespace:
            return False
        claim_value = claim_value.rstrip("/")
        resource_value = resource_value.rstrip("/")
        if os.name == "nt":
            claim_value = claim_value.casefold()
            resource_value = resource_value.casefold()
        return resource_value == claim_value or resource_value.startswith(claim_value + "/")
