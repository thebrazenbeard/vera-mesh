from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import socket
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from .controller_config import ControllerConfig
from .controller_runtime import ControllerRuntime
from .lappy_host import start_host
from .provision import provision_local_pair
from .service_config import WindowsServiceConfig


class LocalQualificationError(RuntimeError):
    code = "LOCAL_QUALIFICATION_ERROR"


def _free_loopback_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _unwrap(response: dict[str, Any], operation: str) -> dict[str, Any]:
    if response.get("ok") is not True:
        raise LocalQualificationError(
            f"{operation} failed: {json.dumps(response, sort_keys=True)}"
        )
    result = response.get("result")
    if not isinstance(result, dict):
        raise LocalQualificationError(
            f"{operation} returned no result object"
        )
    return result


async def qualify_local(*, source_sha: str) -> dict[str, Any]:
    started = time.time()
    token = "veraport-qualified-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(
        prefix="VeraMesh-VeraPort-Qualification-"
    ) as raw_root:
        root = Path(raw_root).resolve()
        allowed = root / "allowed"
        identity = root / "identity"
        state = root / "state"
        allowed.mkdir()
        state.mkdir()

        sentinel = allowed / "sentinel.txt"
        sentinel.write_text(token, encoding="utf-8")

        manifest = provision_local_pair(
            identity,
            harden_windows_acl=False,
        )

        port = _free_loopback_port()
        service_config = WindowsServiceConfig.from_dict({
            "bind_host": "127.0.0.1",
            "bind_port": port,
            "allowed_roots": [str(allowed)],
            "state_db": str(state / "veraport.sqlite3"),
            "tls_cert": str(identity / "tls-cert.pem"),
            "tls_key": str(identity / "tls-key.pem"),
            "workstation_key": str(identity / "workstation-key.pem"),
            "controller_trust": str(identity / "controller-trust.json"),
            "allow_process_exec": False,
            "allow_non_loopback_listener": False,
            "max_lanes": 8,
            "max_inflight": 8,
            "max_read_bytes": 1_048_576,
        })

        prepared = None
        server = None
        runtime = None
        lane_id = "qualify-" + uuid.uuid4().hex
        fence: int | None = None
        try:
            prepared, server = await start_host(service_config)

            controller_config = ControllerConfig.from_dict({
                "schema": "VERAPORT_CONTROLLER_MCP_CONFIG_V1",
                "controller_key": str(identity / "controller-key.pem"),
                "tls_ca": str(identity / "tls-ca.pem"),
                "workstation_public_key": str(
                    identity / "workstation-public.pem"
                ),
                "requested_capabilities": ["fs.read"],
                "gateway_operations": [
                    "lane.list",
                    "lane.open",
                    "lane.close",
                    "fs.read_text",
                ],
                "endpoints": [{
                    "endpoint_id": "local-qualification",
                    "mode": "DIRECT_STREAM",
                    "host": "127.0.0.1",
                    "port": port,
                    "server_hostname": "localhost",
                    "durable_idempotency": True,
                }],
                "max_path_age_ms": 5_000,
                "connect_timeout_s": 5.0,
                "request_timeout_s": 5.0,
            })
            runtime = ControllerRuntime(controller_config)

            machine = await runtime.machine_info()
            if machine.get("workstation_principal") != manifest.get(
                "workstation_principal"
            ):
                raise LocalQualificationError(
                    "runtime workstation principal does not match "
                    "generated identity manifest"
                )
            if machine.get("controller_principal") != manifest.get(
                "controller_principal"
            ):
                raise LocalQualificationError(
                    "runtime controller principal does not match "
                    "generated identity manifest"
                )
            if machine.get("requested_capabilities") != ["fs.read"]:
                raise LocalQualificationError(
                    "controller requested capability ceiling drifted"
                )
            paths = machine.get("paths")
            if not isinstance(paths, list) or len(paths) != 1:
                raise LocalQualificationError(
                    "expected exactly one qualified local path"
                )
            path_info = paths[0]
            if path_info.get("authenticated") is not True:
                raise LocalQualificationError(
                    "local path was not application-authenticated"
                )
            if path_info.get("data_plane_verified") is not True:
                raise LocalQualificationError(
                    "local path data plane was not verified"
                )
            if path_info.get("granted_capabilities") != ["fs.read"]:
                raise LocalQualificationError(
                    "workstation granted capabilities outside read-only ceiling"
                )

            opened = _unwrap(
                await runtime.open_lane(
                    lane_id=lane_id,
                    task_id="local-qualification",
                    capabilities=["fs.read"],
                    claims=[{
                        "key": f"fs:{sentinel}",
                        "mode": "read",
                    }],
                    ttl_s=60.0,
                ),
                "lane.open",
            )
            raw_fence = opened.get("fencing_token")
            if type(raw_fence) is not int or raw_fence < 1:
                raise LocalQualificationError(
                    "lane.open returned no valid fencing token"
                )
            fence = raw_fence

            read = _unwrap(
                await runtime.read_text(
                    lane_id=lane_id,
                    fencing_token=fence,
                    path=str(sentinel),
                    encoding="utf-8",
                ),
                "fs.read_text",
            )
            if read.get("content") != token:
                raise LocalQualificationError(
                    "authenticated read returned unexpected content"
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
                "schema": "VERAPORT_LOCAL_QUALIFICATION_RECEIPT_V1",
                "pass": True,
                "source_sha": source_sha,
                "qualification_scope": (
                    "ephemeral loopback VeraPort TLS/application-auth "
                    "read-only qualification; no service install"
                ),
                "host_observation": {
                    "platform": platform.platform(),
                    "hostname": platform.node(),
                    "python": platform.python_version(),
                },
                "identity": {
                    "workstation_principal": manifest[
                        "workstation_principal"
                    ],
                    "controller_principal": manifest[
                        "controller_principal"
                    ],
                },
                "path": {
                    "mode": path_info.get("mode"),
                    "selected_path_id": machine.get("selected_path_id"),
                    "authenticated": path_info.get("authenticated"),
                    "data_plane_verified": path_info.get(
                        "data_plane_verified"
                    ),
                    "granted_capabilities": path_info.get(
                        "granted_capabilities"
                    ),
                },
                "operation": {
                    "lane_opened": True,
                    "read_only_claim": f"fs:{sentinel}",
                    "sentinel_match": True,
                    "lane_closed": bool(closed.get("closed", True)),
                },
                "effects": {
                    "windows_service_installed": False,
                    "firewall_changed": False,
                    "programdata_written": False,
                    "process_execution_enabled": False,
                    "persistent_identity_created": False,
                },
                "elapsed_ms": round((time.time() - started) * 1000, 3),
            }
        finally:
            if runtime is not None:
                try:
                    if fence is not None:
                        await runtime.close_lane(
                            lane_id=lane_id,
                            fencing_token=fence,
                        )
                except Exception:
                    pass
                try:
                    await runtime.close()
                except Exception:
                    pass
            if server is not None:
                server.close()
                try:
                    await server.wait_closed()
                except Exception:
                    pass
            if prepared is not None:
                try:
                    close_all = getattr(
                        prepared.agent,
                        "close_all_processes",
                        None,
                    )
                    if callable(close_all):
                        await close_all()
                finally:
                    prepared.state_store.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run an ephemeral loopback qualification of the real VeraPort "
            "TLS/application-auth/data-plane stack."
        )
    )
    parser.add_argument(
        "--source-sha",
        required=True,
        help="exact VeraMesh source commit being qualified",
    )
    args = parser.parse_args()
    receipt = asyncio.run(
        qualify_local(source_sha=args.source_sha.strip())
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
