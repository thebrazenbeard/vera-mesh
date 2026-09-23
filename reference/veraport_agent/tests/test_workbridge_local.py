import json
from pathlib import Path

import pytest

from veraport_agent.core import ClaimMode, LaneRegistry, ResourceClaim
from veraport_agent.executor import LocalExecutor
from veraport_agent.protocol import VeraPortAgent
from veraport_agent.workbridge_local import (
    WorkBridgeLocalBackend,
    WorkBridgeLocalConfig,
    WorkBridgeLocalError,
    WorkBridgePathDenied,
)


def make_config(tmp_path):
    token = tmp_path / "token.txt"
    token.write_text("x" * 48, encoding="utf-8")
    return WorkBridgeLocalConfig(
        endpoint="http://127.0.0.1:8765/mcp",
        bearer_token_file=token,
        read_roots=(r"C:\Vera",),
        timeout_seconds=5.0,
    )


def open_read_lane(registry, lane_id="lane-1"):
    return registry.open_lane(
        lane_id=lane_id,
        task_id="read-c-vera",
        capabilities=frozenset({"fs.read"}),
        claims=(ResourceClaim("fs:C:/Vera", ClaimMode.READ),),
        ttl_s=300,
    )


def test_workbridge_config_rejects_non_loopback_and_path_escape(tmp_path):
    token = tmp_path / "token.txt"
    token.write_text("x" * 48, encoding="utf-8")
    with pytest.raises(WorkBridgeLocalError, match="loopback"):
        WorkBridgeLocalConfig(
            endpoint="http://192.0.2.10:8765/mcp",
            bearer_token_file=token,
            read_roots=(r"C:\Vera",),
        ).validate()
    backend = WorkBridgeLocalBackend(make_config(tmp_path), object(), caller=None)
    assert backend.can_handle(r"c:\VERA")
    assert backend.can_handle(r"C:\Vera\child.txt")
    assert not backend.can_handle(r"C:\Vera2")
    assert not backend.can_handle(r"C:\Windows")


@pytest.mark.asyncio
async def test_list_directory_authorizes_before_loopback_call(tmp_path):
    events = []
    class Registry:
        def authorize(self, lane_id, fencing_token, capability, **kwargs):
            events.append(("authorize", lane_id, fencing_token, capability, kwargs))
    async def caller(tool, arguments):
        events.append(("call", tool, arguments))
        return {"entries": [{
            "name": "alpha.txt",
            "path": r"C:\Vera\alpha.txt",
            "type": "file",
            "is_symlink": False,
            "size_bytes": 7,
            "mtime_ns": 9,
        }]}
    backend = WorkBridgeLocalBackend(make_config(tmp_path), Registry(), caller=caller)
    result = await backend.list_dir(
        lane_id="lane-1",
        fencing_token=7,
        path=r"C:\Vera",
    )
    assert [event[0] for event in events] == ["authorize", "call"]
    auth = events[0]
    assert auth[3] == "fs.read"
    assert auth[4]["resource_key"] == "fs:C:/Vera"
    assert auth[4]["resource_mode"] is ClaimMode.READ
    assert events[1][1] == "workspace_list"
    assert result["backend"] == "workbridge"
    assert result["entries"][0]["path"] == "C:/Vera/alpha.txt"


@pytest.mark.asyncio
async def test_lane_claim_still_controls_workbridge_delegate(tmp_path):
    registry = LaneRegistry({"fs.read"})
    lane = registry.open_lane(
        lane_id="lane-1",
        task_id="narrow",
        capabilities=frozenset({"fs.read"}),
        claims=(ResourceClaim("fs:C:/Vera/allowed", ClaimMode.READ),),
        ttl_s=300,
    )
    async def caller(tool, arguments):
        raise AssertionError("network call must not occur when lane claim denies")
    backend = WorkBridgeLocalBackend(make_config(tmp_path), registry, caller=caller)
    with pytest.raises(Exception, match="lacks read claim"):
        await backend.list_dir(
            lane_id=lane.lane_id,
            fencing_token=lane.fencing_token,
            path=r"C:\Vera",
        )


@pytest.mark.asyncio
async def test_veraport_routes_only_workbridge_read_surface(tmp_path):
    local_root = tmp_path / "local"
    local_root.mkdir()
    registry = LaneRegistry({"fs.read", "fs.write"})
    executor = LocalExecutor(registry, allowed_roots=(local_root,), allow_process_exec=False)

    calls = []
    class FakeWB:
        def can_handle(self, path):
            return path.casefold().startswith(r"c:\vera")
        async def list_dir(self, **kwargs):
            calls.append(("list", kwargs))
            return {
                "path": "C:/Vera",
                "entries": [{"name": "live.txt", "path": "C:/Vera/live.txt", "type": "file",
                             "is_symlink": False, "size_bytes": 4, "mtime_ns": 1}],
                "offset": 0, "next_offset": None, "total_entries": 1,
                "truncated": False, "backend": "workbridge",
            }
        async def stat(self, **kwargs):
            calls.append(("stat", kwargs))
            return {"name": "Vera", "path": "C:/Vera", "type": "directory",
                    "is_symlink": False, "size_bytes": 0, "mtime_ns": 1,
                    "backend": "workbridge"}
        async def read_text(self, **kwargs):
            calls.append(("read", kwargs))
            return "live"

    agent = VeraPortAgent(registry, executor, workbridge=FakeWB())
    opened = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "open",
        "operation": "lane.open",
        "lane_id": "lane-1",
        "task_id": "acceptance",
        "capabilities": ["fs.read"],
        "claims": [{"key": "fs:C:/Vera", "mode": "read"}],
        "ttl_s": 300,
    })
    assert opened["ok"] is True
    fence = opened["result"]["fencing_token"]
    listed = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "list",
        "operation": "fs.list_dir",
        "lane_id": "lane-1",
        "fencing_token": fence,
        "path": r"C:\Vera",
        "offset": 0,
        "max_entries": 200,
    })
    assert listed["ok"] is True
    assert listed["result"]["backend"] == "workbridge"
    assert listed["result"]["entries"][0]["name"] == "live.txt"
    assert calls[0][0] == "list"

    denied_write = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "write",
        "operation": "fs.write_text",
        "lane_id": "lane-1",
        "fencing_token": fence,
        "path": r"C:\Vera\nope.txt",
        "content": "no",
    })
    assert denied_write["ok"] is False
    assert len(calls) == 1


def test_config_loader_is_strict_and_runtime_token_is_required(tmp_path):
    config_path = tmp_path / "workbridge-local.json"
    config_path.write_text(json.dumps({
        "schema": "VERAPORT_WORKBRIDGE_LOCAL_V1",
        "endpoint": "http://127.0.0.1:8765/mcp",
        "bearer_token_file": str(tmp_path / "missing-token.txt"),
        "read_roots": [r"C:\Vera"],
        "timeout_seconds": 5,
    }), encoding="utf-8")
    cfg = WorkBridgeLocalConfig.load(config_path)
    with pytest.raises(WorkBridgeLocalError, match="bearer token file missing"):
        cfg.validate_runtime_files()

    bad = json.loads(config_path.read_text(encoding="utf-8"))
    bad["unexpected"] = True
    config_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(WorkBridgeLocalError, match="unknown"):
        WorkBridgeLocalConfig.load(config_path)
