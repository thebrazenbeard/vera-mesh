from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from veraport_agent.core import (
    CapabilityDenied,
    ClaimMode,
    CollisionBlocked,
    LaneExpired,
    LaneRegistry,
    ResourceClaim,
    StaleFence,
)
from veraport_agent.executor import FileReadLimitExceeded, LocalExecutor, PathOutsideRoots, ProcessExecutionDisabled
from veraport_agent.protocol import VeraPortAgent


def claim(namespace: str, path: Path, mode: ClaimMode) -> ResourceClaim:
    return ResourceClaim(f"{namespace}:{path.resolve().as_posix()}", mode)


def test_capabilities_only_narrow() -> None:
    registry = LaneRegistry({"fs.read"})
    with pytest.raises(CapabilityDenied):
        registry.open_lane(
            lane_id="lane-a",
            task_id="task-a",
            capabilities={"fs.read", "fs.write"},
        )


def test_write_collision_blocks_but_read_read_parallelism_is_allowed(tmp_path: Path) -> None:
    resource = claim("fs", tmp_path, ClaimMode.READ)
    registry = LaneRegistry({"fs.read", "fs.write"})
    registry.open_lane(lane_id="r1", task_id="t1", capabilities={"fs.read"}, claims=(resource,))
    registry.open_lane(lane_id="r2", task_id="t2", capabilities={"fs.read"}, claims=(resource,))

    with pytest.raises(CollisionBlocked):
        registry.open_lane(
            lane_id="writer",
            task_id="t3",
            capabilities={"fs.write"},
            claims=(claim("fs", tmp_path, ClaimMode.WRITE),),
        )


def test_stale_fence_cannot_reuse_reopened_lane_id() -> None:
    registry = LaneRegistry({"fs.read"})
    first = registry.open_lane(lane_id="same", task_id="t1", capabilities={"fs.read"})
    registry.close(first.lane_id, first.fencing_token)
    second = registry.open_lane(lane_id="same", task_id="t2", capabilities={"fs.read"})
    assert second.fencing_token > first.fencing_token
    with pytest.raises(StaleFence):
        registry.authorize("same", first.fencing_token, "fs.read")


def test_expired_lane_fails_closed() -> None:
    registry = LaneRegistry({"fs.read"})
    lane = registry.open_lane(lane_id="a", task_id="t", capabilities={"fs.read"}, ttl_s=1, now=10)
    with pytest.raises(LaneExpired):
        registry.authorize("a", lane.fencing_token, "fs.read", now=11)


@pytest.mark.asyncio
async def test_filesystem_is_root_bounded_and_atomic(tmp_path: Path) -> None:
    registry = LaneRegistry({"fs.read", "fs.write"})
    root_claim = claim("fs", tmp_path, ClaimMode.WRITE)
    lane = registry.open_lane(
        lane_id="files",
        task_id="t",
        capabilities={"fs.read", "fs.write"},
        claims=(root_claim,),
    )
    executor = LocalExecutor(registry, allowed_roots=(tmp_path,))
    target = tmp_path / "a" / "hello.txt"
    await executor.write_text(
        lane_id=lane.lane_id,
        fencing_token=lane.fencing_token,
        path=str(target),
        content="hello",
    )
    assert await executor.read_text(
        lane_id=lane.lane_id,
        fencing_token=lane.fencing_token,
        path=str(target),
    ) == "hello"

    with pytest.raises(PathOutsideRoots):
        await executor.read_text(
            lane_id=lane.lane_id,
            fencing_token=lane.fencing_token,
            path=str(tmp_path.parent / "outside.txt"),
        )


@pytest.mark.asyncio
async def test_filesystem_read_is_bounded_at_workstation(tmp_path: Path) -> None:
    registry = LaneRegistry({"fs.read"})
    lane = registry.open_lane(
        lane_id="bounded",
        task_id="t",
        capabilities={"fs.read"},
        claims=(claim("fs", tmp_path, ClaimMode.READ),),
    )
    target = tmp_path / "large.txt"
    target.write_bytes(b"x" * 33)
    executor = LocalExecutor(
        registry,
        allowed_roots=(tmp_path,),
        max_read_bytes=32,
    )
    with pytest.raises(FileReadLimitExceeded):
        await executor.read_text(
            lane_id=lane.lane_id,
            fencing_token=lane.fencing_token,
            path=str(target),
        )


@pytest.mark.asyncio
async def test_process_exec_is_disabled_by_default(tmp_path: Path) -> None:
    registry = LaneRegistry({"process.exec"})
    lane = registry.open_lane(
        lane_id="proc-disabled",
        task_id="t",
        capabilities={"process.exec"},
        claims=(claim("cwd", tmp_path, ClaimMode.WRITE),),
    )
    executor = LocalExecutor(registry, allowed_roots=(tmp_path,))
    with pytest.raises(ProcessExecutionDisabled):
        await executor.run_process(
            lane_id=lane.lane_id,
            fencing_token=lane.fencing_token,
            argv=[sys.executable, "-c", "print('nope')"],
            cwd=str(tmp_path),
        )


@pytest.mark.asyncio
async def test_process_exec_uses_argv_not_shell(tmp_path: Path) -> None:
    registry = LaneRegistry({"process.exec"})
    lane = registry.open_lane(
        lane_id="proc",
        task_id="t",
        capabilities={"process.exec"},
        claims=(claim("cwd", tmp_path, ClaimMode.WRITE),),
    )
    executor = LocalExecutor(registry, allowed_roots=(tmp_path,), allow_process_exec=True)
    result = await executor.run_process(
        lane_id=lane.lane_id,
        fencing_token=lane.fencing_token,
        argv=[sys.executable, "-c", "print('safe;literal')"],
        cwd=str(tmp_path),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "safe;literal"


@pytest.mark.asyncio
async def test_agent_can_dispatch_independent_lanes_concurrently(tmp_path: Path) -> None:
    registry = LaneRegistry({"process.exec"}, max_lanes=4)
    agent = VeraPortAgent(registry, LocalExecutor(registry, allowed_roots=(tmp_path,), allow_process_exec=True))

    open_a = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "1",
        "operation": "lane.open",
        "lane_id": "a",
        "task_id": "ta",
        "capabilities": ["process.exec"],
        "claims": [{"key": f"cwd:{(tmp_path / 'a').as_posix()}", "mode": "write"}],
    })
    open_b = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "2",
        "operation": "lane.open",
        "lane_id": "b",
        "task_id": "tb",
        "capabilities": ["process.exec"],
        "claims": [{"key": f"cwd:{(tmp_path / 'b').as_posix()}", "mode": "write"}],
    })
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    async def execute(request_id: str, lane: str, fence: int, cwd: Path, word: str):
        return await agent.handle({
            "protocol_version": "veraport-v1",
            "request_id": request_id,
            "operation": "process.exec",
            "lane_id": lane,
            "fencing_token": fence,
            "argv": [sys.executable, "-c", f"import time; time.sleep(.05); print('{word}')"],
            "cwd": str(cwd),
            "timeout_s": 2,
        })

    a, b = await asyncio.gather(
        execute("3", "a", open_a["result"]["fencing_token"], tmp_path / "a", "alpha"),
        execute("4", "b", open_b["result"]["fencing_token"], tmp_path / "b", "beta"),
    )
    assert a["ok"] and b["ok"]
    assert a["result"]["stdout"].strip() == "alpha"
    assert b["result"]["stdout"].strip() == "beta"


def test_durable_fence_survives_state_store_reopen(tmp_path: Path) -> None:
    from veraport_agent.state import AgentStateStore

    db = tmp_path / "agent.sqlite3"
    store = AgentStateStore(db)
    first_registry = LaneRegistry({"fs.read"}, fence_allocator=store.next_fence)
    first = first_registry.open_lane(lane_id="a", task_id="t1", capabilities={"fs.read"})
    store.close()

    reopened = AgentStateStore(db)
    second_registry = LaneRegistry({"fs.read"}, fence_allocator=reopened.next_fence)
    second = second_registry.open_lane(lane_id="b", task_id="t2", capabilities={"fs.read"})
    reopened.close()

    assert second.fencing_token > first.fencing_token


@pytest.mark.asyncio
async def test_durable_idempotency_replays_completed_request(tmp_path: Path) -> None:
    from veraport_agent.state import AgentStateStore

    store = AgentStateStore(tmp_path / "agent.sqlite3")
    registry = LaneRegistry({"fs.read"}, fence_allocator=store.next_fence)
    agent = VeraPortAgent(registry, LocalExecutor(registry, allowed_roots=(tmp_path,)), store)
    request = {
        "protocol_version": "veraport-v1",
        "request_id": "idem-1",
        "operation": "lane.open",
        "lane_id": "idem-lane",
        "task_id": "idem-task",
        "capabilities": ["fs.read"],
        "claims": [],
    }

    first = await agent.handle(request)
    second = await agent.handle(request)
    store.close()

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["request_id"] == first["request_id"]
    assert second["result"] == first["result"]
    assert second["replay"] == {
        "durable_evidence": True,
        "current_state_not_implied": True,
    }
    assert len(registry.snapshot()) == 1


@pytest.mark.asyncio
async def test_durable_idempotency_rejects_same_id_different_request(tmp_path: Path) -> None:
    from veraport_agent.state import AgentStateStore

    store = AgentStateStore(tmp_path / "agent.sqlite3")
    registry = LaneRegistry({"fs.read"}, fence_allocator=store.next_fence)
    agent = VeraPortAgent(registry, LocalExecutor(registry, allowed_roots=(tmp_path,)), store)
    first = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "collision-1",
        "operation": "lane.list",
    })
    second = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "collision-1",
        "operation": "lane.open",
        "lane_id": "different",
        "task_id": "different",
        "capabilities": ["fs.read"],
        "claims": [],
    })
    store.close()

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_pending_request_survives_restart_as_outcome_unknown(tmp_path: Path) -> None:
    import hashlib
    import json

    from veraport_agent.state import AgentStateStore, RequestOutcomeUnknown

    db = tmp_path / "agent.sqlite3"
    request = {"protocol_version": "veraport-v1", "request_id": "pending-1", "operation": "lane.list"}
    digest = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()

    store = AgentStateStore(db)
    assert store.begin_request("pending-1", digest).disposition == "NEW"
    store.close()

    reopened = AgentStateStore(db)
    with pytest.raises(RequestOutcomeUnknown):
        reopened.begin_request("pending-1", digest)
    reopened.close()


def test_durable_fence_is_atomic_across_store_connections(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from veraport_agent.state import AgentStateStore

    db = tmp_path / "agent.sqlite3"
    stores = [AgentStateStore(db), AgentStateStore(db)]
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(stores[index % 2].next_fence) for index in range(20)]
            values = sorted(future.result() for future in futures)
    finally:
        for store in stores:
            store.close()

    assert values == list(range(1, 21))


class BlockingReadExecutor:
    def __init__(self, registry):
        self.registry = registry
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def read_text(
        self,
        *,
        lane_id,
        fencing_token,
        path,
        encoding="utf-8",
    ):
        resolved = Path(path).resolve()
        self.registry.authorize(
            lane_id,
            fencing_token,
            "fs.read",
            resource_key="fs:" + resolved.as_posix(),
            resource_mode=ClaimMode.READ,
        )
        self.started.set()
        await self.release.wait()
        return "inflight-complete"


@pytest.mark.asyncio
async def test_lane_close_is_workstation_quiescence_barrier(tmp_path: Path) -> None:
    registry = LaneRegistry({"fs.read"})
    executor = BlockingReadExecutor(registry)
    agent = VeraPortAgent(registry, executor)
    target = tmp_path / "x.txt"
    target.write_text("x", encoding="utf-8")

    opened = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "open-quiesce",
        "operation": "lane.open",
        "lane_id": "quiesce",
        "task_id": "task",
        "capabilities": ["fs.read"],
        "claims": [{
            "key": "fs:" + tmp_path.resolve().as_posix(),
            "mode": "read",
        }],
    })
    fence = opened["result"]["fencing_token"]

    read_task = asyncio.create_task(agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "read-quiesce",
        "operation": "fs.read_text",
        "lane_id": "quiesce",
        "fencing_token": fence,
        "path": str(target),
    }))
    await executor.started.wait()

    close_task = asyncio.create_task(agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "close-quiesce",
        "operation": "lane.close",
        "lane_id": "quiesce",
        "fencing_token": fence,
    }))
    await asyncio.sleep(0)
    assert close_task.done() is False

    executor.release.set()
    read_result = await read_task
    close_result = await close_task

    assert read_result["ok"] is True
    assert read_result["result"]["content"] == "inflight-complete"
    assert close_result["ok"] is True
    assert close_result["result"]["closed"] is True

    after = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "read-after-close",
        "operation": "fs.read_text",
        "lane_id": "quiesce",
        "fencing_token": fence,
        "path": str(target),
    })
    assert after["ok"] is False
    assert after["error"]["code"] == "LANE_NOT_FOUND"
