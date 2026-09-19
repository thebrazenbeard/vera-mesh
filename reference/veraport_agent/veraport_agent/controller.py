from __future__ import annotations

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
    "process.exec",
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
        self._by_workstation: dict[str, dict[str, SessionEndpoint]] = {}

    def register(self, endpoint: SessionEndpoint) -> None:
        workstation = endpoint.binding.workstation_principal
        self._by_workstation.setdefault(workstation, {})[endpoint.endpoint_id] = endpoint

    def remove(self, workstation_principal: str, endpoint_id: str) -> None:
        group = self._by_workstation.get(workstation_principal)
        if group is None:
            return
        group.pop(endpoint_id, None)
        if not group:
            self._by_workstation.pop(workstation_principal, None)

    async def request(
        self,
        workstation_principal: str,
        request: dict[str, Any],
        *,
        now_ms: int,
        max_path_age_ms: int = 5_000,
    ) -> dict[str, Any]:
        request_id = request.get("request_id")
        operation = request.get("operation")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id is required")
        if not isinstance(operation, str) or not operation:
            raise ValueError("operation is required")

        candidates = self._current_endpoints(workstation_principal, now_ms=now_ms)
        attempted: set[str] = set()
        last_error: Exception | None = None

        while True:
            remaining = tuple(item for item in candidates if item.endpoint_id not in attempted)
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
                return await endpoint.channel.request(request)
            except StreamClosed as exc:
                last_error = exc
                mutating = operation in _MUTATING_OPERATIONS
                if mutating and not endpoint.durable_idempotency:
                    raise AmbiguousDelivery(
                        f"{request_id} lost transport after possible mutation; retry blocked without durable idempotency"
                    ) from exc
                continue

    def _current_endpoints(self, workstation_principal: str, *, now_ms: int) -> tuple[SessionEndpoint, ...]:
        result = []
        for endpoint in self._by_workstation.get(workstation_principal, {}).values():
            binding = endpoint.binding
            if now_ms >= binding.expires_at_ms:
                continue
            if binding.workstation_principal != workstation_principal:
                continue
            result.append(endpoint)
        return tuple(result)
