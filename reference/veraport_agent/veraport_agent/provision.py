from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
from typing import Iterable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .hot_session import key_id, principal_id


FILESYSTEM_CAPABILITIES = frozenset({"fs.read", "fs.write"})
PROCESS_CAPABILITIES = frozenset({
    "process.exec",
    "process.inspect",
    "process.interact",
    "process.control",
})
KNOWN_CAPABILITIES = FILESYSTEM_CAPABILITIES | PROCESS_CAPABILITIES


class ProvisionError(RuntimeError):
    code = "PROVISION_ERROR"


def _private_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _public_pem(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ProvisionError(f"refusing to overwrite existing file: {path}") from exc
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


def _certificate(
    tls_key: ec.EllipticCurvePrivateKey,
    *,
    now: dt.datetime,
    valid_days: int,
) -> x509.Certificate:
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(tls_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=valid_days))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                x509.IPAddress(ipaddress.ip_address("::1")),
            ]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0),
            critical=True,
        )
        .sign(tls_key, hashes.SHA256())
    )


def provision_local_pair(
    output_dir: str | Path,
    *,
    capabilities: Iterable[str] = FILESYSTEM_CAPABILITIES,
    valid_days: int = 825,
    now: dt.datetime | None = None,
    harden_windows_acl: bool = True,
) -> dict:
    root = Path(output_dir).expanduser().resolve()
    requested = frozenset(str(item) for item in capabilities)
    unknown = requested - KNOWN_CAPABILITIES
    if unknown:
        raise ProvisionError(f"unknown capabilities: {sorted(unknown)}")
    if not FILESYSTEM_CAPABILITIES.issubset(requested):
        raise ProvisionError("filesystem read/write capabilities are required")
    if not 1 <= int(valid_days) <= 3650:
        raise ProvisionError("valid_days must be in 1..3650")

    target_names = (
        "workstation-key.pem",
        "workstation-public.pem",
        "controller-key.pem",
        "controller-public.pem",
        "controller-trust.json",
        "tls-key.pem",
        "tls-cert.pem",
        "tls-ca.pem",
        "identity-manifest.json",
    )
    existing = [root / name for name in target_names if (root / name).exists()]
    if existing:
        raise ProvisionError(
            "refusing to provision over existing material: "
            + ", ".join(str(path) for path in existing)
        )

    root.mkdir(parents=True, exist_ok=True)
    if os.name == "nt" and harden_windows_acl:
        from .windows_acl import harden_private_directory

        # Establish the protected LocalSystem/Admin boundary before any private
        # material is created; chmod-style modes are not a Windows ACL policy.
        harden_private_directory(root)

    workstation = ec.generate_private_key(ec.SECP256R1())
    controller = ec.generate_private_key(ec.SECP256R1())
    tls_key = ec.generate_private_key(ec.SECP256R1())
    effective_now = now or dt.datetime.now(dt.timezone.utc)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=dt.timezone.utc)
    cert = _certificate(
        tls_key,
        now=effective_now,
        valid_days=int(valid_days),
    )

    workstation_public = workstation.public_key()
    controller_public = controller.public_key()
    controller_principal = principal_id(controller_public, "controller")
    workstation_principal = principal_id(workstation_public, "workstation")
    controller_key_id = key_id(controller_public)
    workstation_key_id = key_id(workstation_public)

    trust = {
        "schema": "VERAPORT_CONTROLLER_TRUST_V1",
        "controllers": [{
            "principal": controller_principal,
            "key_id": controller_key_id,
            "public_key_pem": _public_pem(controller_public).decode("ascii"),
            "capabilities": sorted(requested),
        }],
    }

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    files = {
        "workstation-key.pem": _private_pem(workstation),
        "workstation-public.pem": _public_pem(workstation_public),
        "controller-key.pem": _private_pem(controller),
        "controller-public.pem": _public_pem(controller_public),
        "controller-trust.json": (
            json.dumps(trust, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
        "tls-key.pem": _private_pem(tls_key),
        "tls-cert.pem": cert_pem,
        "tls-ca.pem": cert_pem,
    }

    for name, payload in files.items():
        target = root / name
        _write_new(target, payload)
        if os.name == "nt" and harden_windows_acl:
            from .windows_acl import harden_private_file

            harden_private_file(target)

    manifest = {
        "schema": "VERAPORT_LOCAL_IDENTITY_MANIFEST_V1",
        "workstation_principal": workstation_principal,
        "workstation_key_id": workstation_key_id,
        "controller_principal": controller_principal,
        "controller_key_id": controller_key_id,
        "capabilities": sorted(requested),
        "tls_dns_names": ["localhost"],
        "tls_ip_addresses": ["127.0.0.1", "::1"],
        "tls_not_before": cert.not_valid_before_utc.isoformat(),
        "tls_not_after": cert.not_valid_after_utc.isoformat(),
        "files": {
            name: {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "private": name in {
                    "workstation-key.pem",
                    "controller-key.pem",
                    "tls-key.pem",
                },
            }
            for name, payload in sorted(files.items())
        },
    }
    manifest_path = root / "identity-manifest.json"
    _write_new(
        manifest_path,
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    if os.name == "nt" and harden_windows_acl:
        from .windows_acl import harden_private_file

        harden_private_file(manifest_path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision a local VeraPort controller/workstation identity pair."
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--enable-process",
        action="store_true",
        help=(
            "include process.exec/inspect/interact/control in controller trust; "
            "the Lappy service still requires allow_process_exec=true separately"
        ),
    )
    parser.add_argument("--valid-days", type=int, default=825)
    args = parser.parse_args()

    capabilities = set(FILESYSTEM_CAPABILITIES)
    if args.enable_process:
        capabilities.update(PROCESS_CAPABILITIES)
    manifest = provision_local_pair(
        args.output_dir,
        capabilities=capabilities,
        valid_days=args.valid_days,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
