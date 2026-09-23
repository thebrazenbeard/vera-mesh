from __future__ import annotations

import pytest

from veraport_agent.workbridge_local_qualification import (
    WorkBridgeQualificationError,
    qualify_runtime,
)


class FakeRuntime:
    def __init__(self, *, backend="workbridge"):
        self.backend = backend
        self.closed = False
        self.closed_lane = None

    async def ensure_started(self):
        return None

    async def machine_info(self):
        return {
            "selected_path_id": "lappy-existing-loopback",
            "workstation_principal": "workstation:test",
            "controller_principal": "controller:test",
            "requested_capabilities": ["fs.read"],
        }

    async def open_lane(self, **kwargs):
        self.opened = kwargs
        return {"lane_id": kwargs["lane_id"], "fencing_token": 17}

    async def read_operation(self, operation, **kwargs):
        assert operation == "fs.list_dir"
        self.read = kwargs
        return {
            "path": "C:/Vera",
            "backend": self.backend,
            "entries": [{"name": "one.txt", "path": "C:/Vera/one.txt"}],
            "total_entries": 1,
            "truncated": False,
        }

    async def close_lane(self, **kwargs):
        self.closed_lane = kwargs
        return {"closed": True}

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_qualifier_requires_workbridge_backend_and_closes_lane():
    runtime = FakeRuntime()
    result = await qualify_runtime(runtime, r"C:\Vera")
    assert result["status"] == "PASS"
    assert result["backend"] == "workbridge"
    assert result["resource_key"] == "fs:C:/Vera"
    assert result["entries"][0]["name"] == "one.txt"
    assert runtime.opened["capabilities"] == ["fs.read"]
    assert runtime.opened["claims"] == [{"key": "fs:C:/Vera", "mode": "read"}]
    assert runtime.closed_lane["fencing_token"] == 17
    assert runtime.closed is True


@pytest.mark.asyncio
async def test_qualifier_rejects_non_workbridge_route_but_still_closes():
    runtime = FakeRuntime(backend="local")
    with pytest.raises(WorkBridgeQualificationError, match="did not traverse"):
        await qualify_runtime(runtime, r"C:\Vera")
    assert runtime.closed_lane["fencing_token"] == 17
    assert runtime.closed is True
