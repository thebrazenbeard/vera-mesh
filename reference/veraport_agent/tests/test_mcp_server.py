import pytest

from veraport_agent.mcp_server import VeraPortMCPAdapter


class FakeGateway:
    def __init__(self):
        self.calls = []

    async def list_lanes(self):
        self.calls.append(("lane.list", {}))
        return {"ok": True, "result": {"lanes": []}}

    async def read_text(self, **kwargs):
        self.calls.append(("fs.read_text", kwargs))
        return {"ok": True, "result": {"content": "fixture"}}


class FakeRuntime:
    def __init__(self):
        self.gateway = FakeGateway()
        self.ensure_count = 0

    async def ensure_current(self):
        self.ensure_count += 1

    def machine_info(self):
        return {
            "endpoints": [{"endpoint_id": "direct", "session_id": "same"}],
            "transport_reconnect_recreates_veraport_session": False,
        }


@pytest.mark.asyncio
async def test_adapter_reuses_persistent_runtime_across_calls():
    runtime = FakeRuntime()
    adapter = VeraPortMCPAdapter(runtime)
    first = await adapter.machine_info()
    second = await adapter.lane_list()
    assert first["transport_reconnect_recreates_veraport_session"] is False
    assert second["ok"] is True
    assert runtime.ensure_count == 2
    assert runtime.gateway.calls == [("lane.list", {})]


@pytest.mark.asyncio
async def test_adapter_is_thin_gateway_projection():
    runtime = FakeRuntime()
    adapter = VeraPortMCPAdapter(runtime)
    result = await adapter.fs_read_text(
        "lane", 7, r"C:\Temp\fixture.txt", "utf-8"
    )
    assert result["result"]["content"] == "fixture"
    assert runtime.gateway.calls[0][0] == "fs.read_text"
