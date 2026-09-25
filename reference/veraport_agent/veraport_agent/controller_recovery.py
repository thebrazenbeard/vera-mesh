from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .hot_session import key_id, principal_id
from .identity_store import load_identity
from .service_config import WindowsServiceConfig


class ControllerRecoveryError(RuntimeError):
    code = "CONTROLLER_RECOVERY_ERROR"


READ_ONLY_CAPABILITIES = frozenset({"fs.read"})


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _inside(root: Path, path: str | Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ControllerRecoveryError(
            f"{label} must stay inside existing VeraMesh root {root}: {resolved}"
        ) from exc
    return resolved


def _load_private(path: Path) -> ec.EllipticCurvePrivateKey:
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except Exception as exc:
        raise ControllerRecoveryError(
            f"cannot load controller private key: {path}"
        ) from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise ControllerRecoveryError("controller private key must be EC P-256")
    return key


def _private_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _public_pem(key: ec.EllipticCurvePublicKey) -> str:
    return key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ControllerRecoveryError(
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
            # Cleanup is best-effort after a failed exclusive create.
            pass
        raise


def _write_or_verify(path: Path, payload: bytes) -> str:
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise ControllerRecoveryError(
                f"existing recovery artifact diverges: {path}"
            )
        return "reused_exact"
    _write_new(path, payload)
    return "created"


def _harden_private(path: Path, *, directory: bool = False) -> None:
    if os.name != "nt":
        return
    from .windows_acl import harden_private_directory, harden_private_file

    if directory:
        harden_private_directory(path)
    else:
        harden_private_file(path)


def _atomic_replace_private(path: Path, payload: bytes) -> None:
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    _write_new(temp, payload)
    try:
        _harden_private(temp)
        os.replace(temp, path)
        _harden_private(path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                # If cleanup fails, preserve evidence rather than masking the
                # primary trust-write result.
                pass


def _trust_document(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControllerRecoveryError("controller trust is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict) or value.get("schema") != "VERAPORT_CONTROLLER_TRUST_V1":
        raise ControllerRecoveryError("wrong controller trust schema")
    entries = value.get("controllers")
    if not isinstance(entries, list) or not entries:
        raise ControllerRecoveryError("controller trust list must be non-empty")
    return value, raw


def _recovery_marker(
    key_path: Path,
    principal: str,
    controller_key_id: str,
) -> dict[str, Any]:
    return {
        "schema": "VERAPORT_CONTROLLER_RECOVERY_MARKER_V1",
        "controller_private_key": str(key_path),
        "controller_principal": principal,
        "controller_key_id": controller_key_id,
        "capabilities": ["fs.read"],
    }


def _recovery_lock_path(service_config_path: str | Path) -> Path:
    service_path = Path(service_config_path).expanduser().resolve()
    service = WindowsServiceConfig.load(service_path)
    return service.controller_trust.with_name(
        service.controller_trust.name + ".recovery.lock"
    )


def _acquire_recovery_lock(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ControllerRecoveryError(
            "controller trust recovery lock already exists; "
            "another recovery may be active or a prior recovery requires "
            f"explicit reconciliation: {path}"
        ) from exc
    try:
        payload = (
            json.dumps(
                {
                    "schema": "VERAPORT_CONTROLLER_RECOVERY_LOCK_V1",
                    "pid": os.getpid(),
                },
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "nt":
            _harden_private(path)
    except Exception as exc:
        try:
            path.unlink()
        except OSError as cleanup_exc:
            raise ControllerRecoveryError(
                "controller trust recovery lock setup failed and the lock "
                f"could not be cleaned up: {path}"
            ) from cleanup_exc
        raise exc


def _release_recovery_lock(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ControllerRecoveryError(
            f"controller trust recovery lock could not be released: {path}"
        ) from exc


def _validate_marker(
    marker_path: Path,
    *,
    key_path: Path,
    principal: str,
    controller_key_id: str,
) -> None:
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ControllerRecoveryError("cannot read controller recovery marker") from exc
    expected = _recovery_marker(key_path, principal, controller_key_id)
    if marker != expected:
        raise ControllerRecoveryError(
            "existing generated controller key has no matching recovery marker"
        )


def _ensure_readonly_controller_locked(
    *,
    service_config_path: str | Path,
    preferred_controller_private_key_path: str | Path,
    generated_controller_private_key_path: str | Path,
    harden_windows_acl: bool = True,
) -> dict[str, Any]:
    service_path = Path(service_config_path).expanduser().resolve()
    service = WindowsServiceConfig.load(service_path)
    service.validate_runtime_files()
    root = service_path.parent.resolve()

    preferred = _inside(
        root,
        preferred_controller_private_key_path,
        "preferred_controller_private_key_path",
    )
    generated = _inside(
        root,
        generated_controller_private_key_path,
        "generated_controller_private_key_path",
    )
    if preferred == generated:
        raise ControllerRecoveryError(
            "preferred and generated controller paths must be distinct"
        )

    before_identity = load_identity(
        workstation_key_path=service.workstation_key,
        controller_trust_path=service.controller_trust,
    )
    trust_doc, trust_raw = _trust_document(service.controller_trust)
    trust_before_sha = _sha256_bytes(trust_raw)

    preferred_status = "missing"
    if preferred.is_file():
        preferred_private = _load_private(preferred)
        preferred_principal = principal_id(
            preferred_private.public_key(),
            "controller",
        )
        preferred_ceiling = before_identity.capability_policy.get(
            preferred_principal
        )
        if preferred_ceiling == READ_ONLY_CAPABILITIES:
            return {
                "schema": "VERAPORT_CONTROLLER_RECOVERY_V1",
                "mode": "REUSED_ENROLLED_CONTROLLER",
                "controller_private_key": str(preferred),
                "controller_principal": preferred_principal,
                "controller_key_id": key_id(preferred_private.public_key()),
                "capabilities": sorted(preferred_ceiling),
                "preferred_status": "enrolled",
                "trust_path": str(service.controller_trust),
                "trust_before_sha256": trust_before_sha,
                "trust_after_sha256": trust_before_sha,
                "trust_backup": None,
                "old_controller_entries_preserved": True,
                "service_restart_required": False,
                "private_key_value_recorded": False,
            }
        if preferred_ceiling is None:
            preferred_status = "present_but_not_enrolled"
        elif "fs.read" not in preferred_ceiling:
            preferred_status = "enrolled_without_fs_read"
        else:
            preferred_status = "enrolled_but_broader_than_fs_read"

    marker_path = generated.with_suffix(generated.suffix + ".recovery.json")
    generated_new = False
    if generated.exists():
        if not generated.is_file():
            raise ControllerRecoveryError(
                f"generated controller path is not a file: {generated}"
            )
        generated_private = _load_private(generated)
        generated_principal = principal_id(
            generated_private.public_key(),
            "controller",
        )
        generated_key_id = key_id(generated_private.public_key())
        existing_ceiling = before_identity.capability_policy.get(
            generated_principal
        )
        if existing_ceiling is not None:
            if READ_ONLY_CAPABILITIES != frozenset(existing_ceiling):
                raise ControllerRecoveryError(
                    "generated controller is enrolled with a capability ceiling "
                    "other than exactly fs.read"
                )
            return {
                "schema": "VERAPORT_CONTROLLER_RECOVERY_V1",
                "mode": "REUSED_ENROLLED_READONLY_CONTROLLER",
                "controller_private_key": str(generated),
                "controller_principal": generated_principal,
                "controller_key_id": generated_key_id,
                "capabilities": ["fs.read"],
                "preferred_status": preferred_status,
                "trust_path": str(service.controller_trust),
                "trust_before_sha256": trust_before_sha,
                "trust_after_sha256": trust_before_sha,
                "trust_backup": None,
                "old_controller_entries_preserved": True,
                "service_restart_required": False,
                "private_key_value_recorded": False,
            }
        if not marker_path.is_file():
            raise ControllerRecoveryError(
                "generated controller key already exists but is not enrolled and "
                "has no recovery marker"
            )
        _validate_marker(
            marker_path,
            key_path=generated,
            principal=generated_principal,
            controller_key_id=generated_key_id,
        )
    else:
        if marker_path.exists():
            raise ControllerRecoveryError(
                "controller recovery marker exists without its private key"
            )
        generated.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt" and harden_windows_acl:
            _harden_private(generated.parent, directory=True)
        generated_private = ec.generate_private_key(ec.SECP256R1())
        generated_principal = principal_id(
            generated_private.public_key(),
            "controller",
        )
        generated_key_id = key_id(generated_private.public_key())
        _write_new(generated, _private_pem(generated_private))
        marker_payload = (
            json.dumps(
                _recovery_marker(
                    generated,
                    generated_principal,
                    generated_key_id,
                ),
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        _write_new(marker_path, marker_payload)
        if os.name == "nt" and harden_windows_acl:
            _harden_private(generated)
            _harden_private(marker_path)
        generated_new = True

    latest_raw = service.controller_trust.read_bytes()
    if _sha256_bytes(latest_raw) != trust_before_sha:
        raise ControllerRecoveryError(
            "controller trust changed during recovery; refusing blind retry"
        )

    backup = service.controller_trust.with_name(
        service.controller_trust.name
        + ".pre-chatgpt-readonly-"
        + trust_before_sha[:12]
        + ".bak"
    )
    _write_or_verify(backup, trust_raw)
    if os.name == "nt" and harden_windows_acl:
        _harden_private(backup)

    old_entries = list(trust_doc["controllers"])
    new_entry = {
        "principal": generated_principal,
        "key_id": generated_key_id,
        "public_key_pem": _public_pem(generated_private.public_key()),
        "capabilities": ["fs.read"],
    }
    trust_doc["controllers"] = old_entries + [new_entry]
    new_raw = (
        json.dumps(trust_doc, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_replace_private(service.controller_trust, new_raw)

    after_identity = load_identity(
        workstation_key_path=service.workstation_key,
        controller_trust_path=service.controller_trust,
    )
    if generated_principal not in after_identity.allowed_controllers:
        raise ControllerRecoveryError(
            "new controller missing after trust update"
        )
    if after_identity.capability_policy[generated_principal] != READ_ONLY_CAPABILITIES:
        raise ControllerRecoveryError(
            "new controller capability ceiling is not exactly fs.read"
        )
    if set(before_identity.allowed_controllers) - set(after_identity.allowed_controllers):
        raise ControllerRecoveryError(
            "existing controller entry disappeared during recovery"
        )

    return {
        "schema": "VERAPORT_CONTROLLER_RECOVERY_V1",
        "mode": "ENROLLED_NEW_READONLY_CONTROLLER",
        "controller_private_key": str(generated),
        "controller_principal": generated_principal,
        "controller_key_id": generated_key_id,
        "capabilities": ["fs.read"],
        "preferred_status": preferred_status,
        "generated_private_key_created": generated_new,
        "trust_path": str(service.controller_trust),
        "trust_before_sha256": trust_before_sha,
        "trust_after_sha256": _sha256(service.controller_trust),
        "trust_backup": str(backup),
        "old_controller_entries_preserved": True,
        "service_restart_required": True,
        "private_key_value_recorded": False,
    }



def ensure_readonly_controller(
    *,
    service_config_path: str | Path,
    preferred_controller_private_key_path: str | Path,
    generated_controller_private_key_path: str | Path,
    harden_windows_acl: bool = True,
) -> dict[str, Any]:
    lock_path = _recovery_lock_path(service_config_path)
    _acquire_recovery_lock(lock_path)
    try:
        result = _ensure_readonly_controller_locked(
            service_config_path=service_config_path,
            preferred_controller_private_key_path=preferred_controller_private_key_path,
            generated_controller_private_key_path=generated_controller_private_key_path,
            harden_windows_acl=harden_windows_acl,
        )
        result["recovery_lock"] = {
            "path": str(lock_path),
            "held_for_entire_transaction": True,
            "released_on_return": True,
            "noncooperating_external_writer_ceiling": (
                "not prevented; external/manual trust mutation remains outside "
                "the cooperative VeraMesh writer lock"
            ),
        }
        return result
    finally:
        _release_recovery_lock(lock_path)

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Recover an already-enrolled VeraPort controller key when possible; "
            "otherwise append a new fs.read-only controller without retiring "
            "existing controller trust."
        )
    )
    parser.add_argument(
        "--service-config",
        default=r"C:\ProgramData\VeraMesh\veraport.json",
    )
    parser.add_argument("--preferred-controller-private-key", required=True)
    parser.add_argument("--generated-controller-private-key", required=True)
    args = parser.parse_args()

    result = ensure_readonly_controller(
        service_config_path=args.service_config,
        preferred_controller_private_key_path=args.preferred_controller_private_key,
        generated_controller_private_key_path=args.generated_controller_private_key,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
