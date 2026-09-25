from __future__ import annotations

from types import SimpleNamespace

import pytest

mcp = pytest.importorskip("mcp")
from mcp import types

from veraport_agent.public_mcp import build_public_mcp_protocol


class GatewayStub:
    async def call_operation(self, operation, **body):
        return {"ok": True, "result": {"operation": operation}}


class RuntimeStub:
    def __init__(self):
        self.config = SimpleNamespace(
            requested_capabilities=frozenset({"fs.read"}),
            gateway_operations=frozenset({
                "lane.open",
                "lane.close",
                "fs.read_text",
            }),
        )
        self.gateway = GatewayStub()
        self.opened = []
        self.closed = []

    async def machine_info(self):
        return {"schema": "VERAPORT_MCP_MACHINE_INFO_V1"}

    async def open_lane(self, **kwargs):
        self.opened.append(dict(kwargs))
        return {
            "ok": True,
            "result": {
                "lane_id": kwargs["lane_id"],
                "fencing_token": 9,
            },
        }

    async def close_lane(self, **kwargs):
        self.closed.append(dict(kwargs))
        return {"ok": True, "result": {"closed": True}}

    async def read_text(self, **kwargs):
        return {"ok": True, "result": {"content": "hello"}}

    async def ensure_started(self):
        return None


def _list_request():
    return types.ListToolsRequest(method="tools/list")


def _call_request(name, arguments=None):
    return types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(
            name=name,
            arguments=arguments or {},
        ),
    )


@pytest.mark.asyncio
async def test_public_tool_descriptors_emit_standard_and_meta_security_schemes():
    runtime = RuntimeStub()
    bundle = build_public_mcp_protocol(
        runtime,
        resource_metadata_url=(
            "https://mesh.example/.well-known/oauth-protected-resource"
        ),
        issuer_url="https://issuer.example",
        token_getter=lambda: None,
    )
    wrapped = await bundle.server.request_handlers[
        types.ListToolsRequest
    ](_list_request())
    payload = wrapped.root
    descriptors = {
        tool.name: tool.model_dump(by_alias=True, exclude_none=True)
        for tool in payload.tools
    }

    assert "lane_open" not in descriptors
    read = descriptors["read_file"]
    assert read["securitySchemes"] == [{
        "type": "oauth2",
        "scopes": ["computer.read"],
    }]
    assert read["_meta"]["securitySchemes"] == read["securitySchemes"]
    assert read["annotations"]["readOnlyHint"] is True
    assert "lane_id" not in read["inputSchema"]["properties"]
    assert "fencing_token" not in read["inputSchema"]["properties"]


@pytest.mark.asyncio
async def test_unauthenticated_tool_call_returns_chatgpt_oauth_challenge():
    runtime = RuntimeStub()
    bundle = build_public_mcp_protocol(
        runtime,
        resource_metadata_url=(
            "https://mesh.example/.well-known/oauth-protected-resource"
        ),
        issuer_url="https://issuer.example",
        token_getter=lambda: None,
    )
    wrapped = await bundle.server.request_handlers[
        types.CallToolRequest
    ](_call_request("computer_info"))
    result = wrapped.root

    assert result.isError is True
    assert "Link your VeraMesh account" in result.content[0].text
    challenge = result.meta["mcp/www_authenticate"][0]
    assert 'error="invalid_token"' in challenge
    assert "resource_metadata=" in challenge


@pytest.mark.asyncio
async def test_scope_is_enforced_before_veraport_and_subject_binds_actor():
    runtime = RuntimeStub()
    token = SimpleNamespace(
        subject="patrick",
        scopes=["computer.profile"],
    )
    bundle = build_public_mcp_protocol(
        runtime,
        resource_metadata_url=(
            "https://mesh.example/.well-known/oauth-protected-resource"
        ),
        issuer_url="https://issuer.example",
        token_getter=lambda: token,
    )

    denied = await bundle.server.request_handlers[
        types.CallToolRequest
    ](_call_request("read_file", {"path": r"C:\Users\Patrick\x.txt"}))
    denied_result = denied.root
    assert denied_result.isError is True
    assert "computer.read" in denied_result.content[0].text
    assert runtime.opened == []

    token.scopes.append("computer.read")
    allowed = await bundle.server.request_handlers[
        types.CallToolRequest
    ](_call_request("read_file", {"path": r"C:\Users\Patrick\x.txt"}))
    allowed_result = allowed.root
    assert allowed_result.isError is False
    assert '"content": "hello"' in allowed_result.content[0].text
    assert len(runtime.opened) == 1
    assert list(bundle.facades) == ["https://issuer.example|patrick"]


@pytest.mark.asyncio
async def test_missing_resource_owner_subject_is_not_replaced_with_client_id():
    runtime = RuntimeStub()
    token = SimpleNamespace(
        subject=None,
        client_id="chatgpt-client",
        scopes=["computer.profile"],
    )
    bundle = build_public_mcp_protocol(
        runtime,
        resource_metadata_url=(
            "https://mesh.example/.well-known/oauth-protected-resource"
        ),
        issuer_url="https://issuer.example",
        token_getter=lambda: token,
    )
    wrapped = await bundle.server.request_handlers[
        types.CallToolRequest
    ](_call_request("computer_info"))
    assert wrapped.root.isError is True
    assert bundle.facades == {}
