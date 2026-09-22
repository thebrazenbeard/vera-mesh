from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .controller_config import ControllerConfig
from .gateway import ALL_OPERATIONS
from .hot_session import key_id, principal_id
from .service_config import WindowsServiceConfig
from .windows_acl import harden_controller_materials, harden_service_materials


class ProvisioningError(RuntimeError):
    code = "PROVISIONING_ERROR"


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

FILESYSTEM_CAPABILITIES = ("fs.read", "fs.write")
PROCESS_CAPABILITIES = (
    "process.exec",
    "process.inspect",
    "process.interact",
    "process.control",
)


@dataclass(frozen=True)
class ProvisionedBundle:
    root: Path
    service_config: Path
    controller_config: Path
    workstation_principal: str
    controller_principal: str
    process_enabled: bool
    edge_enabled: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "service_config": str(self.service_config),
            "controller_config": str(self.controller_config),
            "workstation_principal": self.workstation_principal,
            "controller_principal": self.controller_principal,
            "process_enabled": self.process_enabled,
            "edge_enabled": self.edge_enabled,
        }


def _private_key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _public_key_pem(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _self_signed_localhost_certificate(
    key: ec.EllipticCurvePrivateKey,
) -> bytes:
    now = dt.datetime.now(dt.timezone.utc)
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.IPAddress(ipaddress.ip_address("::1")),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM)


def _write_new(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, mode)
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


def _write_json_new(path: Path, value: dict[str, Any]) -> None:
    _write_new(
        path,
        (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def _bundle_paths(root: Path) -> dict[str, Path]:
    return {
        "service_config": root / "veraport.json",
        "controller_config": root / "controller.json",
        "state_db": root / "state" / "agent.sqlite3",
        "tls_cert": root / "tls" / "localhost-cert.pem",
        "tls_key": root / "tls" / "localhost-key.pem",
        "workstation_key": root / "identity" / "workstation-key.pem",
        "workstation_public": root / "identity" / "workstation-public.pem",
        "controller_key": root / "identity" / "controller-key.pem",
        "controller_trust": root / "trust" / "controllers.json",
        "manifest": root / "bundle-manifest.json",
    }


def _preflight_empty(paths: dict[str, Path]) -> None:
    collisions = [
        path
        for name, path in paths.items()
        if name != "state_db" and path.exists()
    ]
    if collisions:
        raise ProvisioningError(
            "refusing to overwrite existing VeraMesh material: "
            + ", ".join(str(path) for path in sorted(collisions))
        )


def provision_bundle(
    *,
    output_dir: str | Path,
    allowed_roots: list[str | Path],
    allow_process_exec: bool = False,
    agent_port: int = 17444,
    edge_host: str | None = None,
    edge_port: int = 17445,
    apply_windows_acl: bool = True,
) -> ProvisionedBundle:
    if not allowed_roots:
        raise ProvisioningError("at least one allowed root is required")
    if type(allow_process_exec) is not bool:
        raise ProvisioningError("allow_process_exec must be bool")
    if type(apply_windows_acl) is not bool:
        raise ProvisioningError("apply_windows_acl must be bool")
    if not 1 <= int(agent_port) <= 65535:
        raise ProvisioningError("agent_port must be in 1..65535")
    if not 1 <= int(edge_port) <= 65535:
        raise ProvisioningError("edge_port must be in 1..65535")

    root = Path(output_dir).expanduser().resolve()
    resolved_roots = tuple(
        Path(value).expanduser().resolve()
        for value in allowed_roots
    )
    missing_roots = [path for path in resolved_roots if not path.is_dir()]
    if missing_roots:
        raise ProvisioningError(
            "allowed roots must already exist: "
            + ", ".join(str(path) for path in missing_roots)
        )
    if len(set(resolved_roots)) != len(resolved_roots):
        raise ProvisioningError("allowed roots contain duplicates")

    normalized_edge_host: str | None = None
    if edge_host is not None:
        normalized_edge_host = edge_host.strip()
        if not normalized_edge_host:
            raise ProvisioningError("edge_host must be non-empty when supplied")

    paths = _bundle_paths(root)
    _preflight_empty(paths)
    root.mkdir(parents=True, exist_ok=True)

    workstation_key = ec.generate_private_key(ec.SECP256R1())
    controller_key = ec.generate_private_key(ec.SECP256R1())
    tls_key = ec.generate_private_key(ec.SECP256R1())

    workstation_public = workstation_key.public_key()
    controller_public = controller_key.public_key()
    workstation_principal = principal_id(
        workstation_public, "workstation"
    )
    controller_principal = principal_id(
        controller_public, "controller"
    )

    capabilities = list(FILESYSTEM_CAPABILITIES)
    operations = list(FILESYSTEM_OPERATIONS)
    if allow_process_exec:
        capabilities.extend(PROCESS_CAPABILITIES)
        operations.extend(PROCESS_OPERATIONS)

    unknown_operations = set(operations) - set(ALL_OPERATIONS)
    if unknown_operations:
        raise ProvisioningError(
            "bootstrap operation set is ahead of runtime: "
            + repr(sorted(unknown_operations))
        )

    _write_new(paths["workstation_key"], _private_key_pem(workstation_key))
    _write_new(
        paths["workstation_public"],
        _public_key_pem(workstation_public),
        mode=0o644,
    )
    _write_new(paths["controller_key"], _private_key_pem(controller_key))
    _write_new(paths["tls_key"], _private_key_pem(tls_key))
    _write_new(
        paths["tls_cert"],
        _self_signed_localhost_certificate(tls_key),
        mode=0o644,
    )

    trust = {
        "schema": "VERAPORT_CONTROLLER_TRUST_V1",
        "controllers": [
            {
                "principal": controller_principal,
                "key_id": key_id(controller_public),
                "public_key_pem": _public_key_pem(
                    controller_public
                ).decode("ascii"),
                "capabilities": capabilities,
            }
        ],
    }
    _write_json_new(paths["controller_trust"], trust)

    service = {
        "bind_host": "127.0.0.1",
        "bind_port": int(agent_port),
        "allowed_roots": [str(path) for path in resolved_roots],
        "state_db": str(paths["state_db"]),
        "tls_cert": str(paths["tls_cert"]),
        "tls_key": str(paths["tls_key"]),
        "workstation_key": str(paths["workstation_key"]),
        "controller_trust": str(paths["controller_trust"]),
        "allow_process_exec": allow_process_exec,
        "allow_non_loopback_listener": False,
        "max_lanes": 32,
        "max_inflight": 64,
        "max_read_bytes": 1_048_576,
    }
    _write_json_new(paths["service_config"], service)

    endpoints: list[dict[str, Any]] = [
        {
            "endpoint_id": "direct-loopback",
            "mode": "DIRECT_STREAM",
            "host": "127.0.0.1",
            "port": int(agent_port),
            "server_hostname": "localhost",
            "durable_idempotency": True,
        }
    ]
    if normalized_edge_host is not None:
        endpoints.append(
            {
                "endpoint_id": "verarelay-edge",
                "mode": "EDGE_STREAM",
                "host": normalized_edge_host,
                "port": int(edge_port),
                "server_hostname": "localhost",
                "durable_idempotency": True,
            }
        )

    controller = {
        "schema": "VERAPORT_CONTROLLER_MCP_CONFIG_V1",
        "controller_key": str(paths["controller_key"]),
        "tls_ca": str(paths["tls_cert"]),
        "workstation_public_key": str(paths["workstation_public"]),
        "requested_capabilities": capabilities,
        "gateway_operations": operations,
        "endpoints": endpoints,
        "max_path_age_ms": 5_000,
        "connect_timeout_s": 5.0,
        "request_timeout_s": 5.0,
    }
    _write_json_new(paths["controller_config"], controller)

    manifest = {
        "schema": "VERAMESH_LOCAL_BUNDLE_V1",
        "workstation_principal": workstation_principal,
        "workstation_key_id": key_id(workstation_public),
        "controller_principal": controller_principal,
        "controller_key_id": key_id(controller_public),
        "process_enabled": allow_process_exec,
        "edge_enabled": normalized_edge_host is not None,
        "allowed_roots": [str(path) for path in resolved_roots],
        "service_config": str(paths["service_config"]),
        "controller_config": str(paths["controller_config"]),
    }
    _write_json_new(paths["manifest"], manifest)

    parsed_service = WindowsServiceConfig.load(paths["service_config"])
    parsed_service.validate_runtime_files()

    if os.name == "nt" and apply_windows_acl:
        parsed_controller = ControllerConfig.load(paths["controller_config"])
        harden_service_materials(paths["service_config"], parsed_service)
        harden_controller_materials(
            paths["controller_config"],
            parsed_controller,
        )

    return ProvisionedBundle(
        root=root,
        service_config=paths["service_config"],
        controller_config=paths["controller_config"],
        workstation_principal=workstation_principal,
        controller_principal=controller_principal,
        process_enabled=allow_process_exec,
        edge_enabled=normalized_edge_host is not None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a local VeraMesh/VeraPort identity and config bundle. "
            "This does not install a service or change networking."
        )
    )
    parser.add_argument(
        "--output-dir",
        default=r"C:\ProgramData\VeraMesh",
    )
    parser.add_argument(
        "--allowed-root",
        action="append",
        required=True,
        dest="allowed_roots",
    )
    parser.add_argument(
        "--allow-process-exec",
        action="store_true",
        help=(
            "Opt in to process start/inspect/interact/control capabilities. "
            "Default is filesystem-only."
        ),
    )
    parser.add_argument("--agent-port", type=int, default=17444)
    parser.add_argument("--edge-host")
    parser.add_argument("--edge-port", type=int, default=17445)
    args = parser.parse_args()

    bundle = provision_bundle(
        output_dir=args.output_dir,
        allowed_roots=args.allowed_roots,
        allow_process_exec=args.allow_process_exec,
        agent_port=args.agent_port,
        edge_host=args.edge_host,
        edge_port=args.edge_port,
    )
    print(json.dumps(bundle.as_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
