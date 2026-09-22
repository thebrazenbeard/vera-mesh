from __future__ import annotations

import asyncio
import sys

import pytest

from veraport_agent.core import ClaimMode, LaneRegistry, ResourceClaim
from veraport_agent.executor import LocalExecutor
from veraport_agent.protocol import VeraPortAgent
from veraport_agent.rdc_surface import ProcessHandleOwnerMismatch, surface_for


def read_lane(registry, root, *, lane_id="read"):
    return registry.open_lane(
        lane_id=lane_id,
        task_id="rdc-read",
        capabilities=frozenset({"fs.read"}),
        claims=(ResourceClaim("fs:" + root.as_posix(), ClaimMode.READ),),
    )


def process_lane(registry, root, *, lane_id="proc"):
    return registry.open_lane(
        lane_id=lane_id,
        task_id="rdc-process",
        capabilities=frozenset({
            "process.exec",
            "process.inspect",
            "process.control",
        }),
        claims=(ResourceClaim("cwd:" + root.as_posix(), ClaimMode.WRITE),),
    )


@pytest.mark.asyncio
async def test_list_and_search_are_bounded_and_root_scoped(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "needle.txt").write_text("x", encoding="utf-8")

    registry = LaneRegistry({"fs.read"})
    lane = read_lane(registry, tmp_path)
    surface = surface_for(LocalExecutor(registry, allowed_roots=(tmp_path,)))

    page = await surface.list_dir(
        lane_id=lane.lane_id,
        fencing_token=lane.fencing_token,
        path=str(tmp_path),
        max_entries=2,
    )
    assert len(page["entries"]) == 2
    assert page["truncated"] is True
    assert page["next_offset"] == 2

    found = await surface.search(
        lane_id=lane.lane_id,
        fencing_token=lane.fencing_token,
        root=str(tmp_path),
        query="needle",
        max_results=10,
        max_entries=100,
        max_depth=5,
    )
    assert [item["relative_path"] for item in found["matches"]] == [
        "nested/needle.txt"
    ]

    outside = tmp_path.parent / "outside-veramesh-test"
    outside.mkdir(exist_ok=True)
    with pytest.raises(PermissionError):
        await surface.list_dir(
            lane_id=lane.lane_id,
            fencing_token=lane.fencing_token,
            path=str(outside),
        )


@pytest.mark.asyncio
async def test_managed_process_lifecycle_and_chunked_output(tmp_path):
    registry = LaneRegistry({
        "process.exec",
        "process.inspect",
        "process.control",
    })
    lane = process_lane(registry, tmp_path)
    surface = surface_for(
        LocalExecutor(
            registry,
            allowed_roots=(tmp_path,),
            allow_process_exec=True,
            max_output_bytes=1024,
        )
    )

    started = await surface.process_start(
        lane_id=lane.lane_id,
        fencing_token=lane.fencing_token,
        argv=[
            sys.executable,
            "-c",
            "import time; print('alpha'); print('beta'); time.sleep(0.1)",
        ],
        cwd=str(tmp_path),
        max_runtime_s=5,
    )
    handle = started["process_handle"]

    for _ in range(100):
        status = await surface.process_status(
            lane_id=lane.lane_id,
            fencing_token=lane.fencing_token,
            process_handle=handle,
        )
        if not status["running"]:
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.05)

    first = await surface.process_output(
        lane_id=lane.lane_id,
        fencing_token=lane.fencing_token,
        process_handle=handle,
        max_bytes=6,
    )
    second = await surface.process_output(
        lane_id=lane.lane_id,
        fencing_token=lane.fencing_token,
        process_handle=handle,
        stdout_offset=first["stdout_next_offset"],
        max_bytes=64,
    )
    assert first["stdout"] + second["stdout"] == "alpha\nbeta\n"

    listed = await surface.process_list(
        lane_id=lane.lane_id,
        fencing_token=lane.fencing_token,
    )
    assert [item["process_handle"] for item in listed["processes"]] == [handle]


@pytest.mark.asyncio
async def test_process_handle_is_bound_to_lane_and_fence(tmp_path):
    registry = LaneRegistry({
        "process.exec",
        "process.inspect",
        "process.control",
    })
    lane_a = process_lane(registry, tmp_path, lane_id="a")
    other = tmp_path.parent / (tmp_path.name + "-other")
    other.mkdir()
    lane_b = process_lane(registry, other, lane_id="b")
    surface = surface_for(
        LocalExecutor(
            registry,
            allowed_roots=(tmp_path,),
            allow_process_exec=True,
        )
    )

    started = await surface.process_start(
        lane_id=lane_a.lane_id,
        fencing_token=lane_a.fencing_token,
        argv=[sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=str(tmp_path),
        max_runtime_s=30,
    )

    with pytest.raises(ProcessHandleOwnerMismatch):
        await surface.process_status(
            lane_id=lane_b.lane_id,
            fencing_token=lane_b.fencing_token,
            process_handle=started["process_handle"],
        )

    await surface.process_terminate(
        lane_id=lane_a.lane_id,
        fencing_token=lane_a.fencing_token,
        process_handle=started["process_handle"],
        grace_s=0.1,
    )


@pytest.mark.asyncio
async def test_lane_close_reaps_owned_process(tmp_path):
    registry = LaneRegistry({
        "process.exec",
        "process.inspect",
        "process.control",
    })
    lane = process_lane(registry, tmp_path)
    executor = LocalExecutor(
        registry,
        allowed_roots=(tmp_path,),
        allow_process_exec=True,
    )
    agent = VeraPortAgent(registry, executor)

    started = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "start",
        "operation": "process.start",
        "lane_id": lane.lane_id,
        "fencing_token": lane.fencing_token,
        "argv": [sys.executable, "-c", "import time; time.sleep(30)"],
        "cwd": str(tmp_path),
        "max_runtime_s": 30,
    })
    handle = started["result"]["process_handle"]

    closed = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "close",
        "operation": "lane.close",
        "lane_id": lane.lane_id,
        "fencing_token": lane.fencing_token,
    })
    assert closed["ok"] is True
    assert handle in closed["result"]["terminated_process_handles"]


@pytest.mark.asyncio
async def test_interactive_process_input_is_lane_owned_and_bounded(tmp_path):
    registry = LaneRegistry({
        "process.exec",
        "process.inspect",
        "process.interact",
        "process.control",
    })
    lane = registry.open_lane(
        lane_id="interactive",
        task_id="repl",
        capabilities=frozenset({
            "process.exec",
            "process.inspect",
            "process.interact",
            "process.control",
        }),
        claims=(ResourceClaim("cwd:" + tmp_path.as_posix(), ClaimMode.WRITE),),
    )
    surface = surface_for(
        LocalExecutor(
            registry,
            allowed_roots=(tmp_path,),
            allow_process_exec=True,
        )
    )
    started = await surface.process_start(
        lane_id=lane.lane_id,
        fencing_token=lane.fencing_token,
        argv=[
            sys.executable,
            "-u",
            "-c",
            "import sys; print('ready'); line=sys.stdin.readline(); print('got:'+line.strip())",
        ],
        cwd=str(tmp_path),
        max_runtime_s=5,
    )
    handle = started["process_handle"]

    for _ in range(100):
        out = await surface.process_output(
            lane_id=lane.lane_id,
            fencing_token=lane.fencing_token,
            process_handle=handle,
        )
        if "ready\n" in out["stdout"]:
            break
        await asyncio.sleep(0.01)
    assert "ready\n" in out["stdout"]

    wrote = await surface.process_input(
        lane_id=lane.lane_id,
        fencing_token=lane.fencing_token,
        process_handle=handle,
        input_text="hello",
    )
    assert wrote["bytes_written"] == 6

    for _ in range(100):
        final = await surface.process_output(
            lane_id=lane.lane_id,
            fencing_token=lane.fencing_token,
            process_handle=handle,
        )
        if "got:hello\n" in final["stdout"]:
            break
        await asyncio.sleep(0.01)
    assert "got:hello\n" in final["stdout"]

    with pytest.raises(ValueError, match="65536"):
        await surface.process_input(
            lane_id=lane.lane_id,
            fencing_token=lane.fencing_token,
            process_handle=handle,
            input_text="x" * 65537,
            append_newline=False,
        )
