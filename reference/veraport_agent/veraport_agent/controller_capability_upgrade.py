from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .controller_config import ControllerConfig
from .controller_recovery import _acquire_recovery_lock, _release_recovery_lock
from .hot_session import principal_id
from .identity_store import load_identity
from .service_config import WindowsServiceConfig
from .windows_acl import harden_private_file

TARGET_CAPABILITIES = frozenset({"fs.read", "fs.write"})
ALLOWED_SOURCE_CAPABILITIES = (
    frozenset({"fs.read"}),
    TARGET_CAPABILITIES,
)
WRITE_OPERATIONS = (
    "fs.write_text",
    "fs.append_text",
    "fs.mkdir",
    "fs.move",
    "fs.replace_text",
)


class ControllerCapabilityUpgradeError(RuntimeError):
    code = "CONTROLLER_CAPABILITY_UPGRADE_ERROR"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ControllerCapabilityUpgradeError(
            f"refusing to overwrite existing file: {path}"
        ) from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _write_or_verify(path: Path, payload: bytes) -> None:
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise ControllerCapabilityUpgradeError(
                f"existing backup diverges: {path}"
            )
        return
    _write_new(path, payload)
    if os.name == "nt":
        harden_private_file(path)


def _atomic_replace(path: Path, payload: bytes) -> None:
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    _write_new(temp, payload)
    try:
        if os.name == "nt":
            harden_private_file(temp)
        os.replace(temp, path)
        if os.name == "nt":
            harden_private_file(path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def _load_controller_private(path: Path) -> ec.EllipticCurvePrivateKey:
    try:
        value = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except Exception as exc:
        raise ControllerCapabilityUpgradeError(
            f"cannot load tunnel controller private key: {path}"
        ) from exc
    if not isinstance(value, ec.EllipticCurvePrivateKey) or not isinstance(
        value.curve, ec.SECP256R1
    ):
        raise ControllerCapabilityUpgradeError(
            "tunnel controller private key must be EC P-256"
        )
    return value


def _strict_json(path: Path, expected_schema: str) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ControllerCapabilityUpgradeError(
            f"cannot parse strict UTF-8 JSON: {path}"
        ) from exc
    if not isinstance(value, dict) or value.get("schema") != expected_schema:
        raise ControllerCapabilityUpgradeError(
            f"wrong schema in {path}: expected {expected_schema}"
        )
    return value, raw


def upgrade_tunnel_controller_readwrite(
    *,
    service_config_path: str | Path,
    controller_config_path: str | Path,
) -> dict[str, Any]:
    service_path = Path(service_config_path).expanduser().resolve()
    controller_path = Path(controller_config_path).expanduser().resolve()

    service = WindowsServiceConfig.load(service_path)
    service.validate_runtime_files()
    if service.allow_process_exec:
        raise ControllerCapabilityUpgradeError(
            "refusing filesystem-only upgrade while VeraPort process execution is enabled"
        )

    config = ControllerConfig.load(controller_path)
    controller_private = _load_controller_private(config.controller_key)
    controller_principal = principal_id(
        controller_private.public_key(),
        "controller",
    )

    identity = load_identity(
        workstation_key_path=service.workstation_key,
        controller_trust_path=service.controller_trust,
    )
    current_ceiling = identity.capability_policy.get(controller_principal)
    if current_ceiling is None:
        raise ControllerCapabilityUpgradeError(
            "tunnel controller is not enrolled in VeraPort trust"
        )
    current_ceiling = frozenset(current_ceiling)
    if current_ceiling not in ALLOWED_SOURCE_CAPABILITIES:
        raise ControllerCapabilityUpgradeError(
            "tunnel controller capability ceiling must be exactly fs.read "
            "or exactly fs.read+fs.write before this bounded upgrade"
        )

    if config.requested_capabilities not in ALLOWED_SOURCE_CAPABILITIES:
        raise ControllerCapabilityUpgradeError(
            "controller config requested_capabilities must be exactly fs.read "
            "or exactly fs.read+fs.write"
        )
    if any(str(op).startswith("process.") for op in config.gateway_operations):
        raise ControllerCapabilityUpgradeError(
            "controller config unexpectedly contains process operations"
        )

    trust_doc, trust_raw = _strict_json(
        service.controller_trust,
        "VERAPORT_CONTROLLER_TRUST_V1",
    )
    controller_doc, controller_raw = _strict_json(
        controller_path,
        "VERAPORT_CONTROLLER_MCP_CONFIG_V1",
    )

    entries = trust_doc.get("controllers")
    if not isinstance(entries, list):
        raise ControllerCapabilityUpgradeError("controller trust list is invalid")
    matching = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("principal") == controller_principal
    ]
    if len(matching) != 1:
        raise ControllerCapabilityUpgradeError(
            "tunnel controller principal must have exactly one trust entry"
        )
    entry_caps = frozenset(str(v) for v in matching[0].get("capabilities", []))
    if entry_caps != current_ceiling:
        raise ControllerCapabilityUpgradeError(
            "controller trust JSON and loaded capability policy disagree"
        )

    trust_sha = _sha256_bytes(trust_raw)
    controller_sha = _sha256_bytes(controller_raw)
    trust_backup = service.controller_trust.with_name(
        service.controller_trust.name
        + ".pre-chatgpt-readwrite-"
        + trust_sha[:12]
        + ".bak"
    )
    controller_backup = controller_path.with_name(
        controller_path.name
        + ".pre-chatgpt-readwrite-"
        + controller_sha[:12]
        + ".bak"
    )

    lock_path = service.controller_trust.with_name(
        service.controller_trust.name + ".recovery.lock"
    )
    _acquire_recovery_lock(lock_path)
    trust_changed = False
    controller_changed = False
    try:
        if service.controller_trust.read_bytes() != trust_raw:
            raise ControllerCapabilityUpgradeError(
                "controller trust changed before upgrade; refusing blind retry"
            )
        if controller_path.read_bytes() != controller_raw:
            raise ControllerCapabilityUpgradeError(
                "controller config changed before upgrade; refusing blind retry"
            )

        _write_or_verify(trust_backup, trust_raw)
        _write_or_verify(controller_backup, controller_raw)

        if current_ceiling != TARGET_CAPABILITIES:
            for entry in entries:
                if (
                    isinstance(entry, dict)
                    and entry.get("principal") == controller_principal
                ):
                    entry["capabilities"] = sorted(TARGET_CAPABILITIES)
            trust_payload = (
                json.dumps(trust_doc, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            _atomic_replace(service.controller_trust, trust_payload)
            trust_changed = True

        desired_ops = list(controller_doc.get("gateway_operations", []))
        for operation in WRITE_OPERATIONS:
            if operation not in desired_ops:
                desired_ops.append(operation)
        controller_doc["requested_capabilities"] = sorted(TARGET_CAPABILITIES)
        controller_doc["gateway_operations"] = desired_ops
        desired_payload = (
            json.dumps(controller_doc, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        if desired_payload != controller_raw:
            _atomic_replace(controller_path, desired_payload)
            controller_changed = True

        after_identity = load_identity(
            workstation_key_path=service.workstation_key,
            controller_trust_path=service.controller_trust,
        )
        after_ceiling = after_identity.capability_policy.get(controller_principal)
        if frozenset(after_ceiling or ()) != TARGET_CAPABILITIES:
            raise ControllerCapabilityUpgradeError(
                "post-upgrade tunnel controller trust is not exactly fs.read+fs.write"
            )
        after_config = ControllerConfig.load(controller_path)
        if after_config.requested_capabilities != TARGET_CAPABILITIES:
            raise ControllerCapabilityUpgradeError(
                "post-upgrade controller requested_capabilities are not exact"
            )
        missing_ops = set(WRITE_OPERATIONS) - set(after_config.gateway_operations)
        if missing_ops:
            raise ControllerCapabilityUpgradeError(
                f"post-upgrade controller is missing write operations: {sorted(missing_ops)}"
            )
        if any(str(op).startswith("process.") for op in after_config.gateway_operations):
            raise ControllerCapabilityUpgradeError(
                "post-upgrade controller unexpectedly gained process operations"
            )

        return {
            "schema": "VERAMESH_LAPPY_TUNNEL_READWRITE_UPGRADE_V1",
            "controller_principal": controller_principal,
            "capabilities_before": sorted(current_ceiling),
            "capabilities_after": sorted(TARGET_CAPABILITIES),
            "write_operations": list(WRITE_OPERATIONS),
            "process_execution_enabled": False,
            "trust_changed": trust_changed,
            "controller_config_changed": controller_changed,
            "service_restart_required": trust_changed,
            "tunnel_restart_required": controller_changed,
            "trust_backup": str(trust_backup),
            "controller_config_backup": str(controller_backup),
            "old_controller_entries_preserved": True,
        }
    except Exception:
        # Roll back only files changed by this transaction. Preserve backups.
        try:
            if trust_changed:
                _atomic_replace(service.controller_trust, trust_raw)
            if controller_changed:
                _atomic_replace(controller_path, controller_raw)
        except Exception as rollback_exc:
            raise ControllerCapabilityUpgradeError(
                "read/write upgrade failed and rollback also failed; "
                f"manual reconciliation required: {rollback_exc}"
            ) from rollback_exc
        raise
    finally:
        _release_recovery_lock(lock_path)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Upgrade the existing ChatGPT Secure MCP tunnel controller from "
            "exact fs.read to exact fs.read+fs.write without enabling process execution."
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
    result = upgrade_tunnel_controller_readwrite(
        service_config_path=args.service_config,
        controller_config_path=args.controller_config,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
