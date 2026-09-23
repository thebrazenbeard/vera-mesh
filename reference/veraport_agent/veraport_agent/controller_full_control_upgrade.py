from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .controller_capability_upgrade import (
    ControllerCapabilityUpgradeError,
    _acquire_recovery_lock,
    _atomic_replace,
    _load_controller_private,
    _release_recovery_lock,
    _strict_json,
    _write_or_verify,
)
from .controller_config import ControllerConfig
from .hot_session import principal_id
from .identity_store import load_identity
from .service_config import WindowsServiceConfig

TARGET_CAPABILITIES = frozenset({
    "fs.read",
    "fs.write",
    "process.exec",
    "process.inspect",
    "process.interact",
    "process.control",
})
ALLOWED_SOURCE_CAPABILITIES = (
    frozenset({"fs.read"}),
    frozenset({"fs.read", "fs.write"}),
    TARGET_CAPABILITIES,
)
WRITE_OPERATIONS = (
    "fs.write_text",
    "fs.append_text",
    "fs.mkdir",
    "fs.move",
    "fs.replace_text",
)
PROCESS_OPERATIONS = (
    "process.exec",
    "process.start",
    "process.list",
    "process.status",
    "process.output",
    "process.input",
    "process.terminate",
)


class FullControlUpgradeError(ControllerCapabilityUpgradeError):
    code = "FULL_CONTROL_UPGRADE_ERROR"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_service_document(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise FullControlUpgradeError(f"cannot parse VeraPort service config: {path}") from exc
    if not isinstance(value, dict):
        raise FullControlUpgradeError("VeraPort service config must be object")
    return value, raw


def upgrade_tunnel_controller_full_control(
    *,
    service_config_path: str | Path,
    controller_config_path: str | Path,
) -> dict[str, Any]:
    service_path = Path(service_config_path).expanduser().resolve()
    controller_path = Path(controller_config_path).expanduser().resolve()

    service = WindowsServiceConfig.load(service_path)
    service.validate_runtime_files()
    config = ControllerConfig.load(controller_path)

    private = _load_controller_private(config.controller_key)
    controller_principal = principal_id(private.public_key(), "controller")

    identity = load_identity(
        workstation_key_path=service.workstation_key,
        controller_trust_path=service.controller_trust,
    )
    current_ceiling = identity.capability_policy.get(controller_principal)
    if current_ceiling is None:
        raise FullControlUpgradeError("tunnel controller is not enrolled in VeraPort trust")
    current_ceiling = frozenset(current_ceiling)
    if current_ceiling not in ALLOWED_SOURCE_CAPABILITIES:
        raise FullControlUpgradeError(
            "tunnel controller capability ceiling is outside the bounded "
            "fs.read -> fs.read+fs.write -> full-control upgrade chain"
        )
    if config.requested_capabilities not in ALLOWED_SOURCE_CAPABILITIES:
        raise FullControlUpgradeError(
            "controller requested_capabilities are outside the bounded upgrade chain"
        )

    trust_doc, trust_raw = _strict_json(
        service.controller_trust,
        "VERAPORT_CONTROLLER_TRUST_V1",
    )
    controller_doc, controller_raw = _strict_json(
        controller_path,
        "VERAPORT_CONTROLLER_MCP_CONFIG_V1",
    )
    service_doc, service_raw = _load_service_document(service_path)

    entries = trust_doc.get("controllers")
    if not isinstance(entries, list):
        raise FullControlUpgradeError("controller trust list is invalid")
    matching = [
        entry for entry in entries
        if isinstance(entry, dict) and entry.get("principal") == controller_principal
    ]
    if len(matching) != 1:
        raise FullControlUpgradeError(
            "tunnel controller principal must have exactly one trust entry"
        )
    entry_caps = frozenset(str(v) for v in matching[0].get("capabilities", []))
    if entry_caps != current_ceiling:
        raise FullControlUpgradeError(
            "controller trust JSON and loaded capability policy disagree"
        )

    tag = "pre-chatgpt-fullcontrol-"
    trust_backup = service.controller_trust.with_name(
        service.controller_trust.name + "." + tag + _sha256(trust_raw)[:12] + ".bak"
    )
    controller_backup = controller_path.with_name(
        controller_path.name + "." + tag + _sha256(controller_raw)[:12] + ".bak"
    )
    service_backup = service_path.with_name(
        service_path.name + "." + tag + _sha256(service_raw)[:12] + ".bak"
    )
    lock_path = service.controller_trust.with_name(
        service.controller_trust.name + ".recovery.lock"
    )

    _acquire_recovery_lock(lock_path)
    trust_changed = False
    controller_changed = False
    service_changed = False
    try:
        if service.controller_trust.read_bytes() != trust_raw:
            raise FullControlUpgradeError("controller trust changed before upgrade")
        if controller_path.read_bytes() != controller_raw:
            raise FullControlUpgradeError("controller config changed before upgrade")
        if service_path.read_bytes() != service_raw:
            raise FullControlUpgradeError("service config changed before upgrade")

        _write_or_verify(trust_backup, trust_raw)
        _write_or_verify(controller_backup, controller_raw)
        _write_or_verify(service_backup, service_raw)

        if current_ceiling != TARGET_CAPABILITIES:
            matching[0]["capabilities"] = sorted(TARGET_CAPABILITIES)
            _atomic_replace(
                service.controller_trust,
                (json.dumps(trust_doc, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            )
            trust_changed = True

        desired_ops = list(controller_doc.get("gateway_operations", []))
        for op in (*WRITE_OPERATIONS, *PROCESS_OPERATIONS):
            if op not in desired_ops:
                desired_ops.append(op)
        controller_doc["requested_capabilities"] = sorted(TARGET_CAPABILITIES)
        controller_doc["gateway_operations"] = desired_ops
        desired_controller = (
            json.dumps(controller_doc, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        if desired_controller != controller_raw:
            _atomic_replace(controller_path, desired_controller)
            controller_changed = True

        if service_doc.get("allow_process_exec") is not True:
            service_doc["allow_process_exec"] = True
            desired_service = (
                json.dumps(service_doc, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            _atomic_replace(service_path, desired_service)
            service_changed = True

        after_service = WindowsServiceConfig.load(service_path)
        after_service.validate_runtime_files()
        if not after_service.allow_process_exec:
            raise FullControlUpgradeError(
                "post-upgrade VeraPort local process policy is still disabled"
            )
        after_identity = load_identity(
            workstation_key_path=after_service.workstation_key,
            controller_trust_path=after_service.controller_trust,
        )
        if frozenset(after_identity.capability_policy.get(controller_principal, ())) != TARGET_CAPABILITIES:
            raise FullControlUpgradeError(
                "post-upgrade controller trust is not exact full-control capability set"
            )
        after_config = ControllerConfig.load(controller_path)
        if after_config.requested_capabilities != TARGET_CAPABILITIES:
            raise FullControlUpgradeError(
                "post-upgrade controller requested capabilities are not exact"
            )
        missing = set(WRITE_OPERATIONS + PROCESS_OPERATIONS) - set(
            after_config.gateway_operations
        )
        if missing:
            raise FullControlUpgradeError(
                f"post-upgrade controller is missing operations: {sorted(missing)}"
            )

        return {
            "schema": "VERAMESH_LAPPY_TUNNEL_FULL_CONTROL_UPGRADE_V1",
            "controller_principal": controller_principal,
            "capabilities_before": sorted(current_ceiling),
            "capabilities_after": sorted(TARGET_CAPABILITIES),
            "write_operations": list(WRITE_OPERATIONS),
            "process_operations": list(PROCESS_OPERATIONS),
            "process_execution_enabled": True,
            "trust_changed": trust_changed,
            "controller_config_changed": controller_changed,
            "service_config_changed": service_changed,
            "service_restart_required": trust_changed or service_changed,
            "tunnel_restart_required": controller_changed or trust_changed or service_changed,
            "trust_backup": str(trust_backup),
            "controller_config_backup": str(controller_backup),
            "service_config_backup": str(service_backup),
            "old_controller_entries_preserved": True,
        }
    except Exception:
        try:
            if trust_changed:
                _atomic_replace(service.controller_trust, trust_raw)
            if controller_changed:
                _atomic_replace(controller_path, controller_raw)
            if service_changed:
                _atomic_replace(service_path, service_raw)
        except Exception as rollback_exc:
            raise FullControlUpgradeError(
                "full-control upgrade failed and rollback also failed; "
                f"manual reconciliation required: {rollback_exc}"
            ) from rollback_exc
        raise
    finally:
        _release_recovery_lock(lock_path)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Upgrade the existing ChatGPT Secure MCP tunnel controller and "
            "VeraPort local policy to full filesystem + managed-process control."
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
    args = parser.parse_args()
    print(json.dumps(
        upgrade_tunnel_controller_full_control(
            service_config_path=args.service_config,
            controller_config_path=args.controller_config,
        ),
        indent=2,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
