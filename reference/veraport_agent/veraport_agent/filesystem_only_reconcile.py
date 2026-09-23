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
from .service_config import WindowsServiceConfig
from .windows_acl import harden_private_file


TARGET_CAPABILITIES = frozenset({"fs.read", "fs.write"})
READ_OPERATIONS = (
    "lane.list",
    "lane.open",
    "lane.renew",
    "lane.close",
    "fs.read_text",
    "fs.read_bytes",
    "fs.stat",
    "fs.list_dir",
    "fs.search",
    "fs.search_content",
)
WRITE_OPERATIONS = (
    "fs.write_text",
    "fs.append_text",
    "fs.mkdir",
    "fs.move",
    "fs.replace_text",
)
TARGET_OPERATIONS = READ_OPERATIONS + WRITE_OPERATIONS
PROCESS_CAPABILITIES = frozenset({
    "process.exec",
    "process.inspect",
    "process.interact",
    "process.control",
})


class FilesystemOnlyReconcileError(RuntimeError):
    code = "FILESYSTEM_ONLY_RECONCILE_ERROR"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_json(path: Path, expected_schema: str | None = None) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise FilesystemOnlyReconcileError(
            f"cannot parse strict UTF-8 JSON: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise FilesystemOnlyReconcileError(f"JSON root is not an object: {path}")
    if expected_schema is not None and value.get("schema") != expected_schema:
        raise FilesystemOnlyReconcileError(
            f"wrong schema in {path}: expected {expected_schema}"
        )
    return value, raw


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise FilesystemOnlyReconcileError(
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


def _backup(path: Path, raw: bytes, label: str) -> Path:
    backup = path.with_name(
        path.name + f".pre-{label}-" + _sha256(raw)[:12] + ".bak"
    )
    if backup.exists():
        if not backup.is_file() or backup.read_bytes() != raw:
            raise FilesystemOnlyReconcileError(
                f"existing backup diverges: {backup}"
            )
        return backup
    _write_new(backup, raw)
    if os.name == "nt":
        harden_private_file(backup)
    return backup


def _controller_principal(config: ControllerConfig) -> str:
    try:
        private = serialization.load_pem_private_key(
            config.controller_key.read_bytes(),
            password=None,
        )
    except Exception as exc:
        raise FilesystemOnlyReconcileError(
            "cannot load tunnel controller private key"
        ) from exc
    if not isinstance(private, ec.EllipticCurvePrivateKey) or not isinstance(
        private.curve, ec.SECP256R1
    ):
        raise FilesystemOnlyReconcileError(
            "tunnel controller private key must be EC P-256"
        )
    return principal_id(private.public_key(), "controller")


def reconcile_filesystem_only(
    *,
    service_config_path: str | Path,
    controller_config_path: str | Path,
) -> dict[str, Any]:
    service_path = Path(service_config_path).expanduser().resolve()
    controller_path = Path(controller_config_path).expanduser().resolve()

    # Load with the canonical VeraPort parser first. A currently enabled process
    # policy is expected input for this bounded reconciliation, not a refusal.
    service_before = WindowsServiceConfig.load(service_path)
    service_before.validate_runtime_files()
    config_before = ControllerConfig.load(controller_path)
    controller_principal = _controller_principal(config_before)

    service_doc, service_raw = _strict_json(service_path)
    controller_doc, controller_raw = _strict_json(
        controller_path, "VERAPORT_CONTROLLER_MCP_CONFIG_V1"
    )
    trust_path = service_before.controller_trust
    trust_doc, trust_raw = _strict_json(
        trust_path, "VERAPORT_CONTROLLER_TRUST_V1"
    )

    allowed_roots_before = tuple(str(path) for path in service_before.allowed_roots)
    identity_fields_before = {
        key: service_doc.get(key)
        for key in ("tls_cert", "tls_key", "workstation_key", "controller_trust")
    }
    bind_before = (service_doc.get("bind_host"), service_doc.get("bind_port"))

    entries = trust_doc.get("controllers")
    if not isinstance(entries, list):
        raise FilesystemOnlyReconcileError("controller trust list is invalid")
    matching = [
        entry for entry in entries
        if isinstance(entry, dict) and entry.get("principal") == controller_principal
    ]
    if len(matching) != 1:
        raise FilesystemOnlyReconcileError(
            "tunnel controller principal must have exactly one trust entry"
        )

    caps_before = frozenset(str(v) for v in matching[0].get("capabilities", []))
    requested_before = frozenset(config_before.requested_capabilities)
    process_ops_before = tuple(
        str(op) for op in config_before.gateway_operations
        if str(op).startswith("process.")
    )

    lock_path = trust_path.with_name(trust_path.name + ".recovery.lock")
    _acquire_recovery_lock(lock_path)
    service_changed = False
    trust_changed = False
    controller_changed = False
    try:
        if service_path.read_bytes() != service_raw:
            raise FilesystemOnlyReconcileError(
                "service config changed before reconcile; refusing blind write"
            )
        if trust_path.read_bytes() != trust_raw:
            raise FilesystemOnlyReconcileError(
                "controller trust changed before reconcile; refusing blind write"
            )
        if controller_path.read_bytes() != controller_raw:
            raise FilesystemOnlyReconcileError(
                "controller config changed before reconcile; refusing blind write"
            )

        service_backup = _backup(service_path, service_raw, "filesystem-only-reconcile")
        trust_backup = _backup(trust_path, trust_raw, "filesystem-only-reconcile")
        controller_backup = _backup(
            controller_path, controller_raw, "filesystem-only-reconcile"
        )

        service_doc["allow_process_exec"] = False
        service_payload = (
            json.dumps(service_doc, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        if service_payload != service_raw:
            _atomic_replace(service_path, service_payload)
            service_changed = True

        matching[0]["capabilities"] = sorted(TARGET_CAPABILITIES)
        trust_payload = (
            json.dumps(trust_doc, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        if trust_payload != trust_raw:
            _atomic_replace(trust_path, trust_payload)
            trust_changed = True

        controller_doc["requested_capabilities"] = sorted(TARGET_CAPABILITIES)
        controller_doc["gateway_operations"] = list(TARGET_OPERATIONS)
        controller_payload = (
            json.dumps(controller_doc, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        if controller_payload != controller_raw:
            _atomic_replace(controller_path, controller_payload)
            controller_changed = True

        service_after = WindowsServiceConfig.load(service_path)
        service_after.validate_runtime_files()
        config_after = ControllerConfig.load(controller_path)

        if service_after.allow_process_exec:
            raise FilesystemOnlyReconcileError(
                "post-reconcile process execution is still enabled"
            )
        if tuple(str(path) for path in service_after.allowed_roots) != allowed_roots_before:
            raise FilesystemOnlyReconcileError(
                "allowed roots changed during reconcile"
            )
        if (
            service_after.bind_host,
            service_after.bind_port,
        ) != bind_before:
            raise FilesystemOnlyReconcileError(
                "VeraPort bind changed during reconcile"
            )
        identity_fields_after = {
            key: json.loads(service_path.read_text(encoding="utf-8")).get(key)
            for key in identity_fields_before
        }
        if identity_fields_after != identity_fields_before:
            raise FilesystemOnlyReconcileError(
                "identity/trust path bindings changed during reconcile"
            )
        if config_after.requested_capabilities != TARGET_CAPABILITIES:
            raise FilesystemOnlyReconcileError(
                "post-reconcile requested capabilities are not exact fs.read+fs.write"
            )
        if tuple(config_after.gateway_operations) != TARGET_OPERATIONS:
            raise FilesystemOnlyReconcileError(
                "post-reconcile gateway operations are not the exact filesystem-only set"
            )

        trust_after, _ = _strict_json(
            trust_path, "VERAPORT_CONTROLLER_TRUST_V1"
        )
        matching_after = [
            entry for entry in trust_after.get("controllers", [])
            if isinstance(entry, dict) and entry.get("principal") == controller_principal
        ]
        if len(matching_after) != 1:
            raise FilesystemOnlyReconcileError(
                "post-reconcile controller trust entry count changed"
            )
        if frozenset(
            str(v) for v in matching_after[0].get("capabilities", [])
        ) != TARGET_CAPABILITIES:
            raise FilesystemOnlyReconcileError(
                "post-reconcile trust ceiling is not exact fs.read+fs.write"
            )

        return {
            "schema": "VERAMESH_FILESYSTEM_ONLY_RECONCILE_V1",
            "controller_principal": controller_principal,
            "process_enabled_before": bool(service_before.allow_process_exec),
            "process_enabled_after": False,
            "controller_capabilities_before": sorted(caps_before),
            "controller_requested_before": sorted(requested_before),
            "controller_process_operations_before": list(process_ops_before),
            "controller_capabilities_after": sorted(TARGET_CAPABILITIES),
            "controller_requested_after": sorted(TARGET_CAPABILITIES),
            "gateway_operations_after": list(TARGET_OPERATIONS),
            "service_changed": service_changed,
            "trust_changed": trust_changed,
            "controller_changed": controller_changed,
            "service_restart_required": bool(service_changed or trust_changed),
            "tunnel_restart_required": bool(controller_changed or service_changed or trust_changed),
            "allowed_roots_unchanged": True,
            "identity_bindings_unchanged": True,
            "bind_unchanged": True,
            "backups": {
                "service": str(service_backup),
                "trust": str(trust_backup),
                "controller": str(controller_backup),
            },
        }
    except Exception:
        rollback_errors: list[str] = []
        for changed, path, raw in (
            (controller_changed, controller_path, controller_raw),
            (trust_changed, trust_path, trust_raw),
            (service_changed, service_path, service_raw),
        ):
            if not changed:
                continue
            try:
                _atomic_replace(path, raw)
            except Exception as exc:
                rollback_errors.append(f"{path}: {exc}")
        if rollback_errors:
            raise FilesystemOnlyReconcileError(
                "filesystem-only reconcile failed and rollback was incomplete: "
                + "; ".join(rollback_errors)
            )
        raise
    finally:
        _release_recovery_lock(lock_path)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Reconcile an existing VeraPort tunnel to exact filesystem read/write "
            "authority with process execution disabled."
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
    result = reconcile_filesystem_only(
        service_config_path=args.service_config,
        controller_config_path=args.controller_config,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
