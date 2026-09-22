from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

from .controller_config import ControllerConfig
from .provision import (
    FILESYSTEM_CAPABILITIES,
    PROCESS_CAPABILITIES,
    ProvisionError,
    provision_local_pair,
)
from .service_config import WindowsServiceConfig
from .tunnel_runtime_service import (
    TunnelRuntimeServiceConfig,
    harden_service_materials as harden_tunnel_materials,
    validate_service_materials as validate_tunnel_materials,
)
from .windows_acl import (
    harden_service_materials as harden_veraport_materials,
    validate_service_materials as validate_veraport_materials,
)


class BootstrapError(RuntimeError):
    code = "BOOTSTRAP_ERROR"


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
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise BootstrapError(f"refusing to overwrite existing file: {path}") from exc
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


def prepare_local_bootstrap(
    root: str | Path,
    *,
    allowed_roots: Iterable[str | Path],
    tunnel_client: str | Path,
    tunnel_id: str,
    runtime_api_key_file: str | Path,
    mcp_executable: str | Path,
    enable_process: bool = False,
    bind_port: int = 17444,
    alias: str = "veramesh-lappy",
    harden_windows_acl: bool = True,
) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    roots = tuple(Path(item).expanduser().resolve() for item in allowed_roots)
    if not roots:
        raise BootstrapError("at least one allowed root is required")
    missing_roots = [path for path in roots if not path.is_dir()]
    if missing_roots:
        raise BootstrapError(
            "allowed roots must already exist: "
            + ", ".join(str(path) for path in missing_roots)
        )

    tunnel_client = Path(tunnel_client).expanduser().resolve()
    mcp_executable = Path(mcp_executable).expanduser().resolve()
    runtime_api_key_file = Path(runtime_api_key_file).expanduser().resolve()
    if not tunnel_client.is_file():
        raise BootstrapError(f"tunnel-client executable missing: {tunnel_client}")
    if not mcp_executable.is_file():
        raise BootstrapError(
            f"VeraMesh stdio MCP executable missing: {mcp_executable}"
        )
    if not runtime_api_key_file.is_file():
        raise BootstrapError(
            f"runtime API key file missing: {runtime_api_key_file}"
        )

    identity_dir = root / "identity"
    state_dir = root / "state"
    tunnel_profiles = root / "tunnel-profiles"
    tunnel_state = root / "tunnel-state"
    for directory in (root, state_dir, tunnel_profiles, tunnel_state):
        directory.mkdir(parents=True, exist_ok=True)

    capabilities = set(FILESYSTEM_CAPABILITIES)
    operations = list(FILESYSTEM_OPERATIONS)
    if enable_process:
        capabilities.update(PROCESS_CAPABILITIES)
        operations.extend(PROCESS_OPERATIONS)

    identity_manifest = provision_local_pair(
        identity_dir,
        capabilities=capabilities,
    )

    veraport_path = root / "veraport.json"
    controller_path = root / "controller.json"
    tunnel_path = root / "tunnel-runtime.json"

    veraport = {
        "bind_host": "127.0.0.1",
        "bind_port": int(bind_port),
        "allowed_roots": [str(path) for path in roots],
        "state_db": str(state_dir / "veraport.sqlite3"),
        "tls_cert": str(identity_dir / "tls-cert.pem"),
        "tls_key": str(identity_dir / "tls-key.pem"),
        "workstation_key": str(identity_dir / "workstation-key.pem"),
        "controller_trust": str(identity_dir / "controller-trust.json"),
        "allow_process_exec": bool(enable_process),
        "allow_non_loopback_listener": False,
        "max_lanes": 32,
        "max_inflight": 64,
        "max_read_bytes": 1_048_576,
    }
    controller = {
        "schema": "VERAPORT_CONTROLLER_MCP_CONFIG_V1",
        "controller_key": str(identity_dir / "controller-key.pem"),
        "tls_ca": str(identity_dir / "tls-ca.pem"),
        "workstation_public_key": str(identity_dir / "workstation-public.pem"),
        "requested_capabilities": sorted(capabilities),
        "gateway_operations": operations,
        "endpoints": [{
            "endpoint_id": "lappy-direct",
            "mode": "DIRECT_STREAM",
            "host": "127.0.0.1",
            "port": int(bind_port),
            "server_hostname": "localhost",
            "durable_idempotency": True,
        }],
        "max_path_age_ms": 5_000,
        "connect_timeout_s": 5.0,
        "request_timeout_s": 5.0,
    }
    tunnel = {
        "schema": "VERAMESH_TUNNEL_RUNTIME_SERVICE_V1",
        "tunnel_client": str(tunnel_client),
        "tunnel_client_sha256": _sha256(tunnel_client),
        "alias": alias,
        "tunnel_id": tunnel_id,
        "runtime_api_key_file": str(runtime_api_key_file),
        "controller_config": str(controller_path),
        "mcp_executable": str(mcp_executable),
        "mcp_executable_sha256": _sha256(mcp_executable),
        "profile_dir": str(tunnel_profiles),
        "state_dir": str(tunnel_state),
        "status_interval_s": 5.0,
        "command_timeout_s": 60.0,
    }

    created: list[Path] = []
    try:
        for path, value in (
            (veraport_path, veraport),
            (controller_path, controller),
            (tunnel_path, tunnel),
        ):
            _write_new_json(path, value)
            created.append(path)

        service_cfg = WindowsServiceConfig.load(veraport_path)
        service_cfg.validate_runtime_files()
        controller_cfg = ControllerConfig.load(controller_path)
        tunnel_cfg = TunnelRuntimeServiceConfig.load(tunnel_path)
        tunnel_cfg.validate_runtime_files()

        if os.name == "nt" and harden_windows_acl:
            harden_veraport_materials(veraport_path, service_cfg)
            harden_tunnel_materials(tunnel_path, tunnel_cfg)
            validate_veraport_materials(veraport_path, service_cfg)
            validate_tunnel_materials(tunnel_path, tunnel_cfg)

        manifest = {
            "schema": "VERAMESH_LOCAL_BOOTSTRAP_MANIFEST_V1",
            "authority": {
                "process_enabled": bool(enable_process),
                "capabilities": sorted(capabilities),
                "gateway_operations": operations,
                "allowed_roots": [str(path) for path in roots],
            },
            "identity": identity_manifest,
            "paths": {
                "root": str(root),
                "veraport_config": str(veraport_path),
                "controller_config": str(controller_path),
                "tunnel_config": str(tunnel_path),
                "identity_dir": str(identity_dir),
                "state_dir": str(state_dir),
                "tunnel_profiles": str(tunnel_profiles),
                "tunnel_state": str(tunnel_state),
            },
            "sha256": {
                "veraport_config": _sha256(veraport_path),
                "controller_config": _sha256(controller_path),
                "tunnel_config": _sha256(tunnel_path),
            },
            "effects": {
                "services_installed": False,
                "services_started": False,
                "tunnel_created": False,
                "chatgpt_connector_registered": False,
            },
        }
        manifest_path = root / "bootstrap-manifest.json"
        _write_new_json(manifest_path, manifest)
        if os.name == "nt" and harden_windows_acl:
            from .windows_acl import harden_private_file, validate_private_file

            harden_private_file(manifest_path)
            validate_private_file(manifest_path)
        return manifest
    except Exception:
        # Do not try to erase generated identity material on failure: partial
        # provisioning is evidence and may contain secrets. Fail closed and
        # require explicit operator reconciliation instead of destructive cleanup.
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare local VeraMesh RDC-replacement configs and identity material. "
            "This does not install/start services or create a tunnel."
        )
    )
    parser.add_argument("--root", default=r"C:\ProgramData\VeraMesh")
    parser.add_argument("--allowed-root", action="append", required=True)
    parser.add_argument("--tunnel-client", required=True)
    parser.add_argument("--tunnel-id", required=True)
    parser.add_argument("--runtime-api-key-file", required=True)
    parser.add_argument("--mcp-executable", required=True)
    parser.add_argument("--enable-process", action="store_true")
    parser.add_argument("--bind-port", type=int, default=17444)
    parser.add_argument("--alias", default="veramesh-lappy")
    args = parser.parse_args()

    manifest = prepare_local_bootstrap(
        args.root,
        allowed_roots=args.allowed_root,
        tunnel_client=args.tunnel_client,
        tunnel_id=args.tunnel_id,
        runtime_api_key_file=args.runtime_api_key_file,
        mcp_executable=args.mcp_executable,
        enable_process=args.enable_process,
        bind_port=args.bind_port,
        alias=args.alias,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
