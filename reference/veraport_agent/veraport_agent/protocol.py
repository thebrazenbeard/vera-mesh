from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .core import ClaimMode, ResourceClaim, VeraPortError, LaneRegistry
from .executor import LocalExecutor, PathOutsideRoots
from .state import AgentStateStore, RequestOutcomeUnknown

_MUTATING_OPERATIONS = frozenset({
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
    ) -> None:
        self.registry = registry
        self.executor = executor
        self.state_store = state_store

    async def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("request_id")
        operation = request.get("operation")
        try:
            if request.get("protocol_version") != "veraport-v1":
                raise ValueError("protocol_version must be veraport-v1")
            if not isinstance(request_id, str) or not request_id:
                raise ValueError("request_id is required")
            if not isinstance(operation, str) or not operation:
                raise ValueError("operation is required")
            request_sha256 = self._request_sha256(request)
            durable = (
                self.state_store is not None
                and operation in _MUTATING_OPERATIONS
            )
            if durable:
                assert self.state_store is not None
                begun = self.state_store.begin_request(
                    request_id,
                    request_sha256,
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
                response = {"protocol_version": "veraport-v1", "request_id": request_id, "ok": True, "result": result}
            except Exception as exc:
                code = getattr(exc, "code", exc.__class__.__name__.upper())
                response = {
                    "protocol_version": "veraport-v1",
                    "request_id": request_id,
                    "ok": False,
                    "error": {"code": code, "message": str(exc)},
                }
            if durable:
                assert self.state_store is not None
                self.state_store.complete_request(
                    request_id,
                    request_sha256,
                    response,
                )
            return response
        except Exception as exc:
            code = getattr(exc, "code", exc.__class__.__name__.upper())
            return {
                "protocol_version": "veraport-v1",
                "request_id": request_id,
                "ok": False,
                "error": {"code": code, "message": str(exc)},
            }

    async def _dispatch(self, operation: str, request: dict[str, Any]) -> dict[str, Any]:
        if operation == "lane.open":
            claims = tuple(
                ResourceClaim(str(item["key"]), ClaimMode(str(item["mode"])))
                for item in request.get("claims", [])
            )
            lane = self.registry.open_lane(
                lane_id=str(request["lane_id"]),
                task_id=str(request["task_id"]),
                capabilities=frozenset(str(item) for item in request.get("capabilities", [])),
                claims=claims,
                ttl_s=float(request.get("ttl_s", 300.0)),
            )
            return self._lane_json(lane)

        if operation == "lane.renew":
            lane = self.registry.renew(
                str(request["lane_id"]),
                int(request["fencing_token"]),
                ttl_s=float(request.get("ttl_s", 300.0)),
            )
            return self._lane_json(lane)

        if operation == "lane.close":
            lane = await self.executor.close_lane(
                str(request["lane_id"]),
                int(request["fencing_token"]),
            )
            return {"lane_id": lane.lane_id, "closed": True}

        if operation == "lane.list":
            return {"lanes": [self._lane_json(lane) for lane in self.registry.snapshot()]}

        if operation == "state.health":
            if self.state_store is None:
                return {"status": "NO_DURABLE_STORE"}
            return self.state_store.health()

        if operation == "fs.read_text":
            content = await self.executor.read_text(
                lane_id=str(request["lane_id"]),
                fencing_token=int(request["fencing_token"]),
                path=str(request["path"]),
                encoding=str(request.get("encoding", "utf-8")),
            )
            return {"content": content}

        if operation == "fs.write_text":
            await self.executor.write_text(
                lane_id=str(request["lane_id"]),
                fencing_token=int(request["fencing_token"]),
                path=str(request["path"]),
                content=str(request["content"]),
                encoding=str(request.get("encoding", "utf-8")),
            )
            return {"written": True}

        if operation == "process.exec":
            argv = request.get("argv")
            if not isinstance(argv, list):
                raise ValueError("argv must be a list")
            result = await self.executor.run_process(
                lane_id=str(request["lane_id"]),
                fencing_token=int(request["fencing_token"]),
                argv=[str(item) for item in argv],
                cwd=str(request["cwd"]),
                timeout_s=float(request.get("timeout_s", 60.0)),
            )
            return asdict(result)

        raise ValueError(f"unknown operation: {operation}")

    async def close_session(self, session_id: str) -> dict[str, Any]:
        prefix = session_id + "::"
        unresolved: list[str] = []
        closed: list[str] = []
        for lane in self.registry.snapshot():
            if not lane.lane_id.startswith(prefix):
                continue
            try:
                await self.executor.close_lane(
                    lane.lane_id,
                    lane.fencing_token,
                )
                closed.append(lane.lane_id)
            except Exception:
                unresolved.append(lane.lane_id)
        return {
            "session_id": session_id,
            "closed_lanes": sorted(closed),
            "unresolved_lanes": sorted(unresolved),
            "drained": not unresolved,
        }

    @staticmethod
    def _request_sha256(request: dict[str, Any]) -> str:
        canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _lane_json(lane) -> dict[str, Any]:
        return {
            "lane_id": lane.lane_id,
            "task_id": lane.task_id,
            "capabilities": sorted(lane.capabilities),
            "claims": [{"key": claim.key, "mode": claim.mode.value} for claim in lane.claims],
            "fencing_token": lane.fencing_token,
            "expires_at_monotonic": lane.expires_at,
        }
