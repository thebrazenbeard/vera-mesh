from __future__ import annotations

import ipaddress
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .controller_runtime import ControllerRuntime
from .public_mcp import PublicMCPBundle, build_public_mcp_protocol
from .public_tool_policy import SCOPES


class PublicGatewayConfigError(ValueError):
    code = "PUBLIC_GATEWAY_CONFIG_ERROR"


@dataclass(frozen=True)
class PublicGatewayHTTPConfig:
    public_mcp_url: str
    issuer_url: str
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...] = ()
    bind_host: str = "127.0.0.1"
    bind_port: int = 17446
    resource_name: str = "VeraMesh Workstation"

    def __post_init__(self) -> None:
        public = self._https_url(self.public_mcp_url, "public_mcp_url")
        issuer = self._https_url(self.issuer_url, "issuer_url")
        if public.query or public.fragment:
            raise PublicGatewayConfigError(
                "public_mcp_url must not contain query or fragment"
            )
        if issuer.query or issuer.fragment:
            raise PublicGatewayConfigError(
                "issuer_url must not contain query or fragment"
            )
        if public.path.rstrip("/") != "/mcp":
            raise PublicGatewayConfigError(
                "public_mcp_url path must be exactly /mcp"
            )
        if not self.allowed_hosts:
            raise PublicGatewayConfigError(
                "allowed_hosts must contain the public Host value"
            )
        if public.netloc not in self.allowed_hosts:
            raise PublicGatewayConfigError(
                "allowed_hosts must include the public_mcp_url host"
            )
        for host in self.allowed_hosts:
            if (
                not isinstance(host, str)
                or not host
                or "/" in host
                or any(ch.isspace() for ch in host)
            ):
                raise PublicGatewayConfigError(
                    f"invalid allowed host: {host!r}"
                )
        for origin in self.allowed_origins:
            parsed = self._https_url(origin, "allowed_origin")
            if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise PublicGatewayConfigError(
                    "allowed origins must be HTTPS origins without path/query/fragment"
                )
        normalized_bind = self.bind_host.strip()
        try:
            address = ipaddress.ip_address(normalized_bind)
        except ValueError as exc:
            raise PublicGatewayConfigError(
                "bind_host must be a literal loopback address"
            ) from exc
        if not address.is_loopback:
            raise PublicGatewayConfigError(
                "public gateway source listener must remain loopback-only"
            )
        if type(self.bind_port) is not int or not 1 <= self.bind_port <= 65535:
            raise PublicGatewayConfigError(
                "bind_port must be in 1..65535"
            )
        if not self.resource_name.strip():
            raise PublicGatewayConfigError("resource_name is required")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PublicGatewayHTTPConfig":
        if not isinstance(value, dict):
            raise PublicGatewayConfigError("config must be an object")
        if value.get("schema") != "VERAMESH_PUBLIC_GATEWAY_HTTP_V1":
            raise PublicGatewayConfigError("wrong public gateway config schema")
        allowed_hosts = value.get("allowed_hosts")
        if not isinstance(allowed_hosts, list) or not allowed_hosts:
            raise PublicGatewayConfigError(
                "allowed_hosts must be a non-empty list"
            )
        allowed_origins = value.get("allowed_origins", [])
        if not isinstance(allowed_origins, list):
            raise PublicGatewayConfigError(
                "allowed_origins must be a list"
            )
        return cls(
            public_mcp_url=str(value.get("public_mcp_url", "")),
            issuer_url=str(value.get("issuer_url", "")),
            allowed_hosts=tuple(str(item) for item in allowed_hosts),
            allowed_origins=tuple(str(item) for item in allowed_origins),
            bind_host=str(value.get("bind_host", "127.0.0.1")),
            bind_port=int(value.get("bind_port", 17446)),
            resource_name=str(
                value.get("resource_name", "VeraMesh Workstation")
            ),
        )

    @staticmethod
    def _https_url(value: str, name: str):
        if not isinstance(value, str) or not value:
            raise PublicGatewayConfigError(f"{name} is required")
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise PublicGatewayConfigError(
                f"{name} must be an absolute HTTPS URL"
            )
        if parsed.username or parsed.password:
            raise PublicGatewayConfigError(
                f"{name} must not contain userinfo"
            )
        return parsed

    @property
    def resource_metadata_url(self) -> str:
        public = urlsplit(self.public_mcp_url)
        return (
            f"{public.scheme}://{public.netloc}"
            f"/.well-known/oauth-protected-resource/mcp"
        )


@dataclass
class PublicGatewayHTTPApp:
    app: Any
    bundle: PublicMCPBundle
    session_manager: Any
    config: PublicGatewayHTTPConfig


def build_public_gateway_http_app(
    runtime: ControllerRuntime,
    *,
    token_verifier: Any,
    config: PublicGatewayHTTPConfig,
) -> PublicGatewayHTTPApp:
    """Build the NAS-facing public MCP resource server.

    TLS termination and OAuth token issuance are external deployment concerns.
    This app remains a resource server: it validates bearer tokens through the
    injected verifier, publishes RFC 9728 metadata, and forwards scoped tools
    into VeraPort.
    """

    try:
        from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
        from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
        from mcp.server.auth.routes import create_protected_resource_routes
        from mcp.server.streamable_http_manager import (
            StreamableHTTPSessionManager,
        )
        from mcp.server.transport_security import TransportSecuritySettings
        from pydantic import AnyHttpUrl
        from starlette.applications import Starlette
        from starlette.middleware.authentication import AuthenticationMiddleware
        from starlette.routing import Mount
    except ImportError as exc:
        raise RuntimeError(
            "public gateway requires the project mcp extra"
        ) from exc

    bundle = build_public_mcp_protocol(
        runtime,
        resource_metadata_url=config.resource_metadata_url,
        issuer_url=config.issuer_url,
    )

    manager = StreamableHTTPSessionManager(
        app=bundle.server,
        json_response=True,
        stateless=True,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(config.allowed_hosts),
            allowed_origins=list(config.allowed_origins),
        ),
    )

    # AuthenticationMiddleware must run before AuthContextMiddleware so the
    # SDK request-local context contains the verified AccessToken. Missing
    # credentials are intentionally allowed through: tools/list needs to be
    # discoverable pre-link so ChatGPT can see each OAuth security scheme.
    mcp_endpoint = AuthenticationMiddleware(
        AuthContextMiddleware(manager.handle_request),
        backend=BearerAuthBackend(token_verifier),
    )

    resource_url = AnyHttpUrl(config.public_mcp_url)
    issuer_url = AnyHttpUrl(config.issuer_url)
    routes = [
        *create_protected_resource_routes(
            resource_url=resource_url,
            authorization_servers=[issuer_url],
            scopes_supported=sorted(SCOPES),
            resource_name=config.resource_name,
        ),
        Mount("/mcp", app=mcp_endpoint),
    ]

    @asynccontextmanager
    async def lifespan(_app):
        try:
            async with manager.run():
                yield
        finally:
            try:
                await bundle.close()
            finally:
                try:
                    await runtime.close()
                finally:
                    close_verifier = getattr(token_verifier, "aclose", None)
                    if close_verifier is not None:
                        await close_verifier()

    app = Starlette(routes=routes, lifespan=lifespan)
    return PublicGatewayHTTPApp(
        app=app,
        bundle=bundle,
        session_manager=manager,
        config=config,
    )
