from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ServiceConfigError(ValueError):
    code = "SERVICE_CONFIG_ERROR"


@dataclass(frozen=True)
class WindowsServiceConfig:
    bind_host: str
    bind_port: int
    allowed_roots: tuple[Path, ...]
    state_db: Path
    tls_cert: Path
    tls_key: Path
    workstation_key: Path
    controller_trust: Path
    allow_process_exec: bool = False
    allow_non_loopback_listener: bool = False
    max_lanes: int = 32
    max_inflight: int = 64
    max_read_bytes: int = 1_048_576

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WindowsServiceConfig":
        if not isinstance(value, dict):
            raise ServiceConfigError("config must be a JSON object")
        required = (
            "bind_host", "bind_port", "allowed_roots", "state_db",
            "tls_cert", "tls_key", "workstation_key", "controller_trust",
        )
        missing = [key for key in required if key not in value]
        if missing:
            raise ServiceConfigError(f"missing required fields: {missing}")
        roots = value["allowed_roots"]
        if not isinstance(roots, list) or not roots:
            raise ServiceConfigError("allowed_roots must be a non-empty list")
        cfg = cls(
            bind_host=str(value["bind_host"]),
            bind_port=int(value["bind_port"]),
            allowed_roots=tuple(Path(str(item)).expanduser().resolve() for item in roots),
            state_db=Path(str(value["state_db"])).expanduser().resolve(),
            tls_cert=Path(str(value["tls_cert"])).expanduser().resolve(),
            tls_key=Path(str(value["tls_key"])).expanduser().resolve(),
            workstation_key=Path(str(value["workstation_key"])).expanduser().resolve(),
            controller_trust=Path(str(value["controller_trust"])).expanduser().resolve(),
            allow_process_exec=value.get("allow_process_exec", False) is True,
            allow_non_loopback_listener=value.get("allow_non_loopback_listener", False) is True,
            max_lanes=int(value.get("max_lanes", 32)),
            max_inflight=int(value.get("max_inflight", 64)),
            max_read_bytes=int(value.get("max_read_bytes", 1_048_576)),
        )
        cfg.validate_static()
        return cfg

    @classmethod
    def load(cls, path: str | Path) -> "WindowsServiceConfig":
        source = Path(path)
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ServiceConfigError(f"cannot read strict UTF-8 JSON config: {exc}") from exc
        return cls.from_dict(raw)

    def validate_static(self) -> None:
        try:
            address = ipaddress.ip_address(self.bind_host)
        except ValueError as exc:
            raise ServiceConfigError("bind_host must be a literal IP address") from exc
        if not self.allow_non_loopback_listener and not address.is_loopback:
            raise ServiceConfigError(
                "non-loopback listener requires explicit allow_non_loopback_listener=true"
            )
        if not 1 <= self.bind_port <= 65535:
            raise ServiceConfigError("bind_port must be in 1..65535")
        if self.max_lanes < 1 or self.max_lanes > 1024:
            raise ServiceConfigError("max_lanes outside policy")
        if self.max_inflight < 1 or self.max_inflight > 4096:
            raise ServiceConfigError("max_inflight outside policy")
        if self.max_read_bytes < 1 or self.max_read_bytes > 16_777_216:
            raise ServiceConfigError("max_read_bytes outside policy")
        if len(set(self.allowed_roots)) != len(self.allowed_roots):
            raise ServiceConfigError("allowed_roots contains duplicates")

    def validate_runtime_files(self) -> None:
        for path in (self.tls_cert, self.tls_key, self.workstation_key, self.controller_trust):
            if not path.is_file():
                raise ServiceConfigError(f"required trust/identity file missing: {path}")
        self.state_db.parent.mkdir(parents=True, exist_ok=True)
        for root in self.allowed_roots:
            if not root.is_dir():
                raise ServiceConfigError(f"allowed root does not exist: {root}")
