from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from .tunnel_runtime_service import (
    TunnelRuntimeServiceConfig,
    harden_service_materials,
    validate_service_materials,
)


class TunnelRuntimeResealError(RuntimeError):
    code = "TUNNEL_RUNTIME_RESEAL_ERROR"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _strict_json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise TunnelRuntimeResealError(
            f"cannot parse strict UTF-8 JSON: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise TunnelRuntimeResealError("tunnel config root must be an object")
    if value.get("schema") != "VERAMESH_TUNNEL_RUNTIME_SERVICE_V1":
        raise TunnelRuntimeResealError("wrong tunnel runtime config schema")
    return value, raw


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _atomic_replace(path: Path, payload: bytes) -> None:
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    _write_new(temp, payload)
    try:
        os.replace(temp, path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def reseal_tunnel_runtime(
    *,
    config_path: str | Path,
    expected_runtime_root: str | Path,
) -> dict[str, Any]:
    source = Path(config_path).expanduser().resolve()
    runtime_root = Path(expected_runtime_root).expanduser().resolve()
    value, raw = _strict_json(source)

    current = TunnelRuntimeServiceConfig.from_dict(value)
    if not current.tunnel_client.is_file():
        raise TunnelRuntimeResealError("tunnel-client executable is missing")
    if not current.mcp_executable.is_file():
        raise TunnelRuntimeResealError("MCP executable is missing")
    if not _is_within(current.mcp_executable, runtime_root):
        raise TunnelRuntimeResealError(
            "MCP executable escaped the expected isolated runtime root"
        )

    tunnel_hash = _sha256_file(current.tunnel_client)
    if tunnel_hash != current.tunnel_client_sha256:
        raise TunnelRuntimeResealError(
            "refusing reseal because tunnel-client hash also changed"
        )

    mcp_before = current.mcp_executable_sha256
    mcp_after = _sha256_file(current.mcp_executable)

    backup = source.with_name(
        source.name + ".pre-reseal-" + hashlib.sha256(raw).hexdigest()[:12] + ".bak"
    )
    if backup.exists():
        if not backup.is_file() or backup.read_bytes() != raw:
            raise TunnelRuntimeResealError("existing tunnel config backup diverges")
    else:
        _write_new(backup, raw)

    value["mcp_executable_sha256"] = mcp_after
    payload = (
        json.dumps(value, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    changed = payload != raw

    if changed:
        if source.read_bytes() != raw:
            raise TunnelRuntimeResealError(
                "tunnel config changed before reseal; refusing blind write"
            )
        _atomic_replace(source, payload)

    try:
        updated = TunnelRuntimeServiceConfig.load(source)
        updated.validate_runtime_files()
        harden_service_materials(source, updated)
        validate_service_materials(source, updated)
        updated.validate_runtime_files()
    except Exception:
        if changed:
            _atomic_replace(source, raw)
        raise

    return {
        "schema": "VERAMESH_TUNNEL_RUNTIME_RESEAL_V1",
        "config": str(source),
        "runtime_root": str(runtime_root),
        "mcp_executable": str(updated.mcp_executable),
        "mcp_sha256_before": mcp_before,
        "mcp_sha256_after": mcp_after,
        "config_changed": changed,
        "tunnel_client_sha256_unchanged": True,
        "acl_resealed": True,
        "runtime_files_valid": True,
        "backup": str(backup),
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Reseal the VeraMesh tunnel MCP executable digest and ACL."
    )
    parser.add_argument(
        "--config",
        default=r"C:\ProgramData\VeraMesh\tunnel-runtime.json",
    )
    parser.add_argument(
        "--runtime-root",
        default=r"C:\ProgramData\VeraMesh\tunnel-runtime",
    )
    args = parser.parse_args()
    result = reseal_tunnel_runtime(
        config_path=args.config,
        expected_runtime_root=args.runtime_root,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
