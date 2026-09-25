from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")

from veraport_agent.public_http import (
    PublicGatewayConfigError,
    PublicGatewayHTTPConfig,
    build_public_gateway_http_app,
)


class RuntimeStub:
    def __init__(self):
        self.config = type(
            "Config",
            (),
            {
                "requested_capabilities": frozenset({"fs.read"}),
                "gateway_operations": frozenset({
                    "lane.open",
                    "lane.close",
                    "fs.read_text",
                }),
            },
        )()

    async def close(self):
        self.closed = True


class VerifierStub:
    async def verify_token(self, token):
        return None


def config(**overrides):
    value = {
        "schema": "VERAMESH_PUBLIC_GATEWAY_HTTP_V1",
        "public_mcp_url": "https://mesh.example/mcp",
        "issuer_url": "https://login.example",
        "allowed_hosts": ["mesh.example"],
        "allowed_origins": [],
        "bind_host": "127.0.0.1",
        "bind_port": 17446,
        "resource_name": "VeraMesh Workstation",
    }
    value.update(overrides)
    return PublicGatewayHTTPConfig.from_dict(value)


def test_public_http_config_is_https_host_bound_and_loopback_only():
    cfg = config()
    assert cfg.resource_metadata_url == (
        "https://mesh.example/.well-known/oauth-protected-resource/mcp"
    )

    with pytest.raises(PublicGatewayConfigError, match="HTTPS"):
        config(public_mcp_url="http://mesh.example/mcp")
    with pytest.raises(PublicGatewayConfigError, match="exactly /mcp"):
        config(public_mcp_url="https://mesh.example/other")
    with pytest.raises(PublicGatewayConfigError, match="include"):
        config(allowed_hosts=["other.example"])
    with pytest.raises(PublicGatewayConfigError, match="loopback"):
        config(bind_host="0.0.0.0")


@pytest.mark.asyncio
async def test_protected_resource_metadata_is_public_and_exact():
    runtime = RuntimeStub()
    built = build_public_gateway_http_app(
        runtime,
        token_verifier=VerifierStub(),
        config=config(),
    )
    transport = httpx.ASGITransport(app=built.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://mesh.example",
    ) as client:
        response = await client.get(
            "/.well-known/oauth-protected-resource/mcp"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["resource"] == "https://mesh.example/mcp"
    assert body["authorization_servers"] == ["https://login.example/"]
    assert set(body["scopes_supported"]) == {
        "computer.profile",
        "computer.read",
        "computer.write",
        "computer.process",
    }


@pytest.mark.asyncio
async def test_mcp_transport_rejects_unapproved_host_before_protocol():
    runtime = RuntimeStub()
    built = build_public_gateway_http_app(
        runtime,
        token_verifier=VerifierStub(),
        config=config(),
    )

    async with built.app.router.lifespan_context(built.app):
        transport = httpx.ASGITransport(app=built.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://evil.example",
        ) as client:
            response = await client.post(
                "/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {
                            "name": "test",
                            "version": "1",
                        },
                    },
                },
                headers={"accept": "application/json, text/event-stream"},
            )

    assert response.status_code == 421
    assert response.text == "Invalid Host header"
