from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .controller_config import ControllerConfig
from .controller_runtime import ControllerRuntime
from .hot_session import principal_id
from .identity_store import load_identity
from .service_config import WindowsServiceConfig
from .windows_acl import validate_service_materials


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    state: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "state": self.state,
            "detail": self.detail,
        }


def _pass(name: str, detail: str) -> DoctorCheck:
    return DoctorCheck(name, "PASS", detail)


def _fail(name: str, exc: Exception | str) -> DoctorCheck:
    return DoctorCheck(name, "FAIL", str(exc))


def _skip(name: str, detail: str) -> DoctorCheck:
    return DoctorCheck(name, "SKIP", detail)


def _load_ec_private(path: Path) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise ValueError("key must be EC P-256")
    return key


def _load_ec_public(path: Path) -> ec.EllipticCurvePublicKey:
    key = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise ValueError("key must be EC P-256")
    return key


def _public_der(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def offline_doctor(
    *,
    service_config_path: str | Path,
    controller_config_path: str | Path,
    check_windows_acl: bool = True,
) -> tuple[list[DoctorCheck], WindowsServiceConfig | None, ControllerConfig | None]:
    checks: list[DoctorCheck] = []
    service: WindowsServiceConfig | None = None
    controller: ControllerConfig | None = None

    try:
        service = WindowsServiceConfig.load(service_config_path)
        service.validate_runtime_files()
        checks.append(
            _pass(
                "service_config",
                f"valid; roots={len(service.allowed_roots)} "
                f"process={'enabled' if service.allow_process_exec else 'disabled'}",
            )
        )
    except Exception as exc:
        checks.append(_fail("service_config", exc))

    try:
        controller = ControllerConfig.load(controller_config_path)
        checks.append(
            _pass(
                "controller_config",
                f"valid; endpoints={len(controller.endpoints)} "
                f"operations={len(controller.gateway_operations)}",
            )
        )
    except Exception as exc:
        checks.append(_fail("controller_config", exc))

    if service is not None:
        if os.name == "nt" and check_windows_acl:
            try:
                validate_service_materials(service_config_path, service)
                checks.append(
                    _pass("windows_acl", "LocalSystem material ACL profile valid")
                )
            except Exception as exc:
                checks.append(_fail("windows_acl", exc))
        else:
            checks.append(
                _skip(
                    "windows_acl",
                    "not checked on this platform"
                    if os.name != "nt"
                    else "disabled by caller",
                )
            )

    if service is not None and controller is not None:
        try:
            identity = load_identity(
                workstation_key_path=service.workstation_key,
                controller_trust_path=service.controller_trust,
            )
            controller_private = _load_ec_private(controller.controller_key)
            controller_principal = principal_id(
                controller_private.public_key(), "controller"
            )
            if controller_principal not in identity.allowed_controllers:
                raise ValueError(
                    "controller private key is absent from workstation trust"
                )

            workstation_private = _load_ec_private(service.workstation_key)
            workstation_public = _load_ec_public(
                controller.workstation_public_key
            )
            if _public_der(workstation_private.public_key()) != _public_der(
                workstation_public
            ):
                raise ValueError(
                    "controller workstation pin does not match service identity"
                )
            checks.append(
                _pass(
                    "application_identity",
                    "controller trust and workstation pin cross-bind",
                )
            )
        except Exception as exc:
            checks.append(_fail("application_identity", exc))

        try:
            process_caps = {
                "process.exec",
                "process.inspect",
                "process.interact",
                "process.control",
            }
            requested = controller.requested_capabilities
            has_process = bool(process_caps & requested)
            if service.allow_process_exec:
                if not process_caps <= requested:
                    raise ValueError(
                        "service process policy is enabled but controller "
                        "capability ceiling is incomplete"
                    )
            elif has_process:
                raise ValueError(
                    "controller requests process authority while workstation "
                    "local process policy is disabled"
                )
            checks.append(
                _pass(
                    "process_policy",
                    "local process gate and controller capability ceiling agree",
                )
            )
        except Exception as exc:
            checks.append(_fail("process_policy", exc))

    return checks, service, controller


async def doctor(
    *,
    service_config_path: str | Path,
    controller_config_path: str | Path,
    check_windows_acl: bool = True,
    live: bool = True,
) -> dict[str, Any]:
    checks, _, controller = offline_doctor(
        service_config_path=service_config_path,
        controller_config_path=controller_config_path,
        check_windows_acl=check_windows_acl,
    )

    machine: dict[str, Any] | None = None
    if live:
        if controller is None:
            checks.append(
                _skip("live_paths", "controller config did not validate")
            )
        else:
            runtime = ControllerRuntime(controller)
            try:
                machine = await runtime.machine_info()
                selected = machine.get("selected_path_id")
                paths = machine.get("paths", [])
                checks.append(
                    _pass(
                        "live_paths",
                        f"selected={selected}; authenticated_paths={len(paths)}",
                    )
                )
            except Exception as exc:
                checks.append(_fail("live_paths", exc))
            finally:
                await runtime.close()
    else:
        checks.append(_skip("live_paths", "live network check disabled"))

    failed = [item for item in checks if item.state == "FAIL"]
    return {
        "schema": "VERAMESH_DOCTOR_V1",
        "ok": not failed,
        "checks": [item.as_dict() for item in checks],
        "machine_info": machine,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate VeraMesh local configuration, identity binding, policy, "
            "and optionally authenticated live VeraPort paths. Read-only."
        )
    )
    parser.add_argument(
        "--service-config",
        default=r"C:\ProgramData\VeraMesh\veraport.json",
    )
    parser.add_argument(
        "--controller-config",
        default=r"C:\ProgramData\VeraMesh\controller.json",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip live endpoint authentication/data-plane probing.",
    )
    parser.add_argument(
        "--skip-windows-acl",
        action="store_true",
        help="Skip Windows ACL validation (diagnostic only).",
    )
    args = parser.parse_args()

    result = asyncio.run(
        doctor(
            service_config_path=args.service_config,
            controller_config_path=args.controller_config,
            check_windows_acl=not args.skip_windows_acl,
            live=not args.offline,
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
