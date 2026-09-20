from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows service runtime test")


def test_windows_process_exec_works_under_selector_loop(tmp_path: Path):
    from veraport_agent.core import ClaimMode, LaneRegistry, ResourceClaim
    from veraport_agent.executor import LocalExecutor

    registry = LaneRegistry({"process.exec"})
    claim = ResourceClaim(f"cwd:{tmp_path.resolve().as_posix()}", ClaimMode.WRITE)
    lane = registry.open_lane(
        lane_id="selector-proc",
        task_id="selector-proc",
        capabilities={"process.exec"},
        claims=(claim,),
    )
    executor = LocalExecutor(
        registry,
        allowed_roots=(tmp_path,),
        allow_process_exec=True,
    )

    loop = asyncio.SelectorEventLoop()
    try:
        result = loop.run_until_complete(
            executor.run_process(
                lane_id=lane.lane_id,
                fencing_token=lane.fencing_token,
                argv=[sys.executable, "-c", "print('selector-ok')"],
                cwd=str(tmp_path),
                timeout_s=5,
            )
        )
    finally:
        loop.close()

    assert result.returncode == 0
    assert result.stdout.strip() == "selector-ok"


def test_service_loop_can_run_from_non_main_thread(monkeypatch):
    import threading
    import veraport_agent.windows_service as ws

    entered = threading.Event()
    release = threading.Event()
    errors = []

    async def fake_run(config_path, stop_event):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)

    monkeypatch.setattr(ws, "run_until_stop", fake_run)

    def target():
        try:
            stop = threading.Event()
            ws._run_service_loop("unused.json", stop)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=target)
    thread.start()
    assert entered.wait(2)
    release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert errors == []
