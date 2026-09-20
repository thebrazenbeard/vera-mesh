import pytest

from veraport_agent.controller import (
    AmbiguousDelivery,
    HotSessionPool,
    NoCurrentPath,
    SessionEndpoint,
)
from veraport_agent.hot_session import SessionBinding
from veraport_agent.synchrony import PathMode, PathObservation
from veraport_agent.stream import StreamClosed


class Channel:
    def __init__(self, fn):
        self.fn = fn
        self.calls = 0

    async def request(self, request):
        self.calls += 1
        return await self.fn(request)


def binding(
    workstation="workstation:w",
    expires=10000,
    controller="controller:c",
    session="session",
):
    return SessionBinding(
        session,
        controller,
        workstation,
        frozenset({"fs.read", "fs.write"}),
        expires,
    )


def path(path_id, mode, *, healthy=True, observed=1000, rtt=10):
    return PathObservation(path_id, mode, True, healthy, observed, rtt)


@pytest.mark.asyncio
async def test_pool_prefers_direct_hot_session():
    async def direct(request):
        return {"request_id": request["request_id"], "via": "direct"}

    async def edge(request):
        return {"request_id": request["request_id"], "via": "edge"}

    direct_channel = Channel(direct)
    edge_channel = Channel(edge)
    pool = HotSessionPool()
    pool.register(SessionEndpoint(
        "direct-endpoint",
        binding(),
        path("direct", PathMode.DIRECT_STREAM, rtt=50),
        direct_channel,
        True,
    ))
    pool.register(SessionEndpoint(
        "edge-endpoint",
        binding(),
        path("edge", PathMode.EDGE_STREAM, rtt=1),
        edge_channel,
        True,
    ))

    result = await pool.request(
        "workstation:w",
        "controller:c",
        {"request_id": "1", "operation": "fs.read_text"},
        now_ms=1001,
    )
    assert result["via"] == "direct"
    assert direct_channel.calls == 1
    assert edge_channel.calls == 0


@pytest.mark.asyncio
async def test_mutation_can_failover_with_durable_idempotency_same_request():
    executions = 0
    ledger = {}

    async def direct(request):
        nonlocal executions
        if request["request_id"] not in ledger:
            executions += 1
            ledger[request["request_id"]] = {
                "request_id": request["request_id"],
                "ok": True,
                "result": {"written": True},
            }
        raise StreamClosed("lost after endpoint completion")

    async def edge(request):
        nonlocal executions
        if request["request_id"] not in ledger:
            executions += 1
            ledger[request["request_id"]] = {
                "request_id": request["request_id"],
                "ok": True,
                "result": {"written": True},
            }
        return ledger[request["request_id"]]

    direct_channel = Channel(direct)
    edge_channel = Channel(edge)
    pool = HotSessionPool()
    pool.register(SessionEndpoint(
        "direct-endpoint",
        binding(),
        path("direct", PathMode.DIRECT_STREAM),
        direct_channel,
        True,
    ))
    pool.register(SessionEndpoint(
        "edge-endpoint",
        binding(),
        path("edge", PathMode.EDGE_STREAM),
        edge_channel,
        True,
    ))

    result = await pool.request(
        "workstation:w",
        "controller:c",
        {"request_id": "mutation-1", "operation": "fs.write_text"},
        now_ms=1001,
    )
    assert result["ok"] is True
    assert executions == 1
    assert direct_channel.calls == 1
    assert edge_channel.calls == 1


@pytest.mark.asyncio
async def test_mutation_failover_blocked_without_durable_idempotency():
    async def fail(request):
        raise StreamClosed("unknown delivery")

    async def edge(request):
        return {"ok": True}

    direct_channel = Channel(fail)
    edge_channel = Channel(edge)
    pool = HotSessionPool()
    pool.register(SessionEndpoint(
        "direct-endpoint",
        binding(),
        path("direct", PathMode.DIRECT_STREAM),
        direct_channel,
        False,
    ))
    pool.register(SessionEndpoint(
        "edge-endpoint",
        binding(),
        path("edge", PathMode.EDGE_STREAM),
        edge_channel,
        True,
    ))

    with pytest.raises(AmbiguousDelivery):
        await pool.request(
            "workstation:w",
            "controller:c",
            {"request_id": "mutation-2", "operation": "process.exec"},
            now_ms=1001,
        )
    assert edge_channel.calls == 0


@pytest.mark.asyncio
async def test_read_can_failover_without_durable_idempotency():
    async def fail(request):
        raise StreamClosed("lost")

    async def edge(request):
        return {"request_id": request["request_id"], "via": "edge"}

    pool = HotSessionPool()
    pool.register(SessionEndpoint(
        "direct-endpoint",
        binding(),
        path("direct", PathMode.DIRECT_STREAM),
        Channel(fail),
        False,
    ))
    pool.register(SessionEndpoint(
        "edge-endpoint",
        binding(),
        path("edge", PathMode.EDGE_STREAM),
        Channel(edge),
        False,
    ))

    result = await pool.request(
        "workstation:w",
        "controller:c",
        {"request_id": "read-1", "operation": "fs.read_text"},
        now_ms=1001,
    )
    assert result["via"] == "edge"


@pytest.mark.asyncio
async def test_expired_session_not_selected():
    async def ok(request):
        return {"ok": True}

    pool = HotSessionPool()
    pool.register(SessionEndpoint(
        "direct-endpoint",
        binding(expires=1000),
        path("direct", PathMode.DIRECT_STREAM),
        Channel(ok),
        True,
    ))

    with pytest.raises(NoCurrentPath):
        await pool.request(
            "workstation:w",
            "controller:c",
            {"request_id": "expired", "operation": "lane.list"},
            now_ms=1000,
        )


@pytest.mark.asyncio
async def test_pool_never_routes_across_controller_principals_for_same_workstation():
    async def controller_a(request):
        return {"request_id": request["request_id"], "via": "controller-a"}

    async def controller_b(request):
        return {"request_id": request["request_id"], "via": "controller-b"}

    pool = HotSessionPool()
    a_channel = Channel(controller_a)
    b_channel = Channel(controller_b)
    pool.register(SessionEndpoint(
        "a-edge",
        binding(controller="controller:a"),
        path("a-edge", PathMode.EDGE_STREAM, rtt=50),
        a_channel,
        True,
    ))
    pool.register(SessionEndpoint(
        "b-direct",
        binding(controller="controller:b"),
        path("b-direct", PathMode.DIRECT_STREAM, rtt=1),
        b_channel,
        True,
    ))

    result = await pool.request(
        "workstation:w",
        "controller:a",
        {"request_id": "cross-controller", "operation": "lane.open", "capabilities": ["fs.write"]},
        now_ms=1001,
    )

    assert result["via"] == "controller-a"
    assert a_channel.calls == 1
    assert b_channel.calls == 0


@pytest.mark.asyncio
async def test_mutation_failover_does_not_cross_application_session_id():
    async def fail(request):
        raise StreamClosed("unknown delivery")

    async def would_mutate(request):
        return {"request_id": request["request_id"], "ok": True}

    first = Channel(fail)
    second = Channel(would_mutate)
    pool = HotSessionPool()
    pool.register(SessionEndpoint(
        "session-a-direct",
        binding(session="session-a"),
        path("session-a-direct", PathMode.DIRECT_STREAM),
        first,
        True,
    ))
    pool.register(SessionEndpoint(
        "session-b-edge",
        binding(session="session-b"),
        path("session-b-edge", PathMode.EDGE_STREAM),
        second,
        True,
    ))

    with pytest.raises(AmbiguousDelivery, match="same-session"):
        await pool.request(
            "workstation:w",
            "controller:c",
            {"request_id": "mutation-cross-session", "operation": "fs.write_text"},
            now_ms=1001,
        )

    assert first.calls == 1
    assert second.calls == 0
