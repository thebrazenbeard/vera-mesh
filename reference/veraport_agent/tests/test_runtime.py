import pytest

from veraport_agent.hot_session import SessionBinding
from veraport_agent.runtime import WorkstationHandlerFactory


class FakeGlobalAgent:
    def __init__(self):
        self.requests = []
        self.lanes = {}
        self.next_fence = 0

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

    response = await handler({"request_id": "x", "operation": "lane.list"})
    assert response["error"]["code"] == "SESSION_EXPIRED"
    assert agent.requests == []
