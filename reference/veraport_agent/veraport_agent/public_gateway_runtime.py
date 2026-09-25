from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .controller_config import ControllerConfig
from .controller_runtime import ControllerRuntime
from .oauth_verifier import (
    IntrospectionVerifierConfig,
    OAuthVerifierConfigError,
    RFC7662TokenVerifier,
)
from .public_http import (
    PublicGatewayConfigError,
    PublicGatewayHTTPApp,
    PublicGatewayHTTPConfig,
    build_public_gateway_http_app,
)


class PublicGatewayRuntimeError(RuntimeError):
    code = "PUBLIC_GATEWAY_RUNTIME_ERROR"


@dataclass
class PublicGatewayRuntime:
    controller: ControllerRuntime
    verifier: RFC7662TokenVerifier
    http: PublicGatewayHTTPApp


def _load_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PublicGatewayRuntimeError(
            f"cannot read JSON config {source}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise PublicGatewayRuntimeError(
            f"JSON config must be an object: {source}"
        )
    return value


def build_runtime(
    *,
    controller_config_path: str | Path,
    http_config_path: str | Path,
    oauth_config_path: str | Path,
) -> PublicGatewayRuntime:
    controller_config = ControllerConfig.load(controller_config_path)
    http_config = PublicGatewayHTTPConfig.from_dict(
        _load_json(http_config_path)
    )
    oauth_config = IntrospectionVerifierConfig.from_dict(
        _load_json(oauth_config_path)
    )

    if oauth_config.expected_issuer != http_config.issuer_url.rstrip("/"):
        raise PublicGatewayRuntimeError(
            "OAuth expected_issuer must exactly match public HTTP issuer_url"
        )
    if oauth_config.expected_resource != http_config.public_mcp_url.rstrip("/"):
        raise PublicGatewayRuntimeError(
            "OAuth expected_resource must exactly match public_mcp_url"
        )

    controller = ControllerRuntime(controller_config)
    verifier = RFC7662TokenVerifier(oauth_config)
    http = build_public_gateway_http_app(
        controller,
        token_verifier=verifier,
        config=http_config,
    )
    return PublicGatewayRuntime(
        controller=controller,
        verifier=verifier,
        http=http,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the loopback VeraMesh public MCP resource server. "
            "TLS/public exposure must be provided by a reviewed reverse proxy."
        )
    )
    parser.add_argument("--controller-config", required=True)
    parser.add_argument("--http-config", required=True)
    parser.add_argument("--oauth-config", required=True)
    parser.add_argument(
        "--log-level",
        choices=["critical", "error", "warning", "info", "debug"],
        default="info",
    )
    args = parser.parse_args()

    runtime = build_runtime(
        controller_config_path=args.controller_config,
        http_config_path=args.http_config,
        oauth_config_path=args.oauth_config,
    )

    try:
        import uvicorn
    except ImportError as exc:
        raise PublicGatewayRuntimeError(
            "public gateway requires the project mcp extra"
        ) from exc

    # Deliberately loopback-only. The configured Synology reverse proxy owns
    # TLS termination and stable public ingress; this process never binds WAN.
    uvicorn.run(
        runtime.http.app,
        host=runtime.http.config.bind_host,
        port=runtime.http.config.bind_port,
        log_level=args.log_level,
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
