from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
from typing import Any, Iterable

from .controller_config import ControllerConfig
from .provision import (
    FILESYSTEM_CAPABILITIES,
    PROCESS_CAPABILITIES,
    provision_local_pair,
)
from .service_config import WindowsServiceConfig
from .windows_acl import (
    harden_service_materials,
    validate_service_materials,
)


class WorkstationPreparationError(RuntimeError):
    code = "WORKSTATION_PREPARATION_ERROR"


FILESYSTEM_OPERATIONS = (
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


def _write_new_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise WorkstationPreparationError(
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_workstation_service(
    service_root: str | Path,
    controller_export_dir: str | Path,
    *,
    allowed_roots: Iterable[str | Path],
    bind_host: str = "127.0.0.1",
    bind_port: int = 17444,
    allow_non_loopback_listener: bool = False,
    enable_process: bool = False,
    endpoint_id: str = "workstation-direct",
    harden_windows_acl: bool = True,
) -> dict[str, Any]:
    root = Path(service_root).expanduser().resolve()
    export = Path(controller_export_dir).expanduser().resolve()
    if root == export:
        raise WorkstationPreparationError(
            "controller export directory must differ from service root"
        )
    if root.exists() and any(root.iterdir()):
        raise WorkstationPreparationError(
            f"service root must be absent or empty: {root}"
        )
    if export.exists() and any(export.iterdir()):
        raise WorkstationPreparationError(
            f"controller export directory must be absent or empty: {export}"
        )

    roots = tuple(Path(item).expanduser().resolve() for item in allowed_roots)
    if not roots:
        raise WorkstationPreparationError(
            "at least one allowed root is required"
        )
    missing = [path for path in roots if not path.is_dir()]
    if missing:
        raise WorkstationPreparationError(
            "allowed roots must already exist: "
            + ", ".join(str(path) for path in missing)
        )

    try:
        address = ipaddress.ip_address(bind_host)
    except ValueError as exc:
        raise WorkstationPreparationError(
            "bind_host must be a literal IP address"
        ) from exc
    if address.is_unspecified or address.is_multicast:
        raise WorkstationPreparationError(
            "bind_host must be a concrete unicast address"
        )
    if not address.is_loopback and not allow_non_loopback_listener:
        raise WorkstationPreparationError(
            "non-loopback bind requires explicit allow_non_loopback_listener"
        )
    if not 1 <= int(bind_port) <= 65535:
        raise WorkstationPreparationError(
            "bind_port must be in 1..65535"
        )
    if not endpoint_id.strip():
        raise WorkstationPreparationError("endpoint_id is required")

    root.mkdir(parents=True, exist_ok=True)
    export.mkdir(parents=True, exist_ok=True)
    identity = root / "identity"
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)

    capabilities = set(FILESYSTEM_CAPABILITIES)
    operations = list(FILESYSTEM_OPERATIONS)
    if enable_process:
        capabilities.update(PROCESS_CAPABILITIES)
        operations.extend(PROCESS_OPERATIONS)

    identity_manifest = provision_local_pair(
        identity,
        capabilities=capabilities,
        harden_windows_acl=harden_windows_acl,
    )

    export_names = (
        "controller-key.pem",
        "controller-public.pem",
        "workstation-public.pem",
        "tls-ca.pem",
    )
    for name in export_names:
        source = identity / name
        if not source.is_file():
            raise WorkstationPreparationError(
                f"provisioned controller export material missing: {source}"
            )
        target = export / name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        fd = os.open(target, flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(source.read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            raise

    service_config_path = root / "veraport.json"
    service_config_value = {
        "bind_host": str(address),
        "bind_port": int(bind_port),
        "allowed_roots": [str(path) for path in roots],
        "state_db": str(state / "veraport.sqlite3"),
        "tls_cert": str(identity / "tls-cert.pem"),
        "tls_key": str(identity / "tls-key.pem"),
        "workstation_key": str(identity / "workstation-key.pem"),
        "controller_trust": str(identity / "controller-trust.json"),
        "allow_process_exec": bool(enable_process),
        "allow_non_loopback_listener": bool(
            allow_non_loopback_listener
        ),
        "max_lanes": 32,
        "max_inflight": 64,
        "max_read_bytes": 1_048_576,
    }
    _write_new_json(service_config_path, service_config_value)

    controller_config_path = export / "controller.json"
    controller_config_value = {
        "schema": "VERAPORT_CONTROLLER_MCP_CONFIG_V1",
        "controller_key": "controller-key.pem",
        "tls_ca": "tls-ca.pem",
        "workstation_public_key": "workstation-public.pem",
        "requested_capabilities": sorted(capabilities),
        "gateway_operations": operations,
        "endpoints": [{
            "endpoint_id": endpoint_id,
            "mode": "DIRECT_STREAM",
            "host": str(address),
            "port": int(bind_port),
            "server_hostname": "localhost",
            "durable_idempotency": True,
        }],
        "max_path_age_ms": 5_000,
        "connect_timeout_s": 5.0,
        "request_timeout_s": 5.0,
    }
    _write_new_json(controller_config_path, controller_config_value)

    service_cfg = WindowsServiceConfig.load(service_config_path)
    service_cfg.validate_runtime_files()
    ControllerConfig.load(controller_config_path)

    if os.name == "nt" and harden_windows_acl:
        harden_service_materials(service_config_path, service_cfg)
        validate_service_materials(service_config_path, service_cfg)

    # The remote controller private key must not remain beside the persistent
    # workstation service identity after the export has been verified.
    service_controller_private = identity / "controller-key.pem"
    if _sha256(service_controller_private) != _sha256(
        export / "controller-key.pem"
    ):
        raise WorkstationPreparationError(
            "controller export private-key digest mismatch"
        )
    service_controller_private.unlink()
    service_controller_public = identity / "controller-public.pem"
    if service_controller_public.exists():
        service_controller_public.unlink()

    manifest = {
        "schema": "VERAPORT_WORKSTATION_PREPARATION_MANIFEST_V1",
        "authority": {
            "process_enabled": bool(enable_process),
            "capabilities": sorted(capabilities),
            "gateway_operations": operations,
            "allowed_roots": [str(path) for path in roots],
            "bind_host": str(address),
            "bind_port": int(bind_port),
            "non_loopback_listener": not address.is_loopback,
        },
        "identity": {
            "workstation_principal": identity_manifest[
                "workstation_principal"
            ],
            "workstation_key_id": identity_manifest[
                "workstation_key_id"
            ],
            "controller_principal": identity_manifest[
                "controller_principal"
            ],
            "controller_key_id": identity_manifest[
                "controller_key_id"
            ],
        },
        "paths": {
            "service_root": str(root),
            "service_config": str(service_config_path),
            "service_identity": str(identity),
            "controller_export": str(export),
            "controller_config": str(controller_config_path),
        },
        "sha256": {
            "service_config": _sha256(service_config_path),
            "controller_config": _sha256(controller_config_path),
            "controller_private_export": _sha256(
                export / "controller-key.pem"
            ),
            "workstation_public_export": _sha256(
                export / "workstation-public.pem"
            ),
            "tls_ca_export": _sha256(export / "tls-ca.pem"),
        },
        "separation": {
            "controller_private_key_present_in_service_identity": (
                service_controller_private.exists()
            ),
            "controller_private_key_exported": True,
            "controller_export_requires_secure_transfer": True,
        },
        "effects": {
            "windows_service_installed": False,
            "windows_service_started": False,
            "firewall_changed": False,
            "controller_export_transferred": False,
        },
    }
    manifest_path = root / "workstation-preparation-manifest.json"
    _write_new_json(manifest_path, manifest)
    return manifest
