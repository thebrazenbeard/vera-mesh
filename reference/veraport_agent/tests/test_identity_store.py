import json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import pytest

from veraport_agent.hot_session import key_id, principal_id
from veraport_agent.identity_store import load_identity, IdentityStoreError


def pem_private(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def pem_public(key):
    return key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def write_fixture(tmp_path, declared_principal=None):
    workstation = ec.generate_private_key(ec.SECP256R1())
    controller = ec.generate_private_key(ec.SECP256R1())
    wk = tmp_path / "workstation.pem"
    trust = tmp_path / "controllers.json"
    wk.write_bytes(pem_private(workstation))
    pub = controller.public_key()
    trust.write_text(json.dumps({
        "schema": "VERAPORT_CONTROLLER_TRUST_V1",
        "controllers": [{
            "principal": declared_principal or principal_id(pub, "controller"),
            "key_id": key_id(pub),
            "public_key_pem": pem_public(pub),
            "capabilities": ["fs.read"],
        }],
    }), encoding="utf-8")
    return wk, trust, controller


def test_identity_loader_binds_controller_principal_and_policy(tmp_path):
    wk, trust, controller = write_fixture(tmp_path)
    loaded = load_identity(workstation_key_path=wk, controller_trust_path=trust)
    principal = principal_id(controller.public_key(), "controller")
    assert principal in loaded.allowed_controllers
    assert loaded.capability_policy[principal] == frozenset({"fs.read"})


def test_identity_loader_rejects_principal_key_mismatch(tmp_path):
    wk, trust, _ = write_fixture(tmp_path, declared_principal="controller:" + "0" * 64)
    with pytest.raises(IdentityStoreError):
        load_identity(workstation_key_path=wk, controller_trust_path=trust)


def test_identity_loader_rejects_non_p256_controller(tmp_path):
    workstation = ec.generate_private_key(ec.SECP256R1())
    controller = ec.generate_private_key(ec.SECP384R1())
    wk = tmp_path / "w.pem"
    wk.write_bytes(pem_private(workstation))
    trust = tmp_path / "t.json"
    trust.write_text(json.dumps({
        "schema": "VERAPORT_CONTROLLER_TRUST_V1",
        "controllers": [{
            "principal": "controller:" + "0" * 64,
            "key_id": "0" * 64,
            "public_key_pem": pem_public(controller.public_key()),
            "capabilities": [],
        }],
    }))
    with pytest.raises(IdentityStoreError):
        load_identity(workstation_key_path=wk, controller_trust_path=trust)


def test_identity_loader_refuses_empty_trust(tmp_path):
    workstation = ec.generate_private_key(ec.SECP256R1())
    wk = tmp_path / "w.pem"
    wk.write_bytes(pem_private(workstation))
    trust = tmp_path / "t.json"
    trust.write_text(json.dumps({
        "schema": "VERAPORT_CONTROLLER_TRUST_V1",
        "controllers": [],
    }))
    with pytest.raises(IdentityStoreError):
        load_identity(workstation_key_path=wk, controller_trust_path=trust)
