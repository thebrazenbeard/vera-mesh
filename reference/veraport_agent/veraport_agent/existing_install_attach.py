from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .controller_config import ControllerConfig
from .hot_session import principal_id
from .identity_store import load_identity
from .service_config import WindowsServiceConfig
from .tunnel_runtime_service import (
    TunnelRuntimeServiceConfig,
    harden_service_materials as harden_tunnel_materials,
    validate_service_materials as validate_tunnel_materials,
)
from .windows_acl import (
    harden_controller_materials,
    harden_private_file,
    validate_controller_materials,
    validate_private_file,
)


class ExistingInstallAttachError(RuntimeError):
    code = "EXISTING_INSTALL_ATTACH_ERROR"


READ_ONLY_GATEWAY_OPERATIONS = (
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _write_or_verify(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file():
            raise ExistingInstallAttachError(
                f"existing path is not a file: {path}"
            )
        if path.read_bytes() != payload:
            raise ExistingInstallAttachError(
                f"existing file diverges from required attach material: {path}"
            )
        return "reused_exact"

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(path, flags, 0o600)
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
    return "created"


def _inside(root: Path, path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ExistingInstallAttachError(
            f"{label} must stay inside existing VeraMesh root {root}: {resolved}"
        ) from exc
    return resolved


def prepare_existing_install_tunnel_attach(
    *,
    service_config_path: str | Path,
    controller_private_key_path: str | Path,
    tunnel_client_path: str | Path,
    tunnel_id: str,
    runtime_api_key_file: str | Path,
    mcp_executable_path: str | Path,
    alias: str = "veramesh-lappy",
    controller_config_path: str | Path | None = None,
    tunnel_config_path: str | Path | None = None,
    material_dir: str | Path | None = None,
    profile_dir: str | Path | None = None,
    tunnel_state_dir: str | Path | None = None,
    manifest_path: str | Path | None = None,
    harden_windows_acl: bool = True,
) -> dict[str, Any]:
    service_path = Path(service_config_path).expanduser().resolve()
    if not service_path.is_file():
        raise ExistingInstallAttachError(
            f"existing VeraPort config missing: {service_path}"
        )
    root = service_path.parent.resolve()

    controller_path = _inside(
        root,
        Path(controller_config_path or (root / "controller.json")),
        "controller_config_path",
    )
    tunnel_path = _inside(
        root,
        Path(tunnel_config_path or (root / "tunnel-runtime.json")),
        "tunnel_config_path",
    )
    materials = _inside(
        root,
        Path(material_dir or (root / "tunnel-controller-v1")),
        "material_dir",
    )
    profiles = _inside(
        root,
        Path(profile_dir or (root / "tunnel-profiles")),
        "profile_dir",
    )
    state = _inside(
        root,
        Path(tunnel_state_dir or (root / "tunnel-state")),
        "tunnel_state_dir",
    )
    manifest = _inside(
        root,
        Path(manifest_path or (root / "existing-tunnel-attach-manifest.json")),
        "manifest_path",
    )

    service = WindowsServiceConfig.load(service_path)
    service.validate_runtime_files()
    try:
        bind = ipaddress.ip_address(service.bind_host)
    except ValueError as exc:
        raise ExistingInstallAttachError(
            "existing VeraPort bind_host is not a literal IP address"
        ) from exc
    if not bind.is_loopback:
        raise ExistingInstallAttachError(
            "existing-install tunnel attach requires VeraPort to remain loopback-only"
        )

    controller_key = Path(controller_private_key_path).expanduser().resolve()
    if not controller_key.is_file():
        raise ExistingInstallAttachError(
            f"existing controller private key missing: {controller_key}"
        )
    try:
        private = serialization.load_pem_private_key(
            controller_key.read_bytes(),
            password=None,
        )
    except Exception as exc:
        raise ExistingInstallAttachError(
            "cannot load existing controller private key"
        ) from exc
    if not isinstance(private, ec.EllipticCurvePrivateKey) or not isinstance(
        private.curve, ec.SECP256R1
    ):
        raise ExistingInstallAttachError(
            "existing controller private key must be EC P-256"
        )

    identity = load_identity(
        workstation_key_path=service.workstation_key,
        controller_trust_path=service.controller_trust,
    )
    controller_principal = principal_id(
        private.public_key(),
        "controller",
    )
    trust_ceiling = identity.capability_policy.get(controller_principal)
    if trust_ceiling is None:
        raise ExistingInstallAttachError(
            "selected controller private key is not enrolled in existing VeraPort trust"
        )
    if "fs.read" not in trust_ceiling:
        raise ExistingInstallAttachError(
            "selected enrolled controller is not trusted for fs.read"
        )

    workstation_public = identity.workstation_private_key.public_key()
    workstation_public_pem = workstation_public.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    workstation_principal = principal_id(
        workstation_public,
        "workstation",
    )

    tunnel_client = Path(tunnel_client_path).expanduser().resolve()
    runtime_key = Path(runtime_api_key_file).expanduser().resolve()
    mcp_executable = Path(mcp_executable_path).expanduser().resolve()
    for path, label in (
        (tunnel_client, "tunnel-client"),
        (runtime_key, "runtime API key file"),
        (mcp_executable, "veraport-mcp-stdio"),
    ):
        if not path.is_file():
            raise ExistingInstallAttachError(f"{label} missing: {path}")

    materials.mkdir(parents=True, exist_ok=True)
    profiles.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)

    workstation_public_path = materials / "workstation-public.pem"
    tls_ca_path = materials / "tls-ca.pem"
    actions: dict[str, str] = {}
    actions[str(workstation_public_path)] = _write_or_verify(
        workstation_public_path,
        workstation_public_pem,
    )
    actions[str(tls_ca_path)] = _write_or_verify(
        tls_ca_path,
        service.tls_cert.read_bytes(),
    )

    controller_value = {
        "schema": "VERAPORT_CONTROLLER_MCP_CONFIG_V1",
        "controller_key": str(controller_key),
        "tls_ca": str(tls_ca_path),
        "workstation_public_key": str(workstation_public_path),
        "requested_capabilities": ["fs.read"],
        "gateway_operations": list(READ_ONLY_GATEWAY_OPERATIONS),
        "endpoints": [{
            "endpoint_id": "lappy-existing-loopback",
            "mode": "DIRECT_STREAM",
            "host": service.bind_host,
            "port": service.bind_port,
            "server_hostname": "localhost",
            "durable_idempotency": True,
        }],
        "max_path_age_ms": 5_000,
        "connect_timeout_s": 5.0,
        "request_timeout_s": 5.0,
    }
    controller_payload = (
        json.dumps(controller_value, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    actions[str(controller_path)] = _write_or_verify(
        controller_path,
        controller_payload,
    )
    controller = ControllerConfig.load(controller_path)

    tunnel_value = {
        "schema": "VERAMESH_TUNNEL_RUNTIME_SERVICE_V1",
        "tunnel_client": str(tunnel_client),
        "tunnel_client_sha256": _sha256(tunnel_client),
        "alias": alias,
        "tunnel_id": str(tunnel_id).strip(),
        "runtime_api_key_file": str(runtime_key),
        "controller_config": str(controller_path),
        "mcp_executable": str(mcp_executable),
        "mcp_executable_sha256": _sha256(mcp_executable),
        "profile_dir": str(profiles),
        "state_dir": str(state),
        "status_interval_s": 5.0,
        "command_timeout_s": 60.0,
    }
    tunnel_payload = (
        json.dumps(tunnel_value, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    actions[str(tunnel_path)] = _write_or_verify(
        tunnel_path,
        tunnel_payload,
    )
    tunnel = TunnelRuntimeServiceConfig.load(tunnel_path)
    tunnel.validate_runtime_files()

    if os.name == "nt" and harden_windows_acl:
        harden_controller_materials(controller_path, controller)
        harden_tunnel_materials(tunnel_path, tunnel)
        validate_controller_materials(controller_path, controller)
        validate_tunnel_materials(tunnel_path, tunnel)

    receipt = {
        "schema": "VERAMESH_EXISTING_LAPPY_TUNNEL_ATTACH_V1",
        "mode": "PRESERVE_EXISTING_VERAPORT_IDENTITY",
        "existing_veraport": {
            "service_config": str(service_path),
            "service_config_sha256": _sha256(service_path),
            "bind_host": service.bind_host,
            "bind_port": service.bind_port,
            "allowed_roots": [str(path) for path in service.allowed_roots],
            "workstation_principal": workstation_principal,
            "controller_principal": controller_principal,
            "controller_trust_ceiling": sorted(trust_ceiling),
            "process_execution_enabled": bool(service.allow_process_exec),
        },
        "tunnel_controller": {
            "controller_config": str(controller_path),
            "requested_capabilities": ["fs.read"],
            "gateway_operations": list(READ_ONLY_GATEWAY_OPERATIONS),
            "controller_private_key_reused_in_place": str(controller_key),
            "workstation_public_pin": str(workstation_public_path),
            "tls_ca": str(tls_ca_path),
        },
        "tunnel_runtime": {
            "config": str(tunnel_path),
            "alias": tunnel.alias,
            "tunnel_id": tunnel.tunnel_id,
            "tunnel_client": str(tunnel.tunnel_client),
            "tunnel_client_sha256": tunnel.tunnel_client_sha256,
            "mcp_executable": str(tunnel.mcp_executable),
            "mcp_executable_sha256": tunnel.mcp_executable_sha256,
        },
        "write_actions": actions,
        "effects": {
            "existing_veraport_service_modified": False,
            "existing_veraport_identity_rotated": False,
            "existing_veraport_state_rewritten": False,
            "existing_allowed_roots_changed": False,
            "existing_process_policy_changed": False,
            "tunnel_service_installed": False,
            "tunnel_service_started": False,
        },
        "claim_ceiling": [
            "Prepared and cross-bound tunnel/controller material around the existing VeraPort identity.",
            "Does not itself install or start the VeraMeshTunnelRuntime Windows service.",
            "Does not prove live VeraPort authentication until veraport-doctor --live succeeds.",
            "Does not prove Secure MCP Tunnel health until tunnel status succeeds.",
            "Does not prove ChatGPT registration or a ChatGPT-originated tool effect.",
        ],
    }
    manifest_payload = (
        json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    actions[str(manifest)] = _write_or_verify(manifest, manifest_payload)
    if os.name == "nt" and harden_windows_acl:
        harden_private_file(manifest)
        validate_private_file(manifest)

    # Preserve the observed write actions, including the manifest action itself,
    # in the returned value without rewriting the already durable manifest.
    receipt["write_actions"] = actions
    return receipt


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Prepare Secure MCP Tunnel controller/runtime material around an "
            "existing VeraPort installation without replacing its service, "
            "identity, trust list, state database, roots, or process policy."
        )
    )
    parser.add_argument(
        "--service-config",
        default=r"C:\ProgramData\VeraMesh\veraport.json",
    )
    parser.add_argument("--controller-private-key", required=True)
    parser.add_argument("--tunnel-client", required=True)
    parser.add_argument("--tunnel-id", required=True)
    parser.add_argument("--runtime-api-key-file", required=True)
    parser.add_argument("--mcp-executable", required=True)
    parser.add_argument("--alias", default="veramesh-lappy")
    args = parser.parse_args()

    result = prepare_existing_install_tunnel_attach(
        service_config_path=args.service_config,
        controller_private_key_path=args.controller_private_key,
        tunnel_client_path=args.tunnel_client,
        tunnel_id=args.tunnel_id,
        runtime_api_key_file=args.runtime_api_key_file,
        mcp_executable_path=args.mcp_executable,
        alias=args.alias,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
