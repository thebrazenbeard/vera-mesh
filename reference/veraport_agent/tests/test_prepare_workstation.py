from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from veraport_agent.controller_config import ControllerConfig
from veraport_agent.prepare_workstation import (
    WorkstationPreparationError,
    prepare_workstation_service,
)
from veraport_agent.service_config import WindowsServiceConfig


def test_prepare_workstation_splits_controller_private_key(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    root = tmp_path / "service"
    export = tmp_path / "controller-export"

    manifest = prepare_workstation_service(
        root,
        export,
        allowed_roots=[allowed],
        bind_host="127.0.0.1",
        bind_port=17444,
        harden_windows_acl=False,
    )

    assert manifest["effects"] == {
        "windows_service_installed": False,
        "windows_service_started": False,
        "firewall_changed": False,
        "controller_export_transferred": False,
    }
    assert (
        manifest["separation"][
            "controller_private_key_present_in_service_identity"
        ]
        is False
    )
    assert not (root / "identity" / "controller-key.pem").exists()
    assert (export / "controller-key.pem").is_file()

    service = WindowsServiceConfig.load(root / "veraport.json")
    service.validate_runtime_files()
    assert service.bind_host == "127.0.0.1"
    assert service.allow_process_exec is False

    controller = ControllerConfig.load(export / "controller.json")
    assert controller.controller_key == (export / "controller-key.pem").resolve()
    assert controller.tls_ca == (export / "tls-ca.pem").resolve()
    assert controller.workstation_public_key == (
        export / "workstation-public.pem"
    ).resolve()
    assert controller.endpoints[0].host == "127.0.0.1"
    assert controller.requested_capabilities == frozenset(
        {"fs.read", "fs.write"}
    )
    assert "fs.search_content" in controller.gateway_operations


def test_prepare_workstation_process_authority_is_explicit(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    root = tmp_path / "service"
    export = tmp_path / "controller-export"

    manifest = prepare_workstation_service(
        root,
        export,
        allowed_roots=[allowed],
        enable_process=True,
        harden_windows_acl=False,
    )
    assert manifest["authority"]["process_enabled"] is True

    service = WindowsServiceConfig.load(root / "veraport.json")
    assert service.allow_process_exec is True

    controller = ControllerConfig.load(export / "controller.json")
    assert {
        "process.exec",
        "process.inspect",
        "process.interact",
        "process.control",
    }.issubset(controller.requested_capabilities)
    assert "process.input" in controller.gateway_operations


def test_prepare_workstation_non_loopback_requires_explicit_authority(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    with pytest.raises(
        WorkstationPreparationError,
        match="explicit allow_non_loopback_listener",
    ):
        prepare_workstation_service(
            tmp_path / "service",
            tmp_path / "export",
            allowed_roots=[allowed],
            bind_host="100.64.0.2",
            harden_windows_acl=False,
        )

    manifest = prepare_workstation_service(
        tmp_path / "service2",
        tmp_path / "export2",
        allowed_roots=[allowed],
        bind_host="100.64.0.2",
        allow_non_loopback_listener=True,
        harden_windows_acl=False,
    )
    assert manifest["authority"]["non_loopback_listener"] is True

    controller = ControllerConfig.load(
        tmp_path / "export2" / "controller.json"
    )
    assert controller.endpoints[0].host == "100.64.0.2"


@pytest.mark.parametrize("host", ["0.0.0.0", "::"])
def test_prepare_workstation_rejects_wildcard_controller_targets(
    tmp_path: Path,
    host: str,
):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    with pytest.raises(
        WorkstationPreparationError,
        match="concrete unicast",
    ):
        prepare_workstation_service(
            tmp_path / "service",
            tmp_path / "export",
            allowed_roots=[allowed],
            bind_host=host,
            allow_non_loopback_listener=True,
            harden_windows_acl=False,
        )


def test_prepare_workstation_refuses_existing_subjects(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    root = tmp_path / "service"
    root.mkdir()
    (root / "existing.txt").write_text("x", encoding="utf-8")

    with pytest.raises(
        WorkstationPreparationError,
        match="service root must be absent or empty",
    ):
        prepare_workstation_service(
            root,
            tmp_path / "export",
            allowed_roots=[allowed],
            harden_windows_acl=False,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL integration")
def test_prepare_workstation_hardens_service_materials_on_windows(
    tmp_path: Path,
):
    from veraport_agent.windows_acl import validate_service_materials

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    root = tmp_path / "service"
    export = tmp_path / "controller-export"

    prepare_workstation_service(
        root,
        export,
        allowed_roots=[allowed],
        harden_windows_acl=True,
    )
    service = WindowsServiceConfig.load(root / "veraport.json")
    validate_service_materials(root / "veraport.json", service)
