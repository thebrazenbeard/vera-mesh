from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization

from .controller_config import ControllerConfig
from .controller_runtime import ControllerRuntime
from .service_config import WindowsServiceConfig
from .tunnel_runtime_service import (
    TunnelRuntimeServiceConfig,
    read_status as read_tunnel_status,
    validate_service_materials as validate_tunnel_materials,
)
from .windows_acl import (
    validate_controller_materials,
    validate_service_materials as validate_veraport_materials,
)


DEFAULT_SERVICE_CONFIG = Path(r"C:\ProgramData\VeraMesh\veraport.json")
DEFAULT_CONTROLLER_CONFIG = Path(r"C:\ProgramData\VeraMesh\controller.json")
DEFAULT_TUNNEL_CONFIG = Path(r"C:\ProgramData\VeraMesh\tunnel-runtime.json")


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    state: str
    detail: str


def _public_numbers_from_private(path: Path):
    key = serialization.load_pem_private_key(
        path.read_bytes(),
        password=None,
    )
    return key.public_key().public_numbers()


def _public_numbers(path: Path):
    key = serialization.load_pem_public_key(path.read_bytes())
    return key.public_numbers()


def _check(name: str, fn) -> DoctorCheck:
    try:
        detail = fn()
    except Exception as exc:
        return DoctorCheck(name, "FAIL", str(exc))
    return DoctorCheck(name, "PASS", str(detail or "ok"))


def offline_doctor(
    *,
    service_config_path: str | Path = DEFAULT_SERVICE_CONFIG,
    controller_config_path: str | Path = DEFAULT_CONTROLLER_CONFIG,
    tunnel_config_path: str | Path = DEFAULT_TUNNEL_CONFIG,
    check_windows_acl: bool | None = None,
) -> tuple[
    tuple[DoctorCheck, ...],
    WindowsServiceConfig | None,
    ControllerConfig | None,
    TunnelRuntimeServiceConfig | None,
]:
    service_path = Path(service_config_path).resolve()
    controller_path = Path(controller_config_path).resolve()
    tunnel_path = Path(tunnel_config_path).resolve()

    service: WindowsServiceConfig | None = None
    controller: ControllerConfig | None = None
    tunnel: TunnelRuntimeServiceConfig | None = None
    checks: list[DoctorCheck] = []

    def load_service():
        nonlocal service
        service = WindowsServiceConfig.load(service_path)
        service.validate_runtime_files()
        return service_path
    checks.append(_check("service_config", load_service))

    def load_controller():
        nonlocal controller
        controller = ControllerConfig.load(controller_path)
        required = (
            controller.controller_key,
            controller.tls_ca,
            controller.workstation_public_key,
        )
        missing = [path for path in required if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                "controller material missing: "
                + ", ".join(str(path) for path in missing)
            )
        return controller_path
    checks.append(_check("controller_config", load_controller))

    def load_tunnel():
        nonlocal tunnel
        tunnel = TunnelRuntimeServiceConfig.load(tunnel_path)
        tunnel.validate_runtime_files()
        return tunnel_path
    checks.append(_check("tunnel_config", load_tunnel))

    def cross_binding():
        if service is None or controller is None or tunnel is None:
            raise RuntimeError("dependent config failed to load")
        direct = [
            endpoint
            for endpoint in controller.endpoints
            if endpoint.host == service.bind_host
            and endpoint.port == service.bind_port
        ]
        if not direct:
            raise RuntimeError(
                "controller has no endpoint matching VeraPort bind host/port"
            )
        if _public_numbers_from_private(service.workstation_key) != _public_numbers(
            controller.workstation_public_key
        ):
            raise RuntimeError(
                "controller workstation pin does not match service identity"
            )
        if service.tls_cert.read_bytes() != controller.tls_ca.read_bytes():
            raise RuntimeError(
                "controller TLS trust anchor does not match VeraPort certificate"
            )
        if tunnel.controller_config.resolve() != controller_path:
            raise RuntimeError(
                "tunnel runtime points at a different controller config"
            )
        return "service/controller/tunnel lineage agrees"
    checks.append(_check("cross_binding", cross_binding))

    def process_policy():
        if service is None or controller is None:
            raise RuntimeError("dependent config failed to load")
        process_caps = {
            "process.exec",
            "process.inspect",
            "process.interact",
            "process.control",
        }
        requested = bool(
            process_caps.intersection(controller.requested_capabilities)
        )
        if requested != bool(service.allow_process_exec):
            raise RuntimeError(
                "controller process authority and workstation local policy disagree"
            )
        return (
            "process enabled"
            if service.allow_process_exec
            else "process disabled"
        )
    checks.append(_check("process_policy", process_policy))

    should_check_acl = (
        os.name == "nt"
        if check_windows_acl is None
        else bool(check_windows_acl)
    )
    if should_check_acl:
        def acl_check():
            if service is None or controller is None or tunnel is None:
                raise RuntimeError("dependent config failed to load")
            validate_veraport_materials(service_path, service)
            validate_controller_materials(controller_path, controller)
            validate_tunnel_materials(tunnel_path, tunnel)
            return "LocalSystem/Admin protected ACLs verified"
        checks.append(_check("windows_acl", acl_check))
    else:
        checks.append(DoctorCheck("windows_acl", "SKIP", "not requested"))

    return tuple(checks), service, controller, tunnel


async def doctor(
    *,
    service_config_path: str | Path = DEFAULT_SERVICE_CONFIG,
    controller_config_path: str | Path = DEFAULT_CONTROLLER_CONFIG,
    tunnel_config_path: str | Path = DEFAULT_TUNNEL_CONFIG,
    check_windows_acl: bool | None = None,
    live: bool = False,
    tunnel_status: bool = False,
) -> dict[str, Any]:
    checks, _, controller, tunnel = offline_doctor(
        service_config_path=service_config_path,
        controller_config_path=controller_config_path,
        tunnel_config_path=tunnel_config_path,
        check_windows_acl=check_windows_acl,
    )
    mutable = list(checks)
    machine_info = None
    tunnel_state = None

    if live:
        if controller is None:
            mutable.append(
                DoctorCheck(
                    "live_veraport",
                    "FAIL",
                    "controller config unavailable",
                )
            )
        else:
            runtime = ControllerRuntime(controller)
            try:
                try:
                    machine_info = await runtime.machine_info()
                except Exception as exc:
                    mutable.append(
                        DoctorCheck("live_veraport", "FAIL", str(exc))
                    )
                else:
                    selected = machine_info.get("selected_path_id")
                    paths = machine_info.get("paths")
                    current = next(
                        (
                            item
                            for item in paths
                            if isinstance(item, dict)
                            and item.get("path_id") == selected
                        ),
                        None,
                    ) if isinstance(paths, list) else None
                    if (
                        not isinstance(current, dict)
                        or current.get("authenticated") is not True
                        or current.get("data_plane_verified") is not True
                    ):
                        mutable.append(
                            DoctorCheck(
                                "live_veraport",
                                "FAIL",
                                "no authenticated data-plane-verified selected path",
                            )
                        )
                    else:
                        mutable.append(
                            DoctorCheck(
                                "live_veraport",
                                "PASS",
                                f"selected {selected}",
                            )
                        )
            finally:
                await runtime.close()
    else:
        mutable.append(
            DoctorCheck("live_veraport", "SKIP", "not requested")
        )

    if tunnel_status:
        if tunnel is None:
            mutable.append(
                DoctorCheck(
                    "tunnel_runtime",
                    "FAIL",
                    "tunnel config unavailable",
                )
            )
        else:
            try:
                state = read_tunnel_status(tunnel)
                tunnel_state = {
                    "process_running": state.process_running,
                    "healthy": state.healthy,
                    "ready": state.ready,
                }
                if state.usable:
                    mutable.append(
                        DoctorCheck(
                            "tunnel_runtime",
                            "PASS",
                            "process_running=true healthy=true",
                        )
                    )
                else:
                    mutable.append(
                        DoctorCheck(
                            "tunnel_runtime",
                            "FAIL",
                            json.dumps(tunnel_state, sort_keys=True),
                        )
                    )
            except Exception as exc:
                mutable.append(
                    DoctorCheck("tunnel_runtime", "FAIL", str(exc))
                )
    else:
        mutable.append(
            DoctorCheck("tunnel_runtime", "SKIP", "not requested")
        )

    ok = all(item.state in {"PASS", "SKIP"} for item in mutable)
    return {
        "schema": "VERAMESH_DOCTOR_V1",
        "ok": ok,
        "checks": [asdict(item) for item in mutable],
        "machine_info": machine_info,
        "tunnel_status": tunnel_state,
        "claim_ceiling": [
            "Read-only diagnosis of configured local VeraMesh state.",
            "A PASS does not install/start services or create a tunnel.",
            "Live VeraPort PASS does not imply ChatGPT tunnel registration.",
            "Tunnel runtime PASS does not imply VeraPort data-plane PASS unless live_veraport also passes.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only VeraMesh/Lappy connection diagnostics."
    )
    parser.add_argument(
        "--service-config",
        default=str(DEFAULT_SERVICE_CONFIG),
    )
    parser.add_argument(
        "--controller-config",
        default=str(DEFAULT_CONTROLLER_CONFIG),
    )
    parser.add_argument(
        "--tunnel-config",
        default=str(DEFAULT_TUNNEL_CONFIG),
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--tunnel-status", action="store_true")
    parser.add_argument(
        "--skip-windows-acl",
        action="store_true",
    )
    args = parser.parse_args()

    result = asyncio.run(
        doctor(
            service_config_path=args.service_config,
            controller_config_path=args.controller_config,
            tunnel_config_path=args.tunnel_config,
            check_windows_acl=(
                False if args.skip_windows_acl else None
            ),
            live=args.live,
            tunnel_status=args.tunnel_status,
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
