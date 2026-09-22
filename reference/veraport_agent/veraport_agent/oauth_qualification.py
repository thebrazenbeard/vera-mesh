from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


class OAuthQualificationError(RuntimeError):
    code = "OAUTH_QUALIFICATION_ERROR"


@dataclass(frozen=True)
class OAuthQualification:
    issuer: str
    metadata_url: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    client_id_metadata_document_supported: bool
    token_endpoint_auth_methods_supported: tuple[str, ...]
    code_challenge_methods_supported: tuple[str, ...]
    supported_client_modes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "VERAMESH_OAUTH_QUALIFICATION_V1",
            "issuer": self.issuer,
            "metadata_url": self.metadata_url,
            "authorization_endpoint": self.authorization_endpoint,
            "token_endpoint": self.token_endpoint,
            "registration_endpoint": self.registration_endpoint,
            "client_id_metadata_document_supported": (
                self.client_id_metadata_document_supported
            ),
            "token_endpoint_auth_methods_supported": list(
                self.token_endpoint_auth_methods_supported
            ),
            "code_challenge_methods_supported": list(
                self.code_challenge_methods_supported
            ),
            "supported_client_modes": list(self.supported_client_modes),
            "static_metadata_pass": True,
            "non_claims": [
                "does not prove authorization response issuer behavior",
                "does not prove resource parameter preservation",
                "does not prove token audience/resource binding",
                "does not prove token subject/scopes are suitable for VeraMesh",
                "does not register a client or mutate the authorization server",
            ],
        }


def _https_url(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise OAuthQualificationError(f"{name} is required")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise OAuthQualificationError(f"{name} must be absolute HTTPS")
    if parsed.username or parsed.password or parsed.fragment:
        raise OAuthQualificationError(
            f"{name} contains unsupported URL components"
        )
    return value.rstrip("/")


def metadata_url_for_issuer(issuer: str) -> str:
    issuer = _https_url(issuer, "issuer")
    parsed = urlsplit(issuer)
    path = parsed.path.rstrip("/")
    return (
        f"{parsed.scheme}://{parsed.netloc}"
        f"/.well-known/oauth-authorization-server{path}"
    )


def qualify_metadata(
    value: dict[str, Any],
    *,
    expected_issuer: str,
    metadata_url: str | None = None,
) -> OAuthQualification:
    if not isinstance(value, dict):
        raise OAuthQualificationError(
            "authorization metadata must be a JSON object"
        )
    issuer = _https_url(value.get("issuer"), "issuer")
    expected = _https_url(expected_issuer, "expected_issuer")
    if issuer != expected:
        raise OAuthQualificationError(
            f"issuer mismatch: expected {expected!r}, got {issuer!r}"
        )

    authorization_endpoint = _https_url(
        value.get("authorization_endpoint"),
        "authorization_endpoint",
    )
    token_endpoint = _https_url(
        value.get("token_endpoint"),
        "token_endpoint",
    )

    pkce = value.get("code_challenge_methods_supported")
    if (
        not isinstance(pkce, list)
        or not all(isinstance(item, str) for item in pkce)
        or "S256" not in pkce
    ):
        raise OAuthQualificationError(
            "authorization metadata must advertise S256 PKCE"
        )

    auth_methods_raw = value.get(
        "token_endpoint_auth_methods_supported",
        [],
    )
    if not isinstance(auth_methods_raw, list) or not all(
        isinstance(item, str) for item in auth_methods_raw
    ):
        raise OAuthQualificationError(
            "token_endpoint_auth_methods_supported must be a string list"
        )
    auth_methods = tuple(auth_methods_raw)

    cimd = value.get("client_id_metadata_document_supported", False)
    if type(cimd) is not bool:
        raise OAuthQualificationError(
            "client_id_metadata_document_supported must be exact bool"
        )

    registration_endpoint = value.get("registration_endpoint")
    if registration_endpoint is not None:
        registration_endpoint = _https_url(
            registration_endpoint,
            "registration_endpoint",
        )

    modes: list[str] = []
    if cimd and any(
        method in {"none", "private_key_jwt"}
        for method in auth_methods
    ):
        modes.append("CIMD")
    if registration_endpoint is not None:
        modes.append("DCR")
    # A preconfigured client is valid only with an explicit operator binding;
    # metadata alone cannot prove one exists, so it is not promoted here.
    if not modes:
        raise OAuthQualificationError(
            "metadata exposes neither compatible CIMD nor DCR"
        )

    return OAuthQualification(
        issuer=issuer,
        metadata_url=(
            metadata_url
            if metadata_url is not None
            else metadata_url_for_issuer(expected)
        ),
        authorization_endpoint=authorization_endpoint,
        token_endpoint=token_endpoint,
        registration_endpoint=registration_endpoint,
        client_id_metadata_document_supported=cimd,
        token_endpoint_auth_methods_supported=auth_methods,
        code_challenge_methods_supported=tuple(pkce),
        supported_client_modes=tuple(modes),
    )


def fetch_and_qualify(
    issuer: str,
    *,
    timeout_s: float = 10.0,
) -> OAuthQualification:
    metadata_url = metadata_url_for_issuer(issuer)
    request = urllib.request.Request(
        metadata_url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "VeraMesh-OAuth-Qualification/1",
        },
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=float(timeout_s),
        ) as response:
            if response.status != 200:
                raise OAuthQualificationError(
                    f"metadata endpoint returned HTTP {response.status}"
                )
            payload = response.read(262_145)
    except OAuthQualificationError:
        raise
    except Exception as exc:
        raise OAuthQualificationError(
            f"cannot fetch authorization metadata: {exc}"
        ) from exc
    if len(payload) > 262_144:
        raise OAuthQualificationError(
            "authorization metadata exceeds 262144 bytes"
        )
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OAuthQualificationError(
            "authorization metadata is not strict UTF-8 JSON"
        ) from exc
    return qualify_metadata(
        value,
        expected_issuer=issuer,
        metadata_url=metadata_url,
    )
