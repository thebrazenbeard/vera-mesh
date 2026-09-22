from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .controller import HotSessionPool, SessionEndpoint
from .controller_config import ControllerConfig, EndpointConfig
from .gateway import GatewayOperationDenied, VeraPortGateway
from .hot_session import SessionBinding, principal_id
from .read_lane import MirroredReadLaneRouter
from .synchrony import PathDecision, PathObservation, select_path
from .tls_transport import make_client_context, open_tls_session


class ControllerBootstrapError(RuntimeError):
    code = "CONTROLLER_BOOTSTRAP_ERROR"


class ControllerIdentityMismatch(ControllerBootstrapError):
    code = "CONTROLLER_IDENTITY_MISMATCH"


class WorkstationIdentityMismatch(ControllerBootstrapError):
    code = "WORKSTATION_IDENTITY_MISMATCH"


@dataclass
class LiveEndpoint:
    config: EndpointConfig
    binding: SessionBinding
    channel: Any
    path: PathObservation


OpenSession = Callable[..., Awaitable[tuple[Any, SessionBinding]]]


class ControllerRuntime:
    """Persistent authenticated controller runtime shared across MCP reconnects."""

    def __init__(
        self,
        config: ControllerConfig,
        *,
        open_session: OpenSession = open_tls_session,
        now_ms: Callable[[], int] | None = None,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.config = config
        self.open_session = open_session
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.request_id_factory = request_id_factory or (lambda: uuid.uuid4().hex)
        self.pool = HotSessionPool()
        self._live: dict[str, LiveEndpoint] = {}
        self._lock = asyncio.Lock()
        self._controller_private_key: ec.EllipticCurvePrivateKey | None = None
        self._workstation_public_key: ec.EllipticCurvePublicKey | None = None
        self._controller_principal: str | None = None
        self._workstation_principal: str | None = None
        self._gateway: VeraPortGateway | None = None
        self._read_router = MirroredReadLaneRouter(
            live_endpoints=lambda: tuple(self._live.values()),
            now_ms=self.now_ms,
            request_id_factory=self.request_id_factory,
            max_path_age_ms=self.config.max_path_age_ms,
            request_timeout_s=self.config.request_timeout_s,
        )

    @property
    def gateway(self) -> VeraPortGateway:
        if self._gateway is None:
            raise ControllerBootstrapError("controller runtime is not started")
        return self._gateway

    async def ensure_started(self) -> None:
        async with self._lock:
            self._load_identities_once()
            await self._refresh_locked()
            if not self._live:
                raise ControllerBootstrapError(
                    "no authenticated data-plane-verified endpoint"
                )
            if self._gateway is None:
                assert self._controller_principal is not None
                assert self._workstation_principal is not None
                self._gateway = VeraPortGateway(
                    self.pool,
                    workstation_principal=self._workstation_principal,
                    controller_principal=self._controller_principal,
                    allowed_operations=self.config.gateway_operations,
                    now_ms=self.now_ms,
                    request_id_factory=self.request_id_factory,
                    max_path_age_ms=self.config.max_path_age_ms,
                    request_timeout_s=self.config.request_timeout_s,
                )

    async def refresh_paths(self) -> None:
        async with self._lock:
            self._load_identities_once()
            await self._refresh_locked()
            if not self._live:
                raise ControllerBootstrapError(
                    "no authenticated data-plane-verified endpoint"
                )

    async def close(self) -> None:
        async with self._lock:
            for endpoint in tuple(self._live.values()):
                await self._close_channel(endpoint.channel)
            self._live.clear()

    async def machine_info(self) -> dict[str, Any]:
        await self.ensure_started()
        decision = self._route_decision()
        return {
            "schema": "VERAPORT_MCP_MACHINE_INFO_V1",
            "controller_principal": self._controller_principal,
            "workstation_principal": self._workstation_principal,
            "requested_capabilities": sorted(self.config.requested_capabilities),
            "gateway_operations": sorted(self.config.gateway_operations),
            "selected_path_id": (
                None if decision.selected is None else decision.selected.path_id
            ),
            "paths": [
                {
                    "path_id": endpoint.path.path_id,
                    "endpoint_id": endpoint.config.endpoint_id,
                    "mode": endpoint.config.mode.name,
                    "host": endpoint.config.host,
                    "port": endpoint.config.port,
                    "session_id": endpoint.binding.session_id,
                    "expires_at_ms": endpoint.binding.expires_at_ms,
                    "observed_at_ms": endpoint.path.observed_at_ms,
                    "rtt_ms": endpoint.path.rtt_ms,
                    "authenticated": endpoint.path.authenticated,
                    "data_plane_verified": endpoint.path.healthy,
                    "durable_idempotency": endpoint.config.durable_idempotency,
                    "granted_capabilities": sorted(endpoint.binding.granted_capabilities),
                }
                for endpoint in sorted(
                    self._live.values(), key=lambda item: item.config.endpoint_id
                )
            ],
            "rejected_paths": list(decision.rejected),
        }

    async def list_lanes(self) -> dict[str, Any]:
        await self.ensure_started()
        return await self.gateway.list_lanes()

    async def open_lane(self, **kwargs: Any) -> dict[str, Any]:
        await self.ensure_started()
        if self._read_router.supports_open(**kwargs):
            return await self._read_router.open(**kwargs)
        return await self.gateway.open_lane(**kwargs)

    async def renew_lane(self, **kwargs: Any) -> dict[str, Any]:
        await self.ensure_started()
        lane_id = kwargs.get("lane_id")
        fencing_token = kwargs.get("fencing_token")
        if isinstance(lane_id, str) and type(fencing_token) is int and self._read_router.owns(lane_id, fencing_token):
            return await self._read_router.renew(**kwargs)
        return await self.gateway.renew_lane(**kwargs)

    async def close_lane(self, **kwargs: Any) -> dict[str, Any]:
        await self.ensure_started()
        lane_id = kwargs.get("lane_id")
        fencing_token = kwargs.get("fencing_token")
        if isinstance(lane_id, str) and type(fencing_token) is int and self._read_router.owns(lane_id, fencing_token):
            return await self._read_router.close(**kwargs)
        return await self.gateway.close_lane(**kwargs)

    async def read_text(self, **kwargs: Any) -> dict[str, Any]:
        await self.ensure_started()
        lane_id = kwargs.get("lane_id")
        fencing_token = kwargs.get("fencing_token")
        if isinstance(lane_id, str) and type(fencing_token) is int and self._read_router.owns(lane_id, fencing_token):
            return await self._read_router.read_text(**kwargs)
        return await self.gateway.read_text(**kwargs)

    async def read_bytes(self, **kwargs: Any) -> dict[str, Any]:
        await self.ensure_started()
        if "fs.read_bytes" not in self.config.gateway_operations:
            raise GatewayOperationDenied("fs.read_bytes")
        lane_id = kwargs.get("lane_id")
        fencing_token = kwargs.get("fencing_token")
        if (
            isinstance(lane_id, str)
            and type(fencing_token) is int
            and self._read_router.owns(lane_id, fencing_token)
        ):
            return await self._read_router.read_bytes(**kwargs)
        return await self.gateway.read_bytes(**kwargs)

    async def read_operation(
        self,
        operation: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        await self.ensure_started()
        if operation not in {
            "fs.stat",
            "fs.list_dir",
            "fs.search",
            "fs.search_content",
        }:
            raise GatewayOperationDenied(operation)
        if operation not in self.config.gateway_operations:
            raise GatewayOperationDenied(operation)
        lane_id = kwargs.get("lane_id")
        fencing_token = kwargs.get("fencing_token")
        if (
            isinstance(lane_id, str)
            and type(fencing_token) is int
            and self._read_router.owns(lane_id, fencing_token)
        ):
            body = dict(kwargs)
            body.pop("lane_id", None)
            body.pop("fencing_token", None)
            return await self._read_router.call_read_operation(
                operation=operation,
                lane_id=lane_id,
                fencing_token=fencing_token,
                body=body,
            )
        return await self.gateway.call_operation(operation, **kwargs)

    async def write_text(self, **kwargs: Any) -> dict[str, Any]:
        await self.ensure_started()
        return await self.gateway.write_text(**kwargs)

    def _load_identities_once(self) -> None:
        if self._controller_private_key is not None:
            return
        try:
            private = serialization.load_pem_private_key(
                self.config.controller_key.read_bytes(),
                password=None,
            )
        except Exception as exc:
            raise ControllerBootstrapError(
                "cannot load controller private key"
            ) from exc
        if not isinstance(private, ec.EllipticCurvePrivateKey) or not isinstance(
            private.curve, ec.SECP256R1
        ):
            raise ControllerBootstrapError(
                "controller private key must be EC P-256"
            )

        try:
            public = serialization.load_pem_public_key(
                self.config.workstation_public_key.read_bytes()
            )
        except Exception as exc:
            raise ControllerBootstrapError(
                "cannot load workstation application public key"
            ) from exc
        if not isinstance(public, ec.EllipticCurvePublicKey) or not isinstance(
            public.curve, ec.SECP256R1
        ):
            raise ControllerBootstrapError(
                "workstation application public key must be EC P-256"
            )

        self._controller_private_key = private
        self._workstation_public_key = public
        self._controller_principal = principal_id(
            private.public_key(), "controller"
        )
        self._workstation_principal = principal_id(public, "workstation")

    async def _refresh_locked(self) -> None:
        now = self.now_ms()
        for spec in self.config.endpoints:
            current = self._live.get(spec.endpoint_id)
            if current is not None and now < current.binding.expires_at_ms:
                try:
                    current.path = await self._probe(
                        spec, current.channel, current.binding
                    )
                    self.pool.register(
                        SessionEndpoint(
                            endpoint_id=spec.endpoint_id,
                            binding=current.binding,
                            path=current.path,
                            channel=current.channel,
                            durable_idempotency=spec.durable_idempotency,
                        )
                    )
                    continue
                except Exception:
                    self.pool.remove(
                        current.binding.workstation_principal,
                        current.binding.controller_principal,
                        spec.endpoint_id,
                    )
                    await self._close_channel(current.channel)
                    self._live.pop(spec.endpoint_id, None)
            elif current is not None:
                self.pool.remove(
                    current.binding.workstation_principal,
                    current.binding.controller_principal,
                    spec.endpoint_id,
                )
                await self._close_channel(current.channel)
                self._live.pop(spec.endpoint_id, None)

            try:
                await self._connect(spec)
            except Exception:
                continue

    async def _connect(self, spec: EndpointConfig) -> None:
        assert self._controller_private_key is not None
        assert self._workstation_public_key is not None
        assert self._controller_principal is not None
        assert self._workstation_principal is not None

        context = make_client_context(cafile=self.config.tls_ca)
        started = time.perf_counter()
        channel, binding = await asyncio.wait_for(
            self.open_session(
                host=spec.host,
                port=spec.port,
                ssl_context=context,
                server_hostname=spec.server_hostname,
                controller_private_key=self._controller_private_key,
                workstation_public_key=self._workstation_public_key,
                requested_capabilities=self.config.requested_capabilities,
                now_ms=self.now_ms,
                connect_timeout_s=self.config.connect_timeout_s,
                request_timeout_s=self.config.request_timeout_s,
            ),
            timeout=self.config.connect_timeout_s,
        )

        if binding.controller_principal != self._controller_principal:
            await self._close_channel(channel)
            raise ControllerIdentityMismatch(
                binding.controller_principal
            )
        if binding.workstation_principal != self._workstation_principal:
            await self._close_channel(channel)
            raise WorkstationIdentityMismatch(
                binding.workstation_principal
            )

        path = await self._probe(
            spec,
            channel,
            binding,
            connect_started=started,
        )
        endpoint = LiveEndpoint(spec, binding, channel, path)
        self._live[spec.endpoint_id] = endpoint
        self.pool.register(
            SessionEndpoint(
                endpoint_id=spec.endpoint_id,
                binding=binding,
                path=path,
                channel=channel,
                durable_idempotency=spec.durable_idempotency,
            )
        )

    async def _probe(
        self,
        spec: EndpointConfig,
        channel: Any,
        binding: SessionBinding,
        *,
        connect_started: float | None = None,
    ) -> PathObservation:
        started = (
            time.perf_counter()
            if connect_started is None
            else connect_started
        )
        request_id = "probe-" + self.request_id_factory()
        response = await asyncio.wait_for(
            channel.request(
                {
                    "protocol_version": "veraport-v1",
                    "request_id": request_id,
                    "operation": "lane.list",
                }
            ),
            timeout=self.config.request_timeout_s,
        )
        if (
            response.get("request_id") != request_id
            or response.get("ok") is not True
        ):
            raise ControllerBootstrapError(
                f"{spec.endpoint_id}: lane.list data-plane probe failed"
            )
        rtt_ms = (time.perf_counter() - started) * 1000.0
        return PathObservation(
            path_id=spec.endpoint_id,
            mode=spec.mode,
            authenticated=True,
            healthy=True,
            observed_at_ms=self.now_ms(),
            rtt_ms=rtt_ms,
        )

    def _route_decision(self) -> PathDecision:
        return select_path(
            tuple(endpoint.path for endpoint in self._live.values()),
            now_ms=self.now_ms(),
            max_age_ms=self.config.max_path_age_ms,
        )

    @staticmethod
    async def _close_channel(channel: Any) -> None:
        close = getattr(channel, "close", None)
        if close is None:
            return
        result = close()
        if asyncio.iscoroutine(result):
            await result
