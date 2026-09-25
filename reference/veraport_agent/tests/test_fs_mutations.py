from __future__ import annotations

from pathlib import Path

import pytest

from veraport_agent.core import ClaimMode, LaneRegistry, ResourceClaim
from veraport_agent.executor import LocalExecutor, ReplaceCountMismatch
from veraport_agent.protocol import VeraPortAgent
from veraport_agent.state import AgentStateStore


def rw_lane(registry: LaneRegistry, root: Path):
    return registry.open_lane(
        lane_id="files",
        task_id="native-fs-mutations",
        capabilities=frozenset({"fs.read", "fs.write"}),
        claims=(ResourceClaim("fs:" + root.as_posix(), ClaimMode.WRITE),),
    )


@pytest.mark.asyncio
async def test_mkdir_append_replace_and_move_without_process_authority(tmp_path):
    registry = LaneRegistry({"fs.read", "fs.write"})
    lane = rw_lane(registry, tmp_path)
    executor = LocalExecutor(registry, allowed_roots=(tmp_path,))

    created = tmp_path / "nested" / "work"
    await executor.make_directory(
        lane_id=lane.lane_id,
        fencing_token=lane.fencing_token,
        path=str(created),
    )
    assert created.is_dir()

    target = created / "note.txt"
    await executor.write_text(
        lane_id=lane.lane_id,
        fencing_token=lane.fencing_token,
        path=str(target),
        content="alpha",
    )
    await executor.append_text(
        lane_id=lane.lane_id,
        fencing_token=lane.fencing_token,
        path=str(target),
        content=" beta",
    )
    assert target.read_text(encoding="utf-8") == "alpha beta"

    count = await executor.replace_text(
        lane_id=lane.lane_id,
        fencing_token=lane.fencing_token,
        path=str(target),
        old_string="beta",
        new_string="gamma",
        expected_count=1,
    )
    assert count == 1
    assert target.read_text(encoding="utf-8") == "alpha gamma"

    destination = created / "renamed.txt"
    await executor.move_path(
        lane_id=lane.lane_id,
        fencing_token=lane.fencing_token,
        source=str(target),
        destination=str(destination),
    )
    assert not target.exists()
    assert destination.read_text(encoding="utf-8") == "alpha gamma"


@pytest.mark.asyncio
async def test_replace_count_mismatch_is_no_effect(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("same same", encoding="utf-8")
    registry = LaneRegistry({"fs.read", "fs.write"})
    lane = rw_lane(registry, tmp_path)
    executor = LocalExecutor(registry, allowed_roots=(tmp_path,))

    with pytest.raises(ReplaceCountMismatch):
        await executor.replace_text(
            lane_id=lane.lane_id,
            fencing_token=lane.fencing_token,
            path=str(target),
            old_string="same",
            new_string="changed",
            expected_count=1,
        )
    assert target.read_text(encoding="utf-8") == "same same"


@pytest.mark.asyncio
async def test_move_rejects_destination_outside_allowed_roots(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    source = root / "a.txt"
    source.write_text("a", encoding="utf-8")
    registry = LaneRegistry({"fs.read", "fs.write"})
    lane = rw_lane(registry, root)
    executor = LocalExecutor(registry, allowed_roots=(root,))

    with pytest.raises(PermissionError):
        await executor.move_path(
            lane_id=lane.lane_id,
            fencing_token=lane.fencing_token,
            source=str(source),
            destination=str(tmp_path / "outside.txt"),
        )
    assert source.exists()


@pytest.mark.asyncio
async def test_durable_retry_does_not_duplicate_append(tmp_path):
    target = tmp_path / "append.txt"
    target.write_text("", encoding="utf-8")
    store = AgentStateStore(tmp_path / "state.sqlite3")
    registry = LaneRegistry(
        {"fs.read", "fs.write"},
        fence_allocator=store.next_fence,
    )
    lane = rw_lane(registry, tmp_path)
    agent = VeraPortAgent(
        registry,
        LocalExecutor(registry, allowed_roots=(tmp_path,)),
        store,
    )
    request = {
        "protocol_version": "veraport-v1",
        "request_id": "append-stable-id",
        "operation": "fs.append_text",
        "lane_id": lane.lane_id,
        "fencing_token": lane.fencing_token,
        "path": str(target),
        "content": "once",
    }

    first = await agent.handle(request)
    second = await agent.handle(request)
    store.close()

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["replay"]["durable_evidence"] is True
    assert target.read_text(encoding="utf-8") == "once"
