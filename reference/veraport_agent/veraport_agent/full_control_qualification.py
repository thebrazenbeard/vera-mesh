from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from .controller_config import ControllerConfig
from .controller_runtime import ControllerRuntime


def _result(response: dict[str, Any], operation: str) -> dict[str, Any]:
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise RuntimeError(f"{operation} failed: {response!r}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"{operation} returned no result object")
    return result


async def qualify(controller_config: str | Path, root: str) -> dict:
    config = ControllerConfig.load(controller_config)
    runtime = ControllerRuntime(config)
    token = uuid.uuid4().hex
    path = str(Path(root) / ("vera-rdc-qualification-" + token + ".txt"))
    try:
        info = await runtime.machine_info()

        fs_lane = "qual-fs-" + token
        opened = _result(
            await runtime.open_lane(
                lane_id=fs_lane,
                task_id="chatgpt-rdc-filesystem-qualification",
                capabilities=["fs.read", "fs.write"],
                claims=[{"key": "fs:" + root.replace("\\", "/"), "mode": "write"}],
                ttl_s=120,
            ),
            "lane.open(filesystem)",
        )
        fence = int(opened["fencing_token"])
        payload = "VERA_RDC_" + token
        _result(
            await runtime.gateway.call_operation(
                "fs.write_text",
                lane_id=fs_lane,
                fencing_token=fence,
                path=path,
                content=payload,
                encoding="utf-8",
            ),
            "fs.write_text",
        )
        readback = _result(
            await runtime.read_text(
                lane_id=fs_lane,
                fencing_token=fence,
                path=path,
                encoding="utf-8",
            ),
            "fs.read_text",
        )
        _result(
            await runtime.close_lane(lane_id=fs_lane, fencing_token=fence),
            "lane.close(filesystem)",
        )
        if readback.get("content") != payload:
            raise RuntimeError("filesystem write/read sentinel mismatch")

        exec_lane = "qual-exec-" + token
        opened = _result(
            await runtime.open_lane(
                lane_id=exec_lane,
                task_id="chatgpt-rdc-process-exec-qualification",
                capabilities=["process.exec"],
                claims=[{"key": "cwd:" + root.replace("\\", "/"), "mode": "write"}],
                ttl_s=120,
            ),
            "lane.open(process.exec)",
        )
        exec_fence = int(opened["fencing_token"])
        process_result = _result(
            await runtime.gateway.call_operation(
                "process.exec",
                lane_id=exec_lane,
                fencing_token=exec_fence,
                argv=["cmd.exe", "/d", "/c", "whoami & hostname"],
                cwd=root,
                timeout_s=30.0,
            ),
            "process.exec",
        )
        _result(
            await runtime.close_lane(
                lane_id=exec_lane,
                fencing_token=exec_fence,
            ),
            "lane.close(process.exec)",
        )
        if process_result.get("returncode") != 0:
            raise RuntimeError("process.exec qualification returned nonzero")
        stdout = str(process_result.get("stdout", "")).strip()
        if not stdout:
            raise RuntimeError("process.exec qualification returned empty stdout")

        managed_lane = "qual-managed-" + token
        managed_caps = [
            "process.exec",
            "process.inspect",
            "process.interact",
            "process.control",
        ]
        opened = _result(
            await runtime.open_lane(
                lane_id=managed_lane,
                task_id="chatgpt-rdc-managed-process-qualification",
                capabilities=managed_caps,
                claims=[{"key": "cwd:" + root.replace("\\", "/"), "mode": "write"}],
                ttl_s=120,
            ),
            "lane.open(managed-process)",
        )
        managed_fence = int(opened["fencing_token"])
        started = _result(
            await runtime.gateway.call_operation(
                "process.start",
                lane_id=managed_lane,
                fencing_token=managed_fence,
                argv=["cmd.exe", "/d", "/q"],
                cwd=root,
                max_runtime_s=30.0,
            ),
            "process.start",
        )
        handle = str(started.get("process_handle", ""))
        if not handle:
            raise RuntimeError("process.start returned no process handle")

        managed_token = "VERA_MANAGED_" + token
        _result(
            await runtime.gateway.call_operation(
                "process.input",
                lane_id=managed_lane,
                fencing_token=managed_fence,
                process_handle=handle,
                input_text="echo " + managed_token,
                append_newline=True,
            ),
            "process.input(echo)",
        )
        listed = _result(
            await runtime.gateway.call_operation(
                "process.list",
                lane_id=managed_lane,
                fencing_token=managed_fence,
            ),
            "process.list",
        )
        if handle not in {
            str(item.get("process_handle"))
            for item in listed.get("processes", [])
            if isinstance(item, dict)
        }:
            raise RuntimeError("managed process not visible in process.list")

        _result(
            await runtime.gateway.call_operation(
                "process.input",
                lane_id=managed_lane,
                fencing_token=managed_fence,
                process_handle=handle,
                input_text="exit",
                append_newline=True,
            ),
            "process.input(exit)",
        )

        deadline = time.monotonic() + 10.0
        final_status: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            final_status = _result(
                await runtime.gateway.call_operation(
                    "process.status",
                    lane_id=managed_lane,
                    fencing_token=managed_fence,
                    process_handle=handle,
                ),
                "process.status",
            )
            if final_status.get("running") is False:
                break
            await asyncio.sleep(0.1)
        if final_status is None or final_status.get("running") is not False:
            _result(
                await runtime.gateway.call_operation(
                    "process.terminate",
                    lane_id=managed_lane,
                    fencing_token=managed_fence,
                    process_handle=handle,
                    grace_s=1.0,
                ),
                "process.terminate",
            )
            raise RuntimeError("managed process did not exit after input")

        managed_output = _result(
            await runtime.gateway.call_operation(
                "process.output",
                lane_id=managed_lane,
                fencing_token=managed_fence,
                process_handle=handle,
                stdout_offset=0,
                stderr_offset=0,
                max_bytes=16384,
            ),
            "process.output",
        )
        _result(
            await runtime.close_lane(
                lane_id=managed_lane,
                fencing_token=managed_fence,
            ),
            "lane.close(managed-process)",
        )
        if managed_token not in str(managed_output.get("stdout", "")):
            raise RuntimeError("managed process input/output sentinel mismatch")

        return {
            "schema": "VERAPORT_RDC_CONTROL_LOCAL_QUALIFICATION_V2",
            "status": "PASS",
            "filesystem": {
                "root": root,
                "sentinel_path": path,
                "write_read_match": True,
            },
            "process_exec": {
                "argv": ["cmd.exe", "/d", "/c", "whoami & hostname"],
                "returncode": process_result.get("returncode"),
                "stdout": stdout,
                "stderr": process_result.get("stderr", ""),
                "stdout_truncated": process_result.get("stdout_truncated", False),
                "stderr_truncated": process_result.get("stderr_truncated", False),
            },
            "managed_process": {
                "process_handle": handle,
                "start": started,
                "final_status": final_status,
                "output_contains_sentinel": True,
                "stdout": managed_output.get("stdout", ""),
                "stderr": managed_output.get("stderr", ""),
                "process_list_visible": True,
                "input_roundtrip": True,
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
