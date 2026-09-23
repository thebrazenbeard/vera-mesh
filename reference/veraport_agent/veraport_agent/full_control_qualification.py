from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path

from .controller_config import ControllerConfig
from .controller_runtime import ControllerRuntime


async def qualify(controller_config: str | Path, root: str) -> dict:
    config = ControllerConfig.load(controller_config)
    runtime = ControllerRuntime(config)
    token = uuid.uuid4().hex
    path = str(Path(root) / ("vera-rdc-qualification-" + token + ".txt"))
    try:
        info = await runtime.machine_info()

        fs_lane = "qual-fs-" + token
        opened = await runtime.open_lane(
            lane_id=fs_lane,
            task_id="chatgpt-rdc-filesystem-qualification",
            capabilities=["fs.read", "fs.write"],
            claims=[{"key": "fs:" + root.replace("\\", "/"), "mode": "write"}],
            ttl_s=120,
        )
        fence = opened["fencing_token"]
        payload = "VERA_RDC_" + token
        await runtime.gateway.call_operation(
            "fs.write_text",
            lane_id=fs_lane,
            fencing_token=fence,
            path=path,
            content=payload,
            encoding="utf-8",
        )
        readback = await runtime.read_text(
            lane_id=fs_lane,
            fencing_token=fence,
            path=path,
            encoding="utf-8",
        )
        await runtime.close_lane(lane_id=fs_lane, fencing_token=fence)
        if readback.get("text") != payload:
            raise RuntimeError("filesystem write/read sentinel mismatch")

        proc_lane = "qual-proc-" + token
        opened = await runtime.open_lane(
            lane_id=proc_lane,
            task_id="chatgpt-rdc-process-qualification",
            capabilities=["process.exec"],
            claims=[{"key": "cwd:" + root.replace("\\", "/"), "mode": "write"}],
            ttl_s=120,
        )
        pfence = opened["fencing_token"]
        result = await runtime.gateway.call_operation(
            "process.exec",
            lane_id=proc_lane,
            fencing_token=pfence,
            argv=["cmd.exe", "/d", "/c", "whoami & hostname"],
            cwd=root,
            timeout_s=30.0,
        )
        await runtime.close_lane(lane_id=proc_lane, fencing_token=pfence)
        if result.get("returncode") != 0:
            raise RuntimeError("process qualification returned nonzero")
        stdout = str(result.get("stdout", "")).strip()
        if not stdout:
            raise RuntimeError("process qualification returned empty stdout")

        return {
            "schema": "VERAPORT_RDC_CONTROL_LOCAL_QUALIFICATION_V1",
            "status": "PASS",
            "filesystem": {
                "root": root,
                "sentinel_path": path,
                "write_read_match": True,
            },
            "process": {
                "argv": ["cmd.exe", "/d", "/c", "whoami & hostname"],
                "returncode": result.get("returncode"),
                "stdout": stdout,
                "stderr": result.get("stderr", ""),
                "timed_out": result.get("timed_out", False),
            },
            "session": {
                "controller_principal": info.get("controller_principal"),
                "workstation_principal": info.get("workstation_principal"),
                "selected_path_id": info.get("selected_path_id"),
                "requested_capabilities": info.get("requested_capabilities"),
            },
        }
    finally:
        await runtime.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--controller-config",
        default=r"C:\ProgramData\VeraMesh\controller.json",
    )
    parser.add_argument("--root", default=r"C:\Temp")
    args = parser.parse_args()
    result = asyncio.run(qualify(args.controller_config, args.root))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
