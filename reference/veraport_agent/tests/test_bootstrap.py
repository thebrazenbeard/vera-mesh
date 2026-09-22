from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from veraport_agent.bootstrap import BootstrapError, prepare_local_bootstrap
from veraport_agent.controller_config import ControllerConfig
from veraport_agent.service_config import WindowsServiceConfig
from veraport_agent.tunnel_runtime_service import TunnelRuntimeServiceConfig


def make_inputs(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    tunnel = tmp_path / "tunnel-client.exe"
    tunnel.write_text("placeholder", encoding="utf-8")
    key = tmp_path / "runtime.key"
    key.write_text("runtime-secret", encoding="utf-8")
    return allowed, tunnel, key


def test_bootstrap_defaults_to_filesystem_only_and_no_effect_claims(tmp_path):
    allowed, tunnel, key = make_inputs(tmp_path)
    root = tmp_path / "VeraMesh"
    manifest = prepare_local_bootstrap(
        root,
        allowed_roots=[allowed],
        tunnel_client=tunnel,
        tunnel_id="tunnel_0123456789abcdef",
        runtime_api_key_file=key,
        mcp_command="veraport-mcp-stdio",
        harden_windows_acl=False,
    )

    assert manifest["authority"]["process_enabled"] is False
    assert set(manifest["authority"]["capabilities"]) == {
        "fs.read",
        "fs.write",
    }
    assert not any(
        item.startswith("process.")
        for item in manifest["authority"]["gateway_operations"]
    )
    assert manifest["effects"] == {
        "services_installed": False,
        "services_started": False,
        "tunnel_created": False,
        "chatgpt_connector_registered": False,
    }

    service = WindowsServiceConfig.load(root / "veraport.json")
    controller = ControllerConfig.load(root / "controller.json")
    tunnel_cfg = TunnelRuntimeServiceConfig.load(root / "tunnel-runtime.json")
    assert service.allow_process_exec is False
    assert controller.requested_capabilities == frozenset(
        {"fs.read", "fs.write"}
    )
    assert tunnel_cfg.tunnel_id == "tunnel_0123456789abcdef"


def test_bootstrap_process_authority_requires_explicit_choice(tmp_path):
    allowed, tunnel, key = make_inputs(tmp_path)
    root = tmp_path / "VeraMesh"
    manifest = prepare_local_bootstrap(
        root,
        allowed_roots=[allowed],
        tunnel_client=tunnel,
        tunnel_id="tunnel_process",
        runtime_api_key_file=key,
        mcp_command="veraport-mcp-stdio",
        enable_process=True,
        harden_windows_acl=False,
    )
    assert manifest["authority"]["process_enabled"] is True
    assert {
        "process.exec",
        "process.inspect",
        "process.interact",
        "process.control",
    }.issubset(set(manifest["authority"]["capabilities"]))
    controller = ControllerConfig.load(root / "controller.json")
    assert "process.input" in controller.gateway_operations
    assert WindowsServiceConfig.load(root / "veraport.json").allow_process_exec


def test_bootstrap_refuses_missing_root_secret_or_tunnel_binary(tmp_path):
    allowed, tunnel, key = make_inputs(tmp_path)
    with pytest.raises(BootstrapError, match="allowed roots"):
        prepare_local_bootstrap(
            tmp_path / "a",
            allowed_roots=[tmp_path / "missing"],
            tunnel_client=tunnel,
            tunnel_id="tunnel_x",
            runtime_api_key_file=key,
            mcp_command="veraport-mcp-stdio",
            harden_windows_acl=False,
        )
    with pytest.raises(BootstrapError, match="tunnel-client"):
        prepare_local_bootstrap(
            tmp_path / "b",
            allowed_roots=[allowed],
            tunnel_client=tmp_path / "missing.exe",
            tunnel_id="tunnel_x",
            runtime_api_key_file=key,
            mcp_command="veraport-mcp-stdio",
            harden_windows_acl=False,
        )
    with pytest.raises(BootstrapError, match="runtime API key"):
        prepare_local_bootstrap(
            tmp_path / "c",
            allowed_roots=[allowed],
            tunnel_client=tunnel,
            tunnel_id="tunnel_x",
            runtime_api_key_file=tmp_path / "missing.key",
            mcp_command="veraport-mcp-stdio",
            harden_windows_acl=False,
        )


def test_bootstrap_refuses_overwrite_and_preserves_first_subject(tmp_path):
    allowed, tunnel, key = make_inputs(tmp_path)
    root = tmp_path / "VeraMesh"
    first = prepare_local_bootstrap(
        root,
        allowed_roots=[allowed],
        tunnel_client=tunnel,
        tunnel_id="tunnel_first",
        runtime_api_key_file=key,
        mcp_command="veraport-mcp-stdio",
        harden_windows_acl=False,
    )
    original = (root / "controller.json").read_bytes()
    with pytest.raises(Exception):
        prepare_local_bootstrap(
            root,
            allowed_roots=[allowed],
            tunnel_client=tunnel,
            tunnel_id="tunnel_second",
            runtime_api_key_file=key,
            mcp_command="veraport-mcp-stdio",
            harden_windows_acl=False,
        )
    assert (root / "controller.json").read_bytes() == original
    assert first["effects"]["services_installed"] is False


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL integration")
def test_bootstrap_hardens_generated_windows_material(tmp_path):
    from veraport_agent.windows_acl import (
        validate_private_directory,
        validate_private_file,
    )

    allowed, tunnel, key = make_inputs(tmp_path)
    root = tmp_path / "VeraMesh"
    # The external runtime-key file is part of the service material set, so put
    # it under the bootstrap root to let the expected ACL policy own it.
    key = root / "secrets" / "runtime.key"
    key.parent.mkdir(parents=True)
    key.write_text("runtime-secret", encoding="utf-8")
    from veraport_agent.windows_acl import (
        harden_private_directory,
        harden_private_file,
    )
    harden_private_directory(key.parent)
    harden_private_file(key)

    prepare_local_bootstrap(
        root,
        allowed_roots=[allowed],
        tunnel_client=tunnel,
        tunnel_id="tunnel_windows",
        runtime_api_key_file=key,
        mcp_command="veraport-mcp-stdio",
        harden_windows_acl=True,
    )
    validate_private_directory(root)
    for name in (
        "veraport.json",
        "controller.json",
        "tunnel-runtime.json",
        "bootstrap-manifest.json",
    ):
        validate_private_file(root / name)
