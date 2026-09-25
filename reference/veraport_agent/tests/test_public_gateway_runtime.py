from __future__ import annotations

import json
from pathlib import Path

import pytest

from veraport_agent.public_gateway_runtime import (
    PublicGatewayRuntimeError,
    build_runtime,
)


def write(path: Path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def controller(tmp_path: Path):
    return write(
        tmp_path / "controller.json",
        {
            "schema": "VERAPORT_CONTROLLER_MCP_CONFIG_V1",
            "controller_key": str(tmp_path / "controller.pem"),
            "tls_ca": str(tmp_path / "ca.pem"),
            "workstation_public_key": str(tmp_path / "workstation.pem"),
            "requested_capabilities": ["fs.read"],
            "gateway_operations": [
                "lane.list",
                "lane.open",
                "lane.close",
                "fs.read_text",
            ],
            "endpoints": [
                {
                    "endpoint_id": "lappy",
                    "mode": "DIRECT_STREAM",
                    "host": "100.64.0.2",
                    "port": 17444,
                    "server_hostname": "lappy.internal",
                    "durable_idempotency": True,
                }
            ],
        },
    )


def http(tmp_path: Path, **overrides):
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
    return write(tmp_path / "http.json", value)


def oauth(tmp_path: Path, **overrides):
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
    return write(tmp_path / "oauth.json", value)


def test_runtime_build_cross_binds_public_resource_and_issuer(tmp_path: Path):
    built = build_runtime(
        controller_config_path=controller(tmp_path),
        http_config_path=http(tmp_path),
        oauth_config_path=oauth(tmp_path),
    )
    assert built.http.config.bind_host == "127.0.0.1"
    assert built.http.config.public_mcp_url == "https://mesh.example/mcp"
    assert built.verifier.config.expected_resource == (
        built.http.config.public_mcp_url
    )
    assert built.verifier.config.expected_issuer == (
        built.http.config.issuer_url
    )


def test_runtime_rejects_cross_config_issuer_mismatch(tmp_path: Path):
    with pytest.raises(PublicGatewayRuntimeError, match="expected_issuer"):
        build_runtime(
            controller_config_path=controller(tmp_path),
            http_config_path=http(tmp_path),
            oauth_config_path=oauth(
                tmp_path,
                expected_issuer="https://other.example",
            ),
        )


def test_runtime_rejects_cross_config_resource_mismatch(tmp_path: Path):
    with pytest.raises(PublicGatewayRuntimeError, match="expected_resource"):
        build_runtime(
            controller_config_path=controller(tmp_path),
            http_config_path=http(tmp_path),
            oauth_config_path=oauth(
                tmp_path,
                expected_resource="https://other.example/mcp",
            ),
        )
