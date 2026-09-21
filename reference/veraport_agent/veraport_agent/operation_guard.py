from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from .core import ClaimMode, Lane, LaneRegistry


class LaneClosing(RuntimeError):
    code = "LANE_CLOSING"


class LaneDrainTimeout(RuntimeError):
    code = "LANE_DRAIN_TIMEOUT"


@dataclass
class _Activity:
    inflight: int = 0
    closing: bool = False


class LaneOperationCoordinator:
    """Linearize lane close against already-admitted local operations."""

    def __init__(
        self,
        registry: LaneRegistry,
        *,
        close_timeout_s: float = 10.0,
    ) -> None:
        if close_timeout_s <= 0:
            raise ValueError("close_timeout_s must be positive")
        self.registry = registry
        self.close_timeout_s = close_timeout_s
        self._condition = asyncio.Condition()
        self._activity: dict[tuple[str, int], _Activity] = {}

    @asynccontextmanager
    async def operation(
        self,
        lane_id: str,
        fencing_token: int,
        capability: str,
        *,
        resource_key: str | None = None,
        resource_mode: ClaimMode | None = None,
    ) -> AsyncIterator[None]:
        key = (lane_id, fencing_token)
        async with self._condition:
            state = self._activity.setdefault(key, _Activity())
            if state.closing:
                raise LaneClosing(lane_id)
            state.inflight += 1
        try:
            self.registry.authorize(
                lane_id,
                fencing_token,
                capability,
                resource_key=resource_key,
                resource_mode=resource_mode,
            )
            yield
        finally:
            async with self._condition:
                state = self._activity.get(key)
                if state is not None:
                    state.inflight = max(0, state.inflight - 1)
                    if state.inflight == 0 and not state.closing:
                        self._activity.pop(key, None)
                self._condition.notify_all()

    async def close_lane(
        self,
        lane_id: str,
        fencing_token: int,
        *,
        timeout_s: float | None = None,
    ) -> Lane:
        timeout = self.close_timeout_s if timeout_s is None else float(timeout_s)
        if timeout <= 0:
            raise ValueError("close timeout must be positive")
        key = (lane_id, fencing_token)

        async with self._condition:
            state = self._activity.setdefault(key, _Activity())
            state.closing = True
            deadline = asyncio.get_running_loop().time() + timeout
            while state.inflight:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise LaneDrainTimeout(
                        f"lane {lane_id} still has {state.inflight} in-flight operation(s)"
                    )
                try:
                    await asyncio.wait_for(
                        self._condition.wait(),
                        timeout=remaining,
                    )
                except TimeoutError as exc:
                    raise LaneDrainTimeout(
                        f"lane {lane_id} did not drain within {timeout}s"
                    ) from exc

        try:
            return self.registry.close(lane_id, fencing_token)
        finally:
            async with self._condition:
                self._activity.pop(key, None)
                self._condition.notify_all()
