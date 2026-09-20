from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .controller import HotSessionPool, SessionEndpoint
from .gateway import ALL_OPERATIONS, VeraPortGateway
from .hot_session import principal_id
from .synchrony import PathMode, PathObservation
from .tls_transport import make_client_context, open_tls_session


class ControllerBootstrapError(RuntimeError):
    code = "CONTROLLER_BOOTSTRAP_ERROR"


@dataclass(frozen=True)
class EndpointConfig:
    endpoint_id: str
    mode: PathMode
    host: str
    port: int
    durable_idempotency: bool = False


@dataclass(frozen=True)
class ControllerConfig:
    controller_key: Path
    workstation_public_key: Path
    ca_file: Path
    server_hostname: str
    requested_capabilities: frozenset[str]
    allowed_operations: frozenset[str]
    endpoints: tuple[EndpointConfig, ...]

    @classmethod
    def load(cls, path: str | Path) -> "ControllerConfig":
        source = Path(path)
        value = json.loads(source.read_text(encoding="utf-8"))
        if value.get("schema") != "VERAPORT_MCP_CONTROLLER_CONFIG_V1":
            raise ControllerBootstrapError("wrong controller config schema")
        operations = frozenset(str(x) for x in value.get("allowed_operations", []))
        unknown = operations - ALL_OPERATIONS
        if unknown:
            raise ControllerBootstrapError(f"unknown operations: {sorted(unknown)}")
        endpoints = []
        for item in value.get("endpoints", []):
            mode_name = str(item["mode"])
            if mode_name == "DURABLE_RELAY":
                raise ControllerBootstrapError(
                    "DURABLE_RELAY is not implemented/qualified in VeraPort V1"
                )
            try:
                mode = PathMode[mode_name]
            except KeyError as exc:
                raise ControllerBootstrapError(f"unknown route mode {mode_name}") from exc
            endpoints.append(EndpointConfig(
                endpoint_id=str(item["endpoint_id"]),
                mode=mode,
                host=str(item["host"]),
                port=int(item["port"]),
                durable_idempotency=item.get("durable_idempotency", False) is True,
            ))
        if not endpoints:
            raise ControllerBootstrapError("at least one endpoint is required")
        return cls(
            controller_key=Path(str(value["controller_key"])).expanduser(),
            workstation_public_key=Path(str(value["workstation_public_key"])).expanduser(),
            ca_file=Path(str(value["ca_file"])).expanduser(),
            server_hostname=str(value["server_hostname"]),
            requested_capabilities=frozenset(str(x) for x in value.get("requested_capabilities", [])),
            allowed_operations=operations,
            endpoints=tuple(endpoints),
        )


@dataclass
class _ConnectedEndpoint:
    config: EndpointConfig
    channel: Any
    binding: Any
    last_verified_ms: int


class ControllerRuntime:
    """Persistent controller process shared by all MCP transport sessions."""

    def __init__(self, config: ControllerConfig, *, health_max_age_ms: int = 4_000) -> None:
        self.config = config
        self.health_max_age_ms = health_max_age_ms
        self.pool = HotSessionPool()
        self.gateway: VeraPortGateway | None = None
        self._connected: dict[str, _ConnectedEndpoint] = {}
        self._lock = asyncio.Lock()
        self._controller_private: ec.EllipticCurvePrivateKey | None = None
        self._workstation_public: ec.EllipticCurvePublicKey | None = None
        self._ssl_context = None

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    def _load_material(self) -> None:
        controller = serialization.load_pem_private_key(
            self.config.controller_key.read_bytes(), password=None
        )
        workstation = serialization.load_pem_public_key(
            self.config.workstation_public_key.read_bytes()
        )
        if not isinstance(controller, ec.EllipticCurvePrivateKey):
            raise ControllerBootstrapError("controller key is not EC private key")
        if not isinstance(workstation, ec.EllipticCurvePublicKey):
            raise ControllerBootstrapError("workstation key is not EC public key")
        self._controller_private = controller
        self._workstation_public = workstation
        self._ssl_context = make_client_context(cafile=self.config.ca_file)

    async def start(self) -> None:
        async with self._lock:
            if self.gateway is not None:
                return
            self._load_material()
            failures: dict[str, str] = {}
            for endpoint in self.config.endpoints:
                try:
                    await self._connect_endpoint(endpoint)
                except Exception as exc:
                    failures[endpoint.endpoint_id] = f"{type(exc).__name__}: {exc}"
            if not self._connected:
                raise ControllerBootstrapError(f"no endpoint connected: {failures}")
            bindings = [x.binding for x in self._connected.values()]
            workstation = {x.workstation_principal for x in bindings}
            controllers = {x.controller_principal for x in bindings}
            if len(workstation) != 1 or len(controllers) != 1:
                await self.close()
                raise ControllerBootstrapError(
                    "connected endpoints disagree on workstation/controller identity"
                )
            self.gateway = VeraPortGateway(
                self.pool,
                workstation_principal=bindings[0].workstation_principal,
                controller_principal=bindings[0].controller_principal,
                allowed_operations=self.config.allowed_operations,
            )

    async def _connect_endpoint(self, endpoint: EndpointConfig) -> None:
        assert self._controller_private is not None
        assert self._workstation_public is not None
        assert self._ssl_context is not None
        channel, binding = await open_tls_session(
            host=endpoint.host,
            port=endpoint.port,
            ssl_context=self._ssl_context,
            server_hostname=self.config.server_hostname,
            controller_private_key=self._controller_private,
            workstation_public_key=self._workstation_public,
            requested_capabilities=self.config.requested_capabilities,
        )
        now = self._now_ms()
        observation = PathObservation(
            path_id=endpoint.endpoint_id,
            mode=endpoint.mode,
            authenticated=True,
            healthy=True,
            observed_at_ms=now,
        )
        self.pool.register(SessionEndpoint(
            endpoint_id=endpoint.endpoint_id,
            binding=binding,
            path=observation,
            channel=channel,
            durable_idempotency=endpoint.durable_idempotency,
        ))
        self._connected[endpoint.endpoint_id] = _ConnectedEndpoint(
            config=endpoint, channel=channel, binding=binding, last_verified_ms=now
        )

    async def ensure_current(self) -> None:
        if self.gateway is None:
            await self.start()
        async with self._lock:
            now = self._now_ms()
            for endpoint_id, connected in tuple(self._connected.items()):
                if now >= connected.binding.expires_at_ms:
                    await self._replace_endpoint(endpoint_id)
                    continue
                if now - connected.last_verified_ms <= self.health_max_age_ms:
                    continue
                request = {
                    "protocol_version": "veraport-v1",
                    "request_id": f"health-{endpoint_id}-{now}",
                    "operation": "lane.list",
                }
                try:
                    response = await connected.channel.request(request)
                    if response.get("ok") is not True:
                        raise ControllerBootstrapError("health probe returned non-pass")
                    connected.last_verified_ms = self._now_ms()
                    self.pool.register(SessionEndpoint(
                        endpoint_id=endpoint_id,
                        binding=connected.binding,
                        path=PathObservation(
                            path_id=endpoint_id,
                            mode=connected.config.mode,
                            authenticated=True,
                            healthy=True,
                            observed_at_ms=connected.last_verified_ms,
                        ),
                        channel=connected.channel,
                        durable_idempotency=connected.config.durable_idempotency,
                    ))
                except Exception:
                    await self._replace_endpoint(endpoint_id)

    async def _replace_endpoint(self, endpoint_id: str) -> None:
        old = self._connected.pop(endpoint_id, None)
        if old is not None:
            self.pool.remove(
                old.binding.workstation_principal,
                old.binding.controller_principal,
                endpoint_id,
            )
            try:
                await old.channel.close()
            except Exception:
                pass
        config = next((x for x in self.config.endpoints if x.endpoint_id == endpoint_id), None)
        if config is None:
            return
        await self._connect_endpoint(config)

    def machine_info(self) -> dict[str, Any]:
        return {
            "controller_principal": (
                None if self._controller_private is None
                else principal_id(self._controller_private.public_key(), "controller")
            ),
            "gateway_operations": sorted(self.config.allowed_operations),
            "requested_capabilities": sorted(self.config.requested_capabilities),
            "endpoints": [
                {
                    "endpoint_id": x.config.endpoint_id,
                    "mode": x.config.mode.name,
                    "host": x.config.host,
                    "port": x.config.port,
                    "session_id": x.binding.session_id,
                    "workstation_principal": x.binding.workstation_principal,
                    "expires_at_ms": x.binding.expires_at_ms,
                    "last_verified_ms": x.last_verified_ms,
                    "durable_idempotency": x.config.durable_idempotency,
                }
                for x in self._connected.values()
            ],
            "transport_reconnect_recreates_veraport_session": False,
            "controller_process_restart_recreates_veraport_session": True,
        }

    async def close(self) -> None:
        for item in tuple(self._connected.values()):
            try:
                await item.channel.close()
            except Exception:
                pass
        self._connected.clear()
        self.gateway = None
