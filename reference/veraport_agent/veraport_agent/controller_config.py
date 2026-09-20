from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .gateway import ALL_OPERATIONS
from .synchrony import PathMode


class ControllerConfigError(ValueError):
    code = "CONTROLLER_CONFIG_ERROR"


@dataclass(frozen=True)
class EndpointConfig:
    endpoint_id: str
    mode: PathMode
    host: str
    port: int
    server_hostname: str
    durable_idempotency: bool

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EndpointConfig":
        if not isinstance(value, dict):
            raise ControllerConfigError("endpoint must be object")
        endpoint_id = str(value.get("endpoint_id", "")).strip()
        if not endpoint_id:
            raise ControllerConfigError("endpoint_id is required")
        mode_text = str(value.get("mode", "")).strip()
        try:
            mode = PathMode[mode_text]
        except KeyError as exc:
            raise ControllerConfigError(f"unsupported endpoint mode: {mode_text}") from exc
        if mode is PathMode.DURABLE_RELAY:
            raise ControllerConfigError("durable relay is not implemented for controller bootstrap")
        host = str(value.get("host", "")).strip()
        if not host:
            raise ControllerConfigError("endpoint host is required")
        port = int(value.get("port", 0))
        if not 1 <= port <= 65535:
            raise ControllerConfigError("endpoint port outside 1..65535")
        server_hostname = str(value.get("server_hostname", "")).strip()
        if not server_hostname:
            raise ControllerConfigError("server_hostname is required")
        durable = value.get("durable_idempotency")
        if type(durable) is not bool:
            raise ControllerConfigError("durable_idempotency must be exact bool")
        return cls(endpoint_id, mode, host, port, server_hostname, durable)


@dataclass(frozen=True)
class ControllerConfig:
    controller_key: Path
    workstation_cert: Path
    requested_capabilities: frozenset[str]
    gateway_operations: frozenset[str]
    endpoints: tuple[EndpointConfig, ...]
    max_path_age_ms: int = 5_000

    @classmethod
    def load(cls, path: str | Path) -> "ControllerConfig":
        source = Path(path)
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ControllerConfigError(f"cannot read controller config: {exc}") from exc
        return cls.from_dict(raw, base_dir=source.parent)

    @classmethod
    def from_dict(cls, value: dict[str, Any], *, base_dir: Path | None = None) -> "ControllerConfig":
        if not isinstance(value, dict) or value.get("schema") != "VERAPORT_CONTROLLER_MCP_CONFIG_V1":
            raise ControllerConfigError("wrong controller config schema")
        base = Path.cwd() if base_dir is None else base_dir
        controller_key = cls._path(base, value.get("controller_key"))
        workstation_cert = cls._path(base, value.get("workstation_cert"))

        requested = value.get("requested_capabilities")
        if not isinstance(requested, list) or not requested:
            raise ControllerConfigError("requested_capabilities must be non-empty list")
        requested_caps = frozenset(str(item) for item in requested)

        operations = value.get("gateway_operations")
        if not isinstance(operations, list) or not operations:
            raise ControllerConfigError("gateway_operations must be non-empty list")
        gateway_operations = frozenset(str(item) for item in operations)
        unknown = gateway_operations - ALL_OPERATIONS
        if unknown:
            raise ControllerConfigError(f"unknown gateway operations: {sorted(unknown)}")

        required_caps = {
            "fs.read_text": "fs.read",
            "fs.write_text": "fs.write",
            "process.exec": "process.exec",
        }
        for operation, capability in required_caps.items():
            if operation in gateway_operations and capability not in requested_caps:
                raise ControllerConfigError(f"{operation} requires requested capability {capability}")

        raw_endpoints = value.get("endpoints")
        if not isinstance(raw_endpoints, list) or not raw_endpoints:
            raise ControllerConfigError("endpoints must be non-empty list")
        endpoints = tuple(EndpointConfig.from_dict(item) for item in raw_endpoints)
        ids = [item.endpoint_id for item in endpoints]
        if len(ids) != len(set(ids)):
            raise ControllerConfigError("duplicate endpoint_id")

        max_path_age_ms = int(value.get("max_path_age_ms", 5_000))
        if max_path_age_ms < 100 or max_path_age_ms > 300_000:
            raise ControllerConfigError("max_path_age_ms outside policy")

        return cls(
            controller_key=controller_key,
            workstation_cert=workstation_cert,
            requested_capabilities=requested_caps,
            gateway_operations=gateway_operations,
            endpoints=endpoints,
            max_path_age_ms=max_path_age_ms,
        )

    @staticmethod
    def _path(base: Path, value: Any) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ControllerConfigError("credential/certificate path is required")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = base / path
        return path.resolve()
