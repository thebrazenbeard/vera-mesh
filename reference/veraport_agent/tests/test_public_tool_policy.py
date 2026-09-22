from __future__ import annotations

import json
from pathlib import Path

import pytest

from veraport_agent.public_tool_policy import (
    PUBLIC_TOOL_BY_NAME,
    oauth_challenge,
    public_policy_document,
)


def test_protocol_policy_snapshot_matches_executable_catalog():
    repo_root = Path(__file__).resolve().parents[3]
    snapshot = json.loads(
        (repo_root / "protocol/veraport/v1/public-tool-policy.json").read_text(
            encoding="utf-8"
        )
    )
    assert snapshot == public_policy_document()


def test_every_public_tool_has_explicit_oauth_and_annotations():
    for tool in PUBLIC_TOOL_BY_NAME.values():
        ext = tool.descriptor_extensions()
        assert ext["securitySchemes"] == [{
            "type": "oauth2",
            "scopes": list(tool.scopes),
        }]
        assert ext["_meta"]["securitySchemes"] == ext["securitySchemes"]
        assert set(ext["annotations"]) == {
            "readOnlyHint",
            "destructiveHint",
            "idempotentHint",
            "openWorldHint",
        }


def test_public_catalog_never_exposes_lane_or_fence_tools():
    assert not any(
        tool.operation.startswith("lane.")
        for tool in PUBLIC_TOOL_BY_NAME.values()
    )


def test_oauth_challenge_matches_chatgpt_metadata_key_and_rejects_injection():
    meta = oauth_challenge(
        "https://mesh.example/.well-known/oauth-protected-resource"
    )
    value = meta["mcp/www_authenticate"][0]
    assert value.startswith("Bearer resource_metadata=")
    assert 'error="insufficient_scope"' in value
    assert 'error_description="' in value

    with pytest.raises(ValueError, match="unsafe"):
        oauth_challenge(
            'https://mesh.example/"bad',
        )
