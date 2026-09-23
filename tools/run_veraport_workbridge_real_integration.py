from __future__ import annotations

import asyncio
import os
from pathlib import Path

from veraport_agent.core import LaneRegistry
from veraport_agent.executor import LocalExecutor
from veraport_agent.protocol import VeraPortAgent
from veraport_agent.workbridge_local import (
    WorkBridgeLocalBackend,
    WorkBridgeLocalConfig,
    _resource_key,
)


async def main() -> None:
    root = os.environ["VERAPORT_REAL_WB_ROOT"]
    fixture = os.environ["VERAPORT_REAL_WB_FIXTURE"]
    marker = os.environ["VERAPORT_REAL_WB_MARKER"]
    token_file = Path(os.environ["VERAPORT_REAL_WB_TOKEN_FILE"])
    executor_root = Path(os.environ["VERAPORT_REAL_EXECUTOR_ROOT"])

    registry = LaneRegistry({"fs.read", "fs.write"})
    executor = LocalExecutor(
        registry,
        allowed_roots=(executor_root,),
        allow_process_exec=False,
    )
    backend = WorkBridgeLocalBackend(
        WorkBridgeLocalConfig(
            endpoint="http://127.0.0.1:18765/mcp",
            bearer_token_file=token_file,
            read_roots=(root,),
            timeout_seconds=5.0,
        ),
        registry,
    )
    agent = VeraPortAgent(registry, executor, workbridge=backend)

    opened = await agent.handle(
        {
            "protocol_version": "veraport-v1",
            "request_id": "open",
            "operation": "lane.open",
            "lane_id": "real-wb",
            "task_id": "real-workbridge-integration",
            "capabilities": ["fs.read"],
            "claims": [{"key": _resource_key(root), "mode": "read"}],
            "ttl_s": 60,
        }
    )
    assert opened["ok"] is True, opened
    fence = opened["result"]["fencing_token"]

    try:
        listed = await agent.handle(
            {
                "protocol_version": "veraport-v1",
                "request_id": "list",
                "operation": "fs.list_dir",
                "lane_id": "real-wb",
                "fencing_token": fence,
                "path": root,
                "offset": 0,
                "max_entries": 20,
            }
        )
        assert listed["ok"] is True, listed
        assert listed["result"]["backend"] == "workbridge", listed

        stat = await agent.handle(
            {
                "protocol_version": "veraport-v1",
                "request_id": "stat",
                "operation": "fs.stat",
                "lane_id": "real-wb",
                "fencing_token": fence,
                "path": fixture,
            }
        )
        assert stat["ok"] is True, stat
        assert stat["result"]["backend"] == "workbridge", stat

        read = await agent.handle(
            {
                "protocol_version": "veraport-v1",
                "request_id": "read",
                "operation": "fs.read_text",
                "lane_id": "real-wb",
                "fencing_token": fence,
                "path": fixture,
                "encoding": "utf-8",
            }
        )
        assert read["ok"] is True, read
        assert read["result"]["backend"] == "workbridge", read
        assert read["result"]["content"] == marker, read

        denied_target = str(Path(fixture).with_name("must-not-exist.txt"))
        denied = await agent.handle(
            {
                "protocol_version": "veraport-v1",
                "request_id": "write",
                "operation": "fs.write_text",
                "lane_id": "real-wb",
                "fencing_token": fence,
                "path": denied_target,
                "content": "blocked",
                "encoding": "utf-8",
                "overwrite": False,
            }
        )
        assert denied["ok"] is False, denied
        assert not Path(denied_target).exists()

        print("REAL_VERAPORT_WORKBRIDGE_INTEGRATION=PASS")
    finally:
        closed = await agent.handle(
            {
                "protocol_version": "veraport-v1",
                "request_id": "close",
                "operation": "lane.close",
                "lane_id": "real-wb",
                "fencing_token": fence,
            }
        )
        assert closed["ok"] is True, closed


if __name__ == "__main__":
    asyncio.run(main())
