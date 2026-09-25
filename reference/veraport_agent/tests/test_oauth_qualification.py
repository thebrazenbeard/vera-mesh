from __future__ import annotations

import pytest

from veraport_agent.oauth_qualification import (
    OAuthQualificationError,
    metadata_url_for_issuer,
    qualify_metadata,
)


def valid():
    return {
        "issuer": "https://login.example",
        "authorization_endpoint": "https://login.example/authorize",
        "token_endpoint": "https://login.example/token",
        "registration_endpoint": "https://login.example/register",
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "none",
            "client_secret_basic",
        ],
    }


def test_metadata_url_preserves_issuer_path_per_rfc8414_shape():
    assert metadata_url_for_issuer("https://login.example") == (
        "https://login.example/.well-known/oauth-authorization-server"
    )
    assert metadata_url_for_issuer("https://login.example/tenant") == (
        "https://login.example/.well-known/oauth-authorization-server/tenant"
    )


def test_dcr_metadata_passes_only_with_s256_and_exact_issuer():
    result = qualify_metadata(
        valid(),
        expected_issuer="https://login.example",
    )
    assert result.supported_client_modes == ("DCR",)
    assert result.code_challenge_methods_supported == ("S256",)
    assert result.as_dict()["static_metadata_pass"] is True


def test_cimd_requires_supported_chatgpt_token_auth_method():
    value = valid()
    value.pop("registration_endpoint")
    value["client_id_metadata_document_supported"] = True
    result = qualify_metadata(
        value,
        expected_issuer="https://login.example",
    )
    assert result.supported_client_modes == ("CIMD",)

    value["token_endpoint_auth_methods_supported"] = [
        "client_secret_basic"
    ]
    with pytest.raises(
        OAuthQualificationError,
        match="neither compatible CIMD nor DCR",
    ):
        qualify_metadata(
            value,
            expected_issuer="https://login.example",
        )


def test_missing_s256_fails_closed():
    value = valid()
    value["code_challenge_methods_supported"] = ["plain"]
    with pytest.raises(OAuthQualificationError, match="S256"):
        qualify_metadata(
            value,
            expected_issuer="https://login.example",
        )


def test_issuer_or_endpoint_downgrade_fails_closed():
    value = valid()
    value["issuer"] = "https://other.example"
    with pytest.raises(OAuthQualificationError, match="issuer mismatch"):
        qualify_metadata(
            value,
            expected_issuer="https://login.example",
        )

    value = valid()
    value["token_endpoint"] = "http://login.example/token"
    with pytest.raises(OAuthQualificationError, match="absolute HTTPS"):
        qualify_metadata(
            value,
            expected_issuer="https://login.example",
        )
