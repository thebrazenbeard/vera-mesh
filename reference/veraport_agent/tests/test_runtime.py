import pytest

from veraport_agent.hot_session import SessionBinding
from veraport_agent.runtime import WorkstationHandlerFactory


class FakeGlobalAgent:
    def __init__(self):
        self.requests = []
        self.lanes = {}
        self.next_fence = 0
        self.closed_sessions = []

    async def handle(self, request):
        self.requests.append(dict(request))
        operation = request["operation"]
        request_id = request["request_id"]

        if operation == "lane.open":
            claim_keys = tuple(sorted(item["key"] for item in request.get("claims", [])))
            for lane in self.lanes.values():
                if set(claim_keys) & set(lane["claims"]):
                    return {
                        "request_id": request_id,
                        "ok": False,
                        "error": {"code": "COLLISION_BLOCKED", "message": "collision"},
                    }
            self.next_fence += 1
            self.lanes[request["lane_id"]] = {
                "lane_id": request["lane_id"],
                "claims": claim_keys,
                "fencing_token": self.next_fence,
            }
            return {
                "request_id": request_id,
                "ok": True,
                "result": {"lane_id": request["lane_id"], "fencing_token": self.next_fence},
            }

        if operation == "lane.list":
            return {"request_id": request_id, "ok": True, "result": {"lanes": list(self.lanes.values())}}

        if operation == "lane.close":
            lane = self.lanes.pop(request["lane_id"], None)
            if lane is None:
                return {
                    "request_id": request_id,
                    "ok": False,
                    "error": {"code": "LANE_NOT_FOUND", "message": "not found"},
                }
            return {
                "request_id": request_id,
                "ok": True,
                "result": {"lane_id": request["lane_id"], "closed": True},
            }

        return {"request_id": request_id, "ok": True, "result": {}}

    async def close_session(self, session_id):
        self.closed_sessions.append(session_id)
        prefix = session_id + "::"
        closed = [
            lane_id
            for lane_id in tuple(self.lanes)
            if lane_id.startswith(prefix)
        ]
        for lane_id in closed:
            self.lanes.pop(lane_id)
        return {
            "session_id": session_id,
            "closed_lanes": sorted(closed),
            "unresolved_lanes": [],
            "drained": True,
        }


def binding(session, controller="controller:a", caps=frozenset({"fs.read", "fs.write"}), expires=5000):
    return SessionBinding(session, controller, "workstation:w", caps, expires)


@pytest.mark.asyncio
async def test_sessions_share_global_collision_domain_but_lane_names_are_isolated():
    agent = FakeGlobalAgent()
    factory = WorkstationHandlerFactory(agent, now_ms=lambda: 1000)
    first_handler = factory(binding("session-a"))
    second_handler = factory(binding("session-b", controller="controller:b"))

    first = await first_handler({
        "request_id": "1",
        "operation": "lane.open",
        "lane_id": "same",
        "capabilities": ["fs.write"],
        "claims": [{"key": "fs:/repo", "mode": "write"}],
    })
    second = await second_handler({
        "request_id": "1",
        "operation": "lane.open",
        "lane_id": "same",
        "capabilities": ["fs.write"],
        "claims": [{"key": "fs:/repo", "mode": "write"}],
    })

    assert first["ok"] is True
    assert second["error"]["code"] == "COLLISION_BLOCKED"
    assert agent.requests[0]["lane_id"] == "session-a::same"
    assert agent.requests[1]["lane_id"] == "session-b::same"


@pytest.mark.asyncio
async def test_same_controller_request_id_is_stable_across_session_reconnect():
    agent = FakeGlobalAgent()
    factory = WorkstationHandlerFactory(agent, now_ms=lambda: 1000)
    old = factory(binding("old-session"))
    new = factory(binding("new-session"))

    await old({"request_id": "operation-7", "operation": "lane.list"})
    await new({"request_id": "operation-7", "operation": "lane.list"})

    assert agent.requests[0]["request_id"] == agent.requests[1]["request_id"] == "controller:a::operation-7"


@pytest.mark.asyncio
async def test_different_controllers_do_not_share_idempotency_namespace():
    agent = FakeGlobalAgent()
    factory = WorkstationHandlerFactory(agent, now_ms=lambda: 1000)
    first = factory(binding("session-a", "controller:a"))
    second = factory(binding("session-b", "controller:b"))

    await first({"request_id": "same", "operation": "lane.list"})
    await second({"request_id": "same", "operation": "lane.list"})

    assert agent.requests[0]["request_id"] != agent.requests[1]["request_id"]


@pytest.mark.asyncio
async def test_session_capability_ceiling_blocks_lane_open_before_agent():
    agent = FakeGlobalAgent()
    handler = WorkstationHandlerFactory(agent, now_ms=lambda: 1000)(
        binding("session", caps=frozenset({"fs.read"}))
    )

    response = await handler({
        "request_id": "x",
        "operation": "lane.open",
        "lane_id": "lane",
        "capabilities": ["process.exec"],
        "claims": [],
    })

    assert response["error"]["code"] == "SESSION_CAPABILITY_DENIED"
    assert agent.requests == []


@pytest.mark.asyncio
async def test_lane_list_filters_other_sessions_and_externalizes_lane_ids():
    agent = FakeGlobalAgent()
    factory = WorkstationHandlerFactory(agent, now_ms=lambda: 1000)
    first = factory(binding("session-a", "controller:a"))
    second = factory(binding("session-b", "controller:b"))

    await first({
        "request_id": "a1",
        "operation": "lane.open",
        "lane_id": "lane-a",
        "capabilities": ["fs.read"],
        "claims": [{"key": "fs:/a", "mode": "read"}],
    })
    await second({
        "request_id": "b1",
        "operation": "lane.open",
        "lane_id": "lane-b",
        "capabilities": ["fs.read"],
        "claims": [{"key": "fs:/b", "mode": "read"}],
    })

    listed = await first({"request_id": "a2", "operation": "lane.list"})
    assert [item["lane_id"] for item in listed["result"]["lanes"]] == ["lane-a"]


@pytest.mark.asyncio
async def test_session_cannot_close_other_sessions_lane_by_guessing_external_id():
    agent = FakeGlobalAgent()
    factory = WorkstationHandlerFactory(agent, now_ms=lambda: 1000)
    first = factory(binding("session-a", "controller:a"))
    second = factory(binding("session-b", "controller:b"))

    await first({
        "request_id": "a1",
        "operation": "lane.open",
        "lane_id": "shared",
        "capabilities": ["fs.read"],
        "claims": [],
    })
    response = await second({
        "request_id": "b1",
        "operation": "lane.close",
        "lane_id": "shared",
        "fencing_token": 1,
    })

    assert response["error"]["code"] == "LANE_NOT_FOUND"
    assert "session-a::shared" in agent.lanes


@pytest.mark.asyncio
async def test_expired_session_rejects_before_agent():
    agent = FakeGlobalAgent()
    handler = WorkstationHandlerFactory(agent, now_ms=lambda: 1000)(
        binding("session", expires=1000)
    )

    agent.lanes["session::owned"] = {
        "lane_id": "session::owned",
        "claims": (),
        "fencing_token": 1,
    }
    agent.lanes["other::preserved"] = {
        "lane_id": "other::preserved",
        "claims": (),
        "fencing_token": 2,
    }

    response = await handler({"request_id": "x", "operation": "lane.list"})
    assert response["error"]["code"] == "SESSION_EXPIRED"
    assert agent.requests == []
    assert agent.closed_sessions == ["session"]
    assert "session::owned" not in agent.lanes
    assert "other::preserved" in agent.lanes


@pytest.mark.asyncio
async def test_lane_open_ttl_is_clamped_to_remaining_session_lifetime():
    agent = FakeGlobalAgent()
    handler = WorkstationHandlerFactory(agent, now_ms=lambda: 1000)(
        binding("session", expires=2500)
    )

    response = await handler({
        "request_id": "open",
        "operation": "lane.open",
        "lane_id": "lane",
        "capabilities": ["fs.read"],
        "claims": [],
        "ttl_s": 300.0,
    })

    assert response["ok"] is True
    assert agent.requests[-1]["ttl_s"] == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_lane_renew_ttl_is_clamped_to_remaining_session_lifetime():
    agent = FakeGlobalAgent()
    handler = WorkstationHandlerFactory(agent, now_ms=lambda: 4000)(
        binding("session", expires=5000)
    )

    response = await handler({
        "request_id": "renew",
        "operation": "lane.renew",
        "lane_id": "lane",
        "fencing_token": 1,
        "ttl_s": 60.0,
    })

    assert response["ok"] is True
    assert agent.requests[-1]["ttl_s"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_shorter_lane_ttl_is_preserved_inside_session_lifetime():
    agent = FakeGlobalAgent()
    handler = WorkstationHandlerFactory(agent, now_ms=lambda: 1000)(
        binding("session", expires=5000)
    )

    await handler({
        "request_id": "open-short",
        "operation": "lane.open",
        "lane_id": "lane",
        "capabilities": ["fs.read"],
        "claims": [],
        "ttl_s": 0.5,
    })

    assert agent.requests[-1]["ttl_s"] == pytest.approx(0.5)


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl_s", [0, -1, "300", None, True])
async def test_invalid_lane_ttl_is_rejected_before_agent(ttl_s):
    agent = FakeGlobalAgent()
    handler = WorkstationHandlerFactory(agent, now_ms=lambda: 1000)(
        binding("session", expires=5000)
    )

    response = await handler({
        "request_id": "bad-ttl",
        "operation": "lane.open",
        "lane_id": "lane",
        "capabilities": ["fs.read"],
        "claims": [],
        "ttl_s": ttl_s,
    })

    assert response["error"]["code"] == "INVALID_REQUEST"
    assert agent.requests == []
