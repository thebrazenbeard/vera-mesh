from __future__ import annotations

from types import SimpleNamespace

import pytest

mcp = pytest.importorskip("mcp")

from veraport_agent.oauth_verifier import (
    IntrospectionVerifierConfig,
    OAuthVerifierConfigError,
    RFC7662TokenVerifier,
)


class Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def config(**overrides):
    value = {
        "schema": "VERAMESH_OAUTH_INTROSPECTION_V1",
        "introspection_endpoint": "https://login.example/introspect",
        "client_id": "veramesh-resource-server",
        "client_secret_env": "VERAMESH_OAUTH_SECRET",
        "expected_issuer": "https://login.example",
        "expected_resource": "https://mesh.example/mcp",
        "client_auth_method": "client_secret_basic",
        "timeout_s": 5,
    }
    value.update(overrides)
    return IntrospectionVerifierConfig.from_dict(value)


def active(**overrides):
    value = {
        "active": True,
        "iss": "https://login.example",
        "sub": "patrick",
        "client_id": "chatgpt-client",
        "scope": "computer.profile computer.read",
        "exp": 2000,
        "aud": "https://mesh.example/mcp",
    }
    value.update(overrides)
    return value


def test_config_is_https_and_secret_is_indirect():
    cfg = config()
    assert cfg.client_secret_env == "VERAMESH_OAUTH_SECRET"

    with pytest.raises(OAuthVerifierConfigError, match="HTTPS"):
        config(introspection_endpoint="http://login.example/introspect")
    with pytest.raises(OAuthVerifierConfigError, match="environment-variable"):
        config(client_secret_env="NOT VALID!")


@pytest.mark.asyncio
async def test_valid_introspection_returns_resource_owner_token():
    client = Client(Response(payload=active()))
    verifier = RFC7662TokenVerifier(
        config(),
        environ={"VERAMESH_OAUTH_SECRET": "secret"},
        client=client,
        now_s=lambda: 1000,
    )

    token = await verifier.verify_token("opaque-token")

    assert token is not None
    assert token.subject == "patrick"
    assert token.client_id == "chatgpt-client"
    assert token.resource == "https://mesh.example/mcp"
    assert token.scopes == ["computer.profile", "computer.read"]
    url, kwargs = client.calls[0]
    assert url == "https://login.example/introspect"
    assert kwargs["auth"] == ("veramesh-resource-server", "secret")
    assert "client_secret" not in kwargs["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        active(active=False),
        active(iss="https://evil.example"),
        active(sub=""),
        active(client_id=""),
        active(exp=999),
        active(aud="https://other.example/mcp"),
        active(aud=["https://other.example/mcp"]),
        active(aud=None, resource="https://other.example/mcp"),
        active(scope=None),
    ],
)
async def test_introspection_fails_closed_on_identity_resource_or_scope(payload):
    verifier = RFC7662TokenVerifier(
        config(),
        environ={"VERAMESH_OAUTH_SECRET": "secret"},
        client=Client(Response(payload=payload)),
        now_s=lambda: 1000,
    )
    assert await verifier.verify_token("token") is None


@pytest.mark.asyncio
async def test_resource_field_or_audience_list_can_bind_exact_resource():
    for payload in [
        active(aud=None, resource="https://mesh.example/mcp"),
        active(aud=["x", "https://mesh.example/mcp"]),
    ]:
        verifier = RFC7662TokenVerifier(
            config(),
            environ={"VERAMESH_OAUTH_SECRET": "secret"},
            client=Client(Response(payload=payload)),
            now_s=lambda: 1000,
        )
        assert await verifier.verify_token("token") is not None


@pytest.mark.asyncio
async def test_missing_secret_fails_before_network():
    client = Client(Response(payload=active()))
    verifier = RFC7662TokenVerifier(
        config(),
        environ={},
        client=client,
        now_s=lambda: 1000,
    )
    with pytest.raises(OAuthVerifierConfigError, match="missing OAuth"):
        await verifier.verify_token("token")
    assert client.calls == []


@pytest.mark.asyncio
async def test_client_secret_post_is_explicit_and_network_errors_reject_token():
    client = Client(Response(payload=active()))
    verifier = RFC7662TokenVerifier(
        config(client_auth_method="client_secret_post"),
        environ={"VERAMESH_OAUTH_SECRET": "secret"},
        client=client,
        now_s=lambda: 1000,
    )
    assert await verifier.verify_token("token") is not None
    _, kwargs = client.calls[0]
    assert "auth" not in kwargs
    assert kwargs["data"]["client_id"] == "veramesh-resource-server"
    assert kwargs["data"]["client_secret"] == "secret"

    broken = RFC7662TokenVerifier(
        config(),
        environ={"VERAMESH_OAUTH_SECRET": "secret"},
        client=Client(RuntimeError("network down")),
        now_s=lambda: 1000,
    )
    assert await broken.verify_token("token") is None


@pytest.mark.asyncio
async def test_non_200_and_invalid_json_reject_token():
    verifier = RFC7662TokenVerifier(
        config(),
        environ={"VERAMESH_OAUTH_SECRET": "secret"},
        client=Client(Response(status_code=500, payload=active())),
        now_s=lambda: 1000,
    )
    assert await verifier.verify_token("token") is None

    verifier = RFC7662TokenVerifier(
        config(),
        environ={"VERAMESH_OAUTH_SECRET": "secret"},
        client=Client(Response(payload=ValueError("bad json"))),
        now_s=lambda: 1000,
    )
    assert await verifier.verify_token("token") is None
