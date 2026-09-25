from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from .controller_config import ControllerConfig
from .controller_runtime import ControllerRuntime


class ServiceQualificationError(RuntimeError):
    code = "SERVICE_QUALIFICATION_ERROR"


def _unwrap(response: dict[str, Any], operation: str) -> dict[str, Any]:
    if response.get("ok") is not True:
        raise ServiceQualificationError(
            f"{operation} failed: {json.dumps(response, sort_keys=True)}"
        )
    result = response.get("result")
    if not isinstance(result, dict):
        raise ServiceQualificationError(
            f"{operation} returned no result object"
        )
    return result


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def qualify_service(
    *,
    controller_config_path: str | Path,
    probe_path: str | Path,
    expected_token: str,
    source_sha: str,
    expected_workstation_principal: str | None = None,
) -> dict[str, Any]:
    started = time.time()
    controller_path = Path(controller_config_path).resolve()
    probe = Path(probe_path).resolve()
    if not controller_path.is_file():
        raise ServiceQualificationError(
            f"controller config missing: {controller_path}"
        )
    if not probe.is_file():
        raise ServiceQualificationError(f"probe file missing: {probe}")
    if not expected_token:
        raise ServiceQualificationError("expected token is required")

    runtime = ControllerRuntime(ControllerConfig.load(controller_path))
    lane_id = "service-qualify-" + uuid.uuid4().hex
    fence: int | None = None
    try:
        machine = await runtime.machine_info()
        principal = machine.get("workstation_principal")
        if not isinstance(principal, str) or not principal:
            raise ServiceQualificationError(
                "service reported no workstation principal"
            )
        if (
            expected_workstation_principal is not None
            and principal != expected_workstation_principal
        ):
            raise ServiceQualificationError(
                "service workstation principal changed"
            )
        paths = machine.get("paths")
        if not isinstance(paths, list) or not paths:
            raise ServiceQualificationError(
                "controller reported no VeraPort paths"
            )
        selected_id = machine.get("selected_path_id")
        selected = next(
            (
                item
                for item in paths
                if isinstance(item, dict)
                and item.get("path_id") == selected_id
            ),
            None,
        )
        if not isinstance(selected, dict):
            raise ServiceQualificationError(
                "selected VeraPort path is missing from path state"
            )
        if selected.get("authenticated") is not True:
            raise ServiceQualificationError(
                "selected service path is not application-authenticated"
            )
        if selected.get("data_plane_verified") is not True:
            raise ServiceQualificationError(
                "selected service data plane is not verified"
            )
        granted = selected.get("granted_capabilities")
        if not isinstance(granted, list) or "fs.read" not in granted:
            raise ServiceQualificationError(
                "selected service path lacks fs.read"
            )

        opened = _unwrap(
            await runtime.open_lane(
                lane_id=lane_id,
                task_id="service-qualification",
                capabilities=["fs.read"],
                claims=[{
                    "key": "fs:" + probe.as_posix(),
                    "mode": "read",
                }],
                ttl_s=60.0,
            ),
            "lane.open",
        )
        raw_fence = opened.get("fencing_token")
        if type(raw_fence) is not int or raw_fence < 1:
            raise ServiceQualificationError(
                "lane.open returned no valid fencing token"
            )
        fence = raw_fence

        read = _unwrap(
            await runtime.read_text(
                lane_id=lane_id,
                fencing_token=fence,
                path=str(probe),
                encoding="utf-8",
            ),
            "fs.read_text",
        )
        if read.get("content") != expected_token:
            raise ServiceQualificationError(
                "service probe content does not match expected token"
            )

        closed = _unwrap(
            await runtime.close_lane(
                lane_id=lane_id,
                fencing_token=fence,
            ),
            "lane.close",
        )
        fence = None

        return {
            "schema": "VERAPORT_WINDOWS_SERVICE_QUALIFICATION_RECEIPT_V1",
            "pass": True,
            "source_sha": source_sha,
            "controller_config_sha256": _sha256(controller_path),
            "workstation_principal": principal,
            "controller_principal": machine.get("controller_principal"),
            "selected_path_id": selected_id,
            "path_mode": selected.get("mode"),
            "authenticated": True,
            "data_plane_verified": True,
            "granted_capabilities": granted,
            "probe": {
                "path": probe.as_posix(),
                "token_match": True,
                "lane_closed": bool(closed.get("closed", True)),
            },
            "elapsed_ms": round((time.time() - started) * 1000, 3),
            "claim_ceiling": [
                "Qualifies an already-running VeraPort service endpoint only.",
                "Does not prove how the service was installed.",
                "Does not prove restart survival unless run again after restart.",
                "Does not prove non-loopback reachability unless the controller endpoint used a non-loopback address.",
            ],
        }
    finally:
        try:
            if fence is not None:
                await runtime.close_lane(
                    lane_id=lane_id,
                    fencing_token=fence,
                )
        except Exception:
            pass
        await runtime.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Authenticate to an already-running VeraPort Windows service and "
            "verify a read-only probe through the real data plane."
        )
    )
    parser.add_argument("--controller-config", required=True)
    parser.add_argument("--probe-path", required=True)
    parser.add_argument("--expected-token", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--expected-workstation-principal")
    args = parser.parse_args()

    receipt = asyncio.run(
        qualify_service(
            controller_config_path=args.controller_config,
            probe_path=args.probe_path,
            expected_token=args.expected_token,
            source_sha=args.source_sha,
            expected_workstation_principal=(
                args.expected_workstation_principal
            ),
        )
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
