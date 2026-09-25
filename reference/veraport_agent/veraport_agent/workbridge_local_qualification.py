from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Callable

from .controller_config import ControllerConfig
from .controller_runtime import ControllerRuntime
from .workbridge_local import _canonical_windows_path, _resource_key


class WorkBridgeQualificationError(RuntimeError):
    code = "WORKBRIDGE_LOCAL_QUALIFICATION_ERROR"


async def qualify_runtime(runtime: Any, path: str) -> dict[str, Any]:
    canonical = _canonical_windows_path(path)
    lane_id = "workbridge-qualification-" + uuid.uuid4().hex
    fence: int | None = None
    close_error: str | None = None
    try:
        await runtime.ensure_started()
        machine = await runtime.machine_info()
        opened = await runtime.open_lane(
            lane_id=lane_id,
            task_id="workbridge-local-read-qualification",
            capabilities=["fs.read"],
            claims=[{"key": _resource_key(canonical), "mode": "read"}],
            ttl_s=60.0,
        )
        fence = opened.get("fencing_token")
        if type(fence) is not int:
            raise WorkBridgeQualificationError("lane.open returned no integer fencing token")
        listed = await runtime.read_operation(
            "fs.list_dir",
            lane_id=lane_id,
            fencing_token=fence,
            path=canonical,
            offset=0,
            max_entries=200,
        )
        if listed.get("backend") != "workbridge":
            raise WorkBridgeQualificationError(
                "fs.list_dir did not traverse the WorkBridge local adapter"
            )
        entries = listed.get("entries")
        if not isinstance(entries, list):
            raise WorkBridgeQualificationError("fs.list_dir returned no entries list")
        return {
            "schema": "VERAPORT_WORKBRIDGE_LOCAL_QUALIFICATION_V1",
            "status": "PASS",
            "path": canonical.replace("\\", "/"),
            "resource_key": _resource_key(canonical),
            "backend": "workbridge",
            "entry_count_returned": len(entries),
            "total_entries": listed.get("total_entries"),
            "truncated": listed.get("truncated"),
            "entries": entries,
            "selected_path_id": machine.get("selected_path_id"),
            "workstation_principal": machine.get("workstation_principal"),
            "controller_principal": machine.get("controller_principal"),
            "process_execution_requested": any(
                str(item).startswith("process.")
                for item in machine.get("requested_capabilities", [])
            ),
        }
    finally:
        if fence is not None:
            try:
                await runtime.close_lane(
                    lane_id=lane_id,
                    fencing_token=fence,
                )
            except Exception as exc:
                close_error = str(exc)
        try:
            await runtime.close()
        except Exception as exc:
            if close_error is None:
                close_error = str(exc)
        if close_error is not None:
            raise WorkBridgeQualificationError(
                "qualification cleanup failed: " + close_error
            )


async def qualify(
    controller_config_path: str | Path,
    path: str,
    *,
    runtime_factory: Callable[[ControllerConfig], Any] = ControllerRuntime,
) -> dict[str, Any]:
    config = ControllerConfig.load(controller_config_path)
    if "fs.read" not in config.requested_capabilities:
        raise WorkBridgeQualificationError("controller does not request fs.read")
    if "fs.list_dir" not in config.gateway_operations:
        raise WorkBridgeQualificationError("controller does not expose fs.list_dir")
    if any(item.startswith("process.") for item in config.requested_capabilities):
        raise WorkBridgeQualificationError(
            "qualification refuses a process-bearing controller capability set"
        )
    runtime = runtime_factory(config)
    return await qualify_runtime(runtime, path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Qualify authenticated VeraPort to local WorkBridge read routing."
    )
    parser.add_argument(
        "--controller-config",
        default=r"C:\ProgramData\VeraMesh\controller.json",
    )
    parser.add_argument("--path", default=r"C:\Vera")
    args = parser.parse_args()
    try:
        result = asyncio.run(qualify(args.controller_config, args.path))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema": "VERAPORT_WORKBRIDGE_LOCAL_QUALIFICATION_V1",
                    "status": "FAIL",
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise SystemExit(1)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
