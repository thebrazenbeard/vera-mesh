from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from veraport_agent.controller_recovery import (
    ControllerRecoveryError,
    ensure_readonly_controller,
)
from veraport_agent.provision import provision_local_pair


def fixture(tmp_path: Path):
    root = tmp_path / "VeraMesh"
    identity = root / "identity"
    identity.mkdir(parents=True)
    manifest = provision_local_pair(
        identity,
        capabilities={"fs.read", "fs.write"},
        harden_windows_acl=False,
    )
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    state = root / "state"
    state.mkdir()

    workstation = identity / "workstation.pem"
    workstation.write_bytes((identity / "workstation-key.pem").read_bytes())
    controllers = identity / "controllers.json"
    controllers.write_bytes((identity / "controller-trust.json").read_bytes())

    service = root / "veraport.json"
    service.write_text(
        json.dumps(
            {
                "bind_host": "127.0.0.1",
                "bind_port": 17444,
                "allowed_roots": [str(allowed)],
                "state_db": str(state / "agent.sqlite3"),
                "tls_cert": str(identity / "tls-cert.pem"),
                "tls_key": str(identity / "tls-key.pem"),
                "workstation_key": str(workstation),
                "controller_trust": str(controllers),
                "allow_process_exec": False,
                "allow_non_loopback_listener": False,
            }
        ),
        encoding="utf-8",
    )

    preferred = root / "controller" / "vera-controller-bootstrap.pem"
    preferred.parent.mkdir()
    preferred.write_bytes((identity / "controller-key.pem").read_bytes())
    generated = root / "controller" / "chatgpt-readonly-controller.pem"
    return {
        "root": root,
        "identity": identity,
        "service": service,
        "controllers": controllers,
        "preferred": preferred,
        "generated": generated,
        "original_principal": manifest["controller_principal"],
    }


def test_reuses_only_exact_readonly_enrolled_controller_without_trust_write(tmp_path):
    fx = fixture(tmp_path)
    trust = json.loads(fx["controllers"].read_text(encoding="utf-8"))
    trust["controllers"][0]["capabilities"] = ["fs.read"]
    fx["controllers"].write_text(
        json.dumps(trust, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    before = fx["controllers"].read_bytes()
    result = ensure_readonly_controller(
        service_config_path=fx["service"],
        preferred_controller_private_key_path=fx["preferred"],
        generated_controller_private_key_path=fx["generated"],
        harden_windows_acl=False,
    )
    assert result["mode"] == "REUSED_ENROLLED_CONTROLLER"
    assert result["service_restart_required"] is False
    assert result["controller_principal"] == fx["original_principal"]
    assert fx["controllers"].read_bytes() == before
    assert not fx["generated"].exists()


def test_broader_enrolled_controller_is_not_reused_for_chatgpt_tunnel(tmp_path):
    fx = fixture(tmp_path)
    result = ensure_readonly_controller(
        service_config_path=fx["service"],
        preferred_controller_private_key_path=fx["preferred"],
        generated_controller_private_key_path=fx["generated"],
        harden_windows_acl=False,
    )
    assert result["mode"] == "ENROLLED_NEW_READONLY_CONTROLLER"
    assert result["preferred_status"] == "enrolled_but_broader_than_fs_read"
    assert result["controller_principal"] != fx["original_principal"]
    assert result["capabilities"] == ["fs.read"]
    trust = json.loads(fx["controllers"].read_text(encoding="utf-8"))
    by_principal = {entry["principal"]: entry for entry in trust["controllers"]}
    assert by_principal[fx["original_principal"]]["capabilities"] == [
        "fs.read",
        "fs.write",
    ]
    assert by_principal[result["controller_principal"]]["capabilities"] == [
        "fs.read"
    ]


def test_missing_preferred_appends_new_readonly_controller_and_preserves_old(tmp_path):
    fx = fixture(tmp_path)
    fx["preferred"].unlink()
    before = json.loads(fx["controllers"].read_text(encoding="utf-8"))
    result = ensure_readonly_controller(
        service_config_path=fx["service"],
        preferred_controller_private_key_path=fx["preferred"],
        generated_controller_private_key_path=fx["generated"],
        harden_windows_acl=False,
    )
    after = json.loads(fx["controllers"].read_text(encoding="utf-8"))

    assert result["mode"] == "ENROLLED_NEW_READONLY_CONTROLLER"
    assert result["service_restart_required"] is True
    assert result["old_controller_entries_preserved"] is True
    assert result["capabilities"] == ["fs.read"]
    assert result["private_key_value_recorded"] is False
    assert fx["generated"].is_file()
    assert len(after["controllers"]) == len(before["controllers"]) + 1
    assert after["controllers"][:-1] == before["controllers"]
    assert after["controllers"][-1]["capabilities"] == ["fs.read"]
    assert after["controllers"][-1]["principal"] == result["controller_principal"]
    assert Path(result["trust_backup"]).read_bytes()
    assert (
        json.loads(Path(result["trust_backup"]).read_text(encoding="utf-8"))
        == before
    )


def test_recovery_is_idempotent_after_new_controller_enrollment(tmp_path):
    fx = fixture(tmp_path)
    fx["preferred"].unlink()
    first = ensure_readonly_controller(
        service_config_path=fx["service"],
        preferred_controller_private_key_path=fx["preferred"],
        generated_controller_private_key_path=fx["generated"],
        harden_windows_acl=False,
    )
    count = len(
        json.loads(fx["controllers"].read_text(encoding="utf-8"))["controllers"]
    )
    second = ensure_readonly_controller(
        service_config_path=fx["service"],
        preferred_controller_private_key_path=fx["preferred"],
        generated_controller_private_key_path=fx["generated"],
        harden_windows_acl=False,
    )
    assert first["controller_principal"] == second["controller_principal"]
    assert second["mode"] == "REUSED_ENROLLED_READONLY_CONTROLLER"
    assert second["service_restart_required"] is False
    assert len(
        json.loads(fx["controllers"].read_text(encoding="utf-8"))["controllers"]
    ) == count


def test_unowned_generated_key_is_not_silently_enrolled(tmp_path):
    fx = fixture(tmp_path)
    fx["preferred"].unlink()
    rogue = ec.generate_private_key(ec.SECP256R1())
    fx["generated"].write_bytes(
        rogue.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    with pytest.raises(
        ControllerRecoveryError,
        match="no recovery marker",
    ):
        ensure_readonly_controller(
            service_config_path=fx["service"],
            preferred_controller_private_key_path=fx["preferred"],
            generated_controller_private_key_path=fx["generated"],
            harden_windows_acl=False,
        )
