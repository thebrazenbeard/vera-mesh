import asyncio
import sys
import time

import pytest

from veraport_agent.core import ClaimMode, LaneRegistry, ResourceClaim
from veraport_agent.executor import LocalExecutor, ProcessExecutionDisabled


def open_process_lane(registry, root):
    return registry.open_lane(
        lane_id="p",
        task_id="process-test",
        capabilities=frozenset({"process.exec", "process.inspect", "process.control"}),
        claims=(ResourceClaim("cwd:" + root.as_posix(), ClaimMode.WRITE),),
    )


@pytest.mark.asyncio
async def test_managed_process_start_status_output_and_list(tmp_path):
    registry = LaneRegistry({"process.exec", "process.inspect", "process.control"})
    lane = open_process_lane(registry, tmp_path)
    executor = LocalExecutor(registry, allowed_roots=(tmp_path,), allow_process_exec=True)
    started = await executor.start_process(
        lane_id="p", fencing_token=lane.fencing_token,
        argv=[sys.executable, "-c", "print('managed-ok')"], cwd=str(tmp_path),
    )
    handle = started["process_handle"]
    for _ in range(50):
        status = await executor.process_status(
            lane_id="p", fencing_token=lane.fencing_token, process_handle=handle
        )
        if not status["running"]:
            break
        await asyncio.sleep(0.02)
    output = await executor.process_output(
        lane_id="p", fencing_token=lane.fencing_token, process_handle=handle
    )
    assert "managed-ok" in output["stdout"]
    listed = await executor.list_processes(
        lane_id="p", fencing_token=lane.fencing_token
    )
    assert any(x["process_handle"] == handle for x in listed)


@pytest.mark.asyncio
async def test_process_termination_uses_opaque_handle_not_pid(tmp_path):
    registry = LaneRegistry({"process.exec", "process.inspect", "process.control"})
    lane = open_process_lane(registry, tmp_path)
    executor = LocalExecutor(registry, allowed_roots=(tmp_path,), allow_process_exec=True)
    started = await executor.start_process(
        lane_id="p", fencing_token=lane.fencing_token,
        argv=[sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(tmp_path),
    )
    result = await executor.terminate_process(
        lane_id="p", fencing_token=lane.fencing_token,
        process_handle=started["process_handle"], grace_s=0.2,
    )
    assert result["running"] is False


@pytest.mark.asyncio
async def test_process_surface_fails_closed_when_local_policy_disabled(tmp_path):
    registry = LaneRegistry({"process.exec", "process.inspect", "process.control"})
    lane = open_process_lane(registry, tmp_path)
    executor = LocalExecutor(registry, allowed_roots=(tmp_path,), allow_process_exec=False)
    with pytest.raises(ProcessExecutionDisabled):
        await executor.start_process(
            lane_id="p", fencing_token=lane.fencing_token,
            argv=[sys.executable, "-c", "print('no')"], cwd=str(tmp_path),
        )
