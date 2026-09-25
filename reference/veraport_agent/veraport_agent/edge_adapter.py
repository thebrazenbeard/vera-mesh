from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


class EdgeAdapterError(RuntimeError):
    code = "EDGE_ADAPTER_ERROR"


class EdgeUnauthenticated(EdgeAdapterError):
    code = "EDGE_UNAUTHENTICATED"


@dataclass(frozen=True)
class DurableDisposition:
    request_id: str
    state: str
    relay_receipt_id: str | None = None

    def __post_init__(self) -> None:
        if self.state not in {"QUEUED_NOT_EXECUTED", "REJECTED"}:
            raise ValueError("invalid durable disposition")


class LiveEdgeProxy:
    """Live proxy semantics: no store-and-forward completion fiction."""

    def __init__(
        self,
        forward: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        *,
        authenticated: bool,
    ) -> None:
        self.forward = forward
        self.authenticated = authenticated

    async def request(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self.authenticated:
            raise EdgeUnauthenticated("edge live path is not authenticated")
        return await self.forward(request)


class DurableRelayFallback:
    """Explicit asynchronous fallback; queue custody is not Lappy execution."""

    def __init__(
        self,
        submit: Callable[[dict[str, Any]], Awaitable[str]],
        *,
        authenticated: bool,
    ) -> None:
        self.submit = submit
        self.authenticated = authenticated

    async def enqueue(self, request: dict[str, Any]) -> DurableDisposition:
        if not self.authenticated:
            raise EdgeUnauthenticated("durable relay path is not authenticated")
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id is required")
        receipt = await self.submit(request)
        if not isinstance(receipt, str) or not receipt:
            raise EdgeAdapterError("relay did not return a custody receipt id")
        return DurableDisposition(
            request_id=request_id,
            state="QUEUED_NOT_EXECUTED",
            relay_receipt_id=receipt,
        )
