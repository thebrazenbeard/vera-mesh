from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .hot_session import SessionBinding


class SessionRuntimeError(RuntimeError):
    code = "SESSION_RUNTIME_ERROR"


class SessionExpired(SessionRuntimeError):
    code = "SESSION_EXPIRED"


class SessionCapabilityDenied(SessionRuntimeError):
    code = "SESSION_CAPABILITY_DENIED"


def _lane_prefix(binding: SessionBinding) -> str:
    return binding.session_id + "::"


def _request_prefix(binding: SessionBinding) -> str:
    # Stable across transport/application reconnect for one controller so exact
    # request IDs retain durable idempotency across direct/edge path changes.
    return binding.controller_principal + "::"


@dataclass
class SessionBoundHandler:
    binding: SessionBinding
    agent: Any
    now_ms: Callable[[], int]

    async def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.now_ms() >= self.binding.expires_at_ms:
            return self._error(request.get("request_id"), SessionExpired.code, "VeraPort session expired")
        if not isinstance(request, dict):
            return self._error(None, "INVALID_REQUEST", "request must be an object")

        external_request_id = request.get("request_id")
        operation = request.get("operation")
        if not isinstance(external_request_id, str) or not external_request_id:
            return self._error(None, "INVALID_REQUEST", "request_id is required")
        if not isinstance(operation, str) or not operation:
            return self._error(external_request_id, "INVALID_REQUEST", "operation is required")

        if operation == "lane.open":
            requested = request.get("capabilities", [])
            if not isinstance(requested, list):
                return self._error(external_request_id, "INVALID_REQUEST", "capabilities must be a list")
            extras = set(str(item) for item in requested) - set(self.binding.granted_capabilities)
            if extras:
                return self._error(
                    external_request_id,
                    SessionCapabilityDenied.code,
                    f"session does not grant capabilities: {sorted(extras)}",
                )

        internal = dict(request)
        if operation == "lane.open":
            remaining_ms = self.binding.expires_at_ms - self.now_ms()
            if remaining_ms <= 0:
                return self._error(
                    external_request_id,
                    SessionExpired.code,
                    "VeraPort session expired",
                )
            requested_ttl_s = float(internal.get("ttl_s", 300.0))
            if requested_ttl_s <= 0:
                return self._error(
                    external_request_id,
                    "INVALID_REQUEST",
                    "ttl_s must be positive",
                )
            internal["ttl_s"] = min(requested_ttl_s, remaining_ms / 1000.0)
        internal["request_id"] = _request_prefix(self.binding) + external_request_id
        if "lane_id" in internal:
            lane_id = internal["lane_id"]
            if not isinstance(lane_id, str) or not lane_id:
                return self._error(external_request_id, "INVALID_REQUEST", "lane_id must be a non-empty string")
            internal["lane_id"] = _lane_prefix(self.binding) + lane_id

        response = await self.agent.handle(internal)
        return self._externalize(response, external_request_id)


    async def close_session(self) -> None:
        close_session = getattr(self.agent, "close_session", None)
        if close_session is None:
            return
        result = close_session(_lane_prefix(self.binding))
        if hasattr(result, "__await__"):
            await result

    def _externalize(self, response: dict[str, Any], external_request_id: str) -> dict[str, Any]:
        result = dict(response)
        result["request_id"] = external_request_id
        payload = result.get("result")
        if not isinstance(payload, dict):
            return result
        payload = dict(payload)
        prefix = _lane_prefix(self.binding)

        lane_id = payload.get("lane_id")
        if isinstance(lane_id, str) and lane_id.startswith(prefix):
            payload["lane_id"] = lane_id[len(prefix):]

        lanes = payload.get("lanes")
        if isinstance(lanes, list):
            visible = []
            for lane in lanes:
                if not isinstance(lane, dict):
                    continue
                internal_lane_id = lane.get("lane_id")
                if not isinstance(internal_lane_id, str) or not internal_lane_id.startswith(prefix):
                    continue
                external_lane = dict(lane)
                external_lane["lane_id"] = internal_lane_id[len(prefix):]
                visible.append(external_lane)
            payload["lanes"] = visible

        result["result"] = payload
        return result

    @staticmethod
    def _error(request_id: Any, code: str, message: str) -> dict[str, Any]:
        return {
            "protocol_version": "veraport-v1",
            "request_id": request_id if isinstance(request_id, str) else None,
            "ok": False,
            "error": {"code": code, "message": message},
        }


class WorkstationHandlerFactory:
    """Bind every authenticated session to one shared workstation agent.

    Sharing the agent keeps resource collision and durable request state global
    to Lappy while lane identifiers remain isolated per application session.
    """

    def __init__(self, agent: Any, *, now_ms: Callable[[], int] | None = None) -> None:
        self.agent = agent
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))

    def __call__(self, binding: SessionBinding) -> SessionBoundHandler:
        return SessionBoundHandler(binding=binding, agent=self.agent, now_ms=self.now_ms)
