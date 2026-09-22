from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, Any

from .hot_session import SessionBinding
from .synchrony import PathObservation, select_path
from .stream import StreamClosed


class ControllerRoutingError(RuntimeError):
    code = "CONTROLLER_ROUTING_ERROR"


class NoCurrentPath(ControllerRoutingError):
    code = "NO_CURRENT_PATH"


class AmbiguousDelivery(ControllerRoutingError):
    code = "AMBIGUOUS_DELIVERY"


class RequestChannel(Protocol):
    async def request(self, request: dict[str, Any]) -> dict[str, Any]: ...


_MUTATING_OPERATIONS = frozenset({
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


@dataclass(frozen=True)
class SessionEndpoint:
    endpoint_id: str
    binding: SessionBinding
    path: PathObservation
    channel: RequestChannel
    durable_idempotency: bool

    def __post_init__(self) -> None:
        if not self.endpoint_id:
            raise ValueError("endpoint_id is required")
        if not self.path.authenticated:
            raise ValueError("session endpoint path must already be authenticated")


class HotSessionPool:
    """Select current hot paths and preserve mutation safety across failover."""

    def __init__(self) -> None:
        self._by_route: dict[tuple[str, str], dict[str, SessionEndpoint]] = {}

    @staticmethod
    def _route_key(binding: SessionBinding) -> tuple[str, str]:
        return (binding.workstation_principal, binding.controller_principal)

    def register(self, endpoint: SessionEndpoint) -> None:
        route = self._route_key(endpoint.binding)
        self._by_route.setdefault(route, {})[endpoint.endpoint_id] = endpoint

    def remove(
        self,
        workstation_principal: str,
        controller_principal: str,
        endpoint_id: str,
    ) -> None:
        route = (workstation_principal, controller_principal)
        group = self._by_route.get(route)
        if group is None:
            return
        group.pop(endpoint_id, None)
        if not group:
            self._by_route.pop(route, None)

    async def request(
        self,
        workstation_principal: str,
        controller_principal: str,
        request: dict[str, Any],
        *,
        now_ms: int,
        max_path_age_ms: int = 5_000,
        request_timeout_s: float = 5.0,
    ) -> dict[str, Any]:
        request_id = request.get("request_id")
        operation = request.get("operation")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id is required")
        if not isinstance(operation, str) or not operation:
            raise ValueError("operation is required")
        if request_timeout_s <= 0:
            raise ValueError("request_timeout_s must be positive")

        candidates = self._current_endpoints(
            workstation_principal,
            controller_principal,
            now_ms=now_ms,
        )
        attempted: set[str] = set()
        last_error: Exception | None = None
        mutation_retry_session_id: str | None = None

        while True:
            remaining = tuple(
                item
                for item in candidates
                if item.endpoint_id not in attempted
                and (
                    mutation_retry_session_id is None
                    or (
                        item.binding.session_id == mutation_retry_session_id
                        and item.durable_idempotency
                    )
                )
            )
            decision = select_path(
                tuple(item.path for item in remaining),
                now_ms=now_ms,
                max_age_ms=max_path_age_ms,
            )
            if decision.selected is None:
                if last_error is not None:
                    raise last_error
                raise NoCurrentPath(workstation_principal)
            endpoint = next(item for item in remaining if item.path.path_id == decision.selected.path_id)
            attempted.add(endpoint.endpoint_id)
            try:
                return await asyncio.wait_for(
                    endpoint.channel.request(request),
                    timeout=request_timeout_s,
                )
            except (StreamClosed, TimeoutError) as exc:
                transport_error = (
                    exc
                    if isinstance(exc, StreamClosed)
                    else StreamClosed(
                        f"{request_id} timed out on {endpoint.endpoint_id}"
                    )
                )
                last_error = transport_error
                mutating = operation in _MUTATING_OPERATIONS
                if mutating:
                    if not endpoint.durable_idempotency:
                        raise AmbiguousDelivery(
                            f"{request_id} lost transport after possible mutation; retry blocked without durable idempotency"
                        ) from transport_error
                    mutation_retry_session_id = endpoint.binding.session_id
                    compatible = any(
                        item.endpoint_id not in attempted
                        and item.binding.session_id == mutation_retry_session_id
                        and item.durable_idempotency
                        for item in candidates
                    )
                    if not compatible:
                        raise AmbiguousDelivery(
                            f"{request_id} lost transport after possible mutation; no same-session durable failover path"
                        ) from transport_error
                continue

    def _current_endpoints(
        self,
        workstation_principal: str,
        controller_principal: str,
        *,
        now_ms: int,
    ) -> tuple[SessionEndpoint, ...]:
        result = []
        route = (workstation_principal, controller_principal)
        for endpoint in self._by_route.get(route, {}).values():
            binding = endpoint.binding
            if now_ms >= binding.expires_at_ms:
                continue
            if (
                binding.workstation_principal != workstation_principal
                or binding.controller_principal != controller_principal
            ):
                continue
            result.append(endpoint)
        return tuple(result)
