from types import SimpleNamespace

import pytest

from veraport_agent.core import LaneRegistry
from veraport_agent.executor import LocalExecutor
from veraport_agent.hot_session import SessionBinding
from veraport_agent.protocol import VeraPortAgent
from veraport_agent.read_lane import MirroredReadLaneRouter
from veraport_agent.runtime import WorkstationHandlerFactory
from veraport_agent.stream import StreamClosed
from veraport_agent.synchrony import PathMode, PathObservation


class DirectBreaksOnRead:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    async def request(self, request):
        self.calls.append(dict(request))
        if request["operation"] == "fs.read_text":
            raise StreamClosed("direct transport lost before read response")
        return await self.handler(request)


class Channel:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    async def request(self, request):
        self.calls.append(dict(request))
        return await self.handler(request)


def endpoint(endpoint_id, mode, binding, channel, rtt):
    return SimpleNamespace(
        config=SimpleNamespace(endpoint_id=endpoint_id, mode=mode),
        binding=binding,
        channel=channel,
        path=PathObservation(
            path_id=endpoint_id,
            mode=mode,
            authenticated=True,
            healthy=True,
            observed_at_ms=1000,
            rtt_ms=rtt,
        ),
    )


@pytest.mark.asyncio
async def test_read_failover_materializes_session_local_lane_and_fence(tmp_path):
    target = tmp_path / "hello.txt"
    target.write_text("edge-read-ok", encoding="utf-8")
    registry = LaneRegistry({"fs.read"})
    agent = VeraPortAgent(registry, LocalExecutor(registry, allowed_roots=(tmp_path,)))
    factory = WorkstationHandlerFactory(agent, now_ms=lambda: 1001)
    direct_binding = SessionBinding(
        "session-direct", "controller:c", "workstation:w",
        frozenset({"fs.read"}), 999999,
    )
    edge_binding = SessionBinding(
        "session-edge", "controller:c", "workstation:w",
        frozenset({"fs.read"}), 999999,
    )
    direct = DirectBreaksOnRead(factory(direct_binding))
    edge = Channel(factory(edge_binding))
    endpoints = (
        endpoint("direct", PathMode.DIRECT_STREAM, direct_binding, direct, 2),
        endpoint("edge", PathMode.EDGE_STREAM, edge_binding, edge, 8),
    )
    ids = iter(str(i) for i in range(20))
    router = MirroredReadLaneRouter(
        live_endpoints=lambda: endpoints,
        now_ms=lambda: 1001,
        request_id_factory=lambda: next(ids),
        max_path_age_ms=5000,
    )
    opened = await router.open(
        lane_id="read-lane",
        task_id="read-task",
        capabilities=["fs.read"],
        claims=[{"key": f"fs:{tmp_path.as_posix()}", "mode": "read"}],
        ttl_s=300,
    )
    result = await router.read_text(
        lane_id="read-lane",
        fencing_token=opened["result"]["fencing_token"],
        path=str(target),
    )
    assert result["ok"] is True
    assert result["result"]["content"] == "edge-read-ok"
    lanes = {lane.lane_id: lane for lane in registry.snapshot()}
    assert "session-direct::read-lane" in lanes
    assert "session-edge::read-lane" in lanes
    assert (
        lanes["session-direct::read-lane"].fencing_token
        != lanes["session-edge::read-lane"].fencing_token
    )
    assert any(call["operation"] == "fs.read_text" for call in direct.calls)
    assert any(call["operation"] == "fs.read_text" for call in edge.calls)
