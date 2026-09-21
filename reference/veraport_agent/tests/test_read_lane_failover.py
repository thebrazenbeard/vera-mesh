import asyncio
from types import SimpleNamespace

import pytest

from veraport_agent.core import LaneRegistry
from veraport_agent.executor import LocalExecutor
from veraport_agent.hot_session import SessionBinding
from veraport_agent.protocol import VeraPortAgent
from veraport_agent.read_lane import (
    LogicalReadLaneCloseIncomplete,
    LogicalReadLaneStaleFence,
    MirroredReadLaneRouter,
)
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


class ScriptedChannel:
    def __init__(self, *, block_read=False, reject_close=False, read_content="ok"):
        self.calls = []
        self.block_read = block_read
        self.reject_close = reject_close
        self.read_content = read_content
        self.read_started = asyncio.Event()
        self.read_release = asyncio.Event()
        self.next_fence = 100

    async def request(self, request):
        self.calls.append(dict(request))
        operation = request["operation"]
        if operation == "lane.open":
            self.next_fence += 1
            return {
                "request_id": request["request_id"],
                "ok": True,
                "result": {"fencing_token": self.next_fence},
            }
        if operation == "fs.read_text":
            self.read_started.set()
            if self.block_read:
                await self.read_release.wait()
            return {
                "request_id": request["request_id"],
                "ok": True,
                "result": {"content": self.read_content},
            }
        if operation == "lane.close":
            if self.reject_close:
                return {
                    "request_id": request["request_id"],
                    "ok": False,
                    "error": {"code": "CLOSE_REJECTED"},
                }
            return {
                "request_id": request["request_id"],
                "ok": True,
                "result": {"closed": True},
            }
        if operation == "lane.renew":
            return {
                "request_id": request["request_id"],
                "ok": True,
                "result": {"renewed": True},
            }
        raise AssertionError(operation)


def scripted_endpoint(endpoint_id, mode, channel, *, session_id="session"):
    return endpoint(
        endpoint_id,
        mode,
        SessionBinding(
            session_id,
            "controller:c",
            "workstation:w",
            frozenset({"fs.read"}),
            999999,
        ),
        channel,
        2 if mode is PathMode.DIRECT_STREAM else 8,
    )


@pytest.mark.asyncio
async def test_logical_close_waits_for_inflight_read_before_success():
    channel = ScriptedChannel(block_read=True, read_content="before-close")
    endpoints = (
        scripted_endpoint("direct", PathMode.DIRECT_STREAM, channel),
    )
    router = MirroredReadLaneRouter(
        live_endpoints=lambda: endpoints,
        now_ms=lambda: 1000,
        request_id_factory=lambda: "id",
        max_path_age_ms=5000,
        fence_epoch_factory=lambda: 11,
    )
    opened = await router.open(
        lane_id="lane",
        task_id="task",
        capabilities=["fs.read"],
        claims=[{"key": "fs:/tmp", "mode": "read"}],
    )
    fence = opened["result"]["fencing_token"]

    read_task = asyncio.create_task(
        router.read_text(
            lane_id="lane",
            fencing_token=fence,
            path="/tmp/x",
        )
    )
    await channel.read_started.wait()
    close_task = asyncio.create_task(
        router.close(lane_id="lane", fencing_token=fence)
    )
    await asyncio.sleep(0)
    assert close_task.done() is False

    channel.read_release.set()
    read_result = await read_task
    close_result = await close_task

    assert read_result["result"]["content"] == "before-close"
    assert close_result["result"]["closed"] is True
    assert router.owns("lane", fence) is False


@pytest.mark.asyncio
async def test_controller_restart_uses_distinct_logical_fence_epoch():
    first_channel = ScriptedChannel()
    second_channel = ScriptedChannel()
    first_endpoints = (
        scripted_endpoint("direct", PathMode.DIRECT_STREAM, first_channel),
    )
    second_endpoints = (
        scripted_endpoint("direct", PathMode.DIRECT_STREAM, second_channel),
    )
    router_a = MirroredReadLaneRouter(
        live_endpoints=lambda: first_endpoints,
        now_ms=lambda: 1000,
        request_id_factory=lambda: "a",
        max_path_age_ms=5000,
        fence_epoch_factory=lambda: 21,
    )
    router_b = MirroredReadLaneRouter(
        live_endpoints=lambda: second_endpoints,
        now_ms=lambda: 1000,
        request_id_factory=lambda: "b",
        max_path_age_ms=5000,
        fence_epoch_factory=lambda: 22,
    )

    a = await router_a.open(
        lane_id="same",
        task_id="a",
        capabilities=["fs.read"],
        claims=[{"key": "fs:/tmp", "mode": "read"}],
    )
    b = await router_b.open(
        lane_id="same",
        task_id="b",
        capabilities=["fs.read"],
        claims=[{"key": "fs:/tmp", "mode": "read"}],
    )

    stale = a["result"]["fencing_token"]
    current = b["result"]["fencing_token"]
    assert stale != current
    with pytest.raises(LogicalReadLaneStaleFence):
        await router_b.read_text(
            lane_id="same",
            fencing_token=stale,
            path="/tmp/x",
        )


@pytest.mark.asyncio
async def test_logical_close_does_not_claim_success_on_mirror_rejection():
    channel = ScriptedChannel(reject_close=True)
    endpoints = (
        scripted_endpoint("direct", PathMode.DIRECT_STREAM, channel),
    )
    router = MirroredReadLaneRouter(
        live_endpoints=lambda: endpoints,
        now_ms=lambda: 1000,
        request_id_factory=lambda: "id",
        max_path_age_ms=5000,
        fence_epoch_factory=lambda: 31,
    )
    opened = await router.open(
        lane_id="lane",
        task_id="task",
        capabilities=["fs.read"],
        claims=[{"key": "fs:/tmp", "mode": "read"}],
    )
    fence = opened["result"]["fencing_token"]

    with pytest.raises(LogicalReadLaneCloseIncomplete):
        await router.close(lane_id="lane", fencing_token=fence)

    assert router.owns("lane", fence) is True


@pytest.mark.asyncio
async def test_mirrored_read_timeout_falls_through_to_edge():
    direct = ScriptedChannel(block_read=True, read_content="never")
    edge_channel = ScriptedChannel(read_content="edge-timeout-ok")
    endpoints = (
        scripted_endpoint("direct", PathMode.DIRECT_STREAM, direct, session_id="direct-session"),
        scripted_endpoint("edge", PathMode.EDGE_STREAM, edge_channel, session_id="edge-session"),
    )
    router = MirroredReadLaneRouter(
        live_endpoints=lambda: endpoints,
        now_ms=lambda: 1000,
        request_id_factory=lambda: "id",
        max_path_age_ms=5000,
        request_timeout_s=0.01,
        fence_epoch_factory=lambda: 41,
    )
    opened = await router.open(
        lane_id="lane",
        task_id="task",
        capabilities=["fs.read"],
        claims=[{"key": "fs:/tmp", "mode": "read"}],
    )

    result = await router.read_text(
        lane_id="lane",
        fencing_token=opened["result"]["fencing_token"],
        path="/tmp/x",
    )

    assert result["result"]["content"] == "edge-timeout-ok"
    assert any(call["operation"] == "fs.read_text" for call in direct.calls)
    assert any(call["operation"] == "fs.read_text" for call in edge_channel.calls)
