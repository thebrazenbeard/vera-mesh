from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


class OAuthVerifierConfigError(ValueError):
    code = "OAUTH_VERIFIER_CONFIG_ERROR"


@dataclass(frozen=True)
class IntrospectionVerifierConfig:
    introspection_endpoint: str
    client_id: str
    client_secret_env: str
    expected_issuer: str
    expected_resource: str
    client_auth_method: str = "client_secret_basic"
    timeout_s: float = 5.0

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "IntrospectionVerifierConfig":
        if not isinstance(value, dict):
            raise OAuthVerifierConfigError("config must be an object")
        if value.get("schema") != "VERAMESH_OAUTH_INTROSPECTION_V1":
            raise OAuthVerifierConfigError("wrong introspection config schema")
        endpoint = cls._https(
            value.get("introspection_endpoint"),
            "introspection_endpoint",
        )
        issuer = cls._https(value.get("expected_issuer"), "expected_issuer")
        resource = cls._https(
            value.get("expected_resource"),
            "expected_resource",
        )
        client_id = str(value.get("client_id", "")).strip()
        secret_env = str(value.get("client_secret_env", "")).strip()
        if not client_id:
            raise OAuthVerifierConfigError("client_id is required")
        if not secret_env or not secret_env.replace("_", "").isalnum():
            raise OAuthVerifierConfigError(
                "client_secret_env must be an environment-variable name"
            )
        method = str(
            value.get("client_auth_method", "client_secret_basic")
        )
        if method not in {"client_secret_basic", "client_secret_post"}:
            raise OAuthVerifierConfigError(
                "client_auth_method must be client_secret_basic or client_secret_post"
            )
        timeout_s = float(value.get("timeout_s", 5.0))
        if not 0.1 <= timeout_s <= 30.0:
            raise OAuthVerifierConfigError("timeout_s outside 0.1..30")
        return cls(
            introspection_endpoint=endpoint,
            client_id=client_id,
            client_secret_env=secret_env,
            expected_issuer=issuer,
            expected_resource=resource,
            client_auth_method=method,
            timeout_s=timeout_s,
        )

    @staticmethod
    def _https(value: Any, name: str) -> str:
        if not isinstance(value, str) or not value:
            raise OAuthVerifierConfigError(f"{name} is required")
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise OAuthVerifierConfigError(
                f"{name} must be an absolute HTTPS URL without userinfo/fragment"
            )
        return value.rstrip("/")


class RFC7662TokenVerifier:
    """Fail-closed OAuth 2.0 token introspection for the public VeraMesh gateway."""

    def __init__(
        self,
        config: IntrospectionVerifierConfig,
        *,
        environ: dict[str, str] | None = None,
        client: Any | None = None,
        now_s: Any | None = None,
    ) -> None:
        self.config = config
        self.environ = os.environ if environ is None else environ
        self._client = client
        self._owns_client = client is None
        self._now_s = now_s or (lambda: int(time.time()))

    def _secret(self) -> str:
        secret = self.environ.get(self.config.client_secret_env)
        if not isinstance(secret, str) or not secret:
            raise OAuthVerifierConfigError(
                f"missing OAuth client secret env: {self.config.client_secret_env}"
            )
        return secret

    async def _http_client(self):
        if self._client is not None:
            return self._client
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("OAuth introspection requires the mcp extra") from exc
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                self.config.timeout_s,
                connect=min(self.config.timeout_s, 5.0),
            ),
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
            ),
            verify=True,
            follow_redirects=False,
        )
        return self._client

    async def verify_token(self, token: str):
        if not isinstance(token, str) or not token:
            return None
        try:
            from mcp.server.auth.provider import AccessToken
        except ImportError as exc:
            raise RuntimeError("OAuth introspection requires mcp>=1.27.2,<2") from exc

        secret = self._secret()
        client = await self._http_client()
        data = {
            "token": token,
            "token_type_hint": "access_token",
        }
        kwargs: dict[str, Any] = {
            "data": data,
            "headers": {
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        }
        if self.config.client_auth_method == "client_secret_basic":
            kwargs["auth"] = (self.config.client_id, secret)
        else:
            data["client_id"] = self.config.client_id
            data["client_secret"] = secret

        try:
            response = await client.post(
                self.config.introspection_endpoint,
                **kwargs,
            )
        except Exception:
            return None
        if response.status_code != 200:
            return None
        try:
            value = response.json()
        except Exception:
            return None
        if not isinstance(value, dict) or value.get("active") is not True:
            return None

        issuer = value.get("iss")
        if issuer != self.config.expected_issuer:
            return None
        subject = value.get("sub")
        if not isinstance(subject, str) or not subject:
            return None
        client_id = value.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            return None

        expires_at = value.get("exp")
        if type(expires_at) is not int or expires_at <= self._now_s():
            return None

        if not self._resource_matches(value):
            return None

        scopes = self._scopes(value.get("scope"))
        if scopes is None:
            return None

        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=expires_at,
            resource=self.config.expected_resource,
            subject=subject,
            claims=dict(value),
        )

    def _resource_matches(self, value: dict[str, Any]) -> bool:
        expected = self.config.expected_resource
        resource = value.get("resource")
        if isinstance(resource, str) and resource == expected:
            return True
        aud = value.get("aud")
        if isinstance(aud, str):
            return aud == expected
        if isinstance(aud, list) and all(isinstance(item, str) for item in aud):
            return expected in aud
        return False

    @staticmethod
    def _scopes(value: Any) -> list[str] | None:
        if isinstance(value, str):
            return [item for item in value.split() if item]
        if isinstance(value, list) and all(
            isinstance(item, str) and item for item in value
        ):
            return list(dict.fromkeys(value))
        return None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            close = getattr(self._client, "aclose", None)
            if close is not None:
                await close()
            self._client = None
