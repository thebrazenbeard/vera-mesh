from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .hot_session import key_id, principal_id


class IdentityStoreError(ValueError):
    code = "IDENTITY_STORE_ERROR"


@dataclass(frozen=True)
class LoadedIdentity:
    workstation_private_key: ec.EllipticCurvePrivateKey
    allowed_controllers: dict[str, ec.EllipticCurvePublicKey]
    capability_policy: dict[str, frozenset[str]]


def _load_public_pem(text: str) -> ec.EllipticCurvePublicKey:
    try:
        key = serialization.load_pem_public_key(text.encode("ascii"))
    except Exception as exc:
        raise IdentityStoreError("invalid controller public key PEM") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise IdentityStoreError("controller key must be EC P-256")
    return key


def load_identity(
    *,
    workstation_key_path: str | Path,
    controller_trust_path: str | Path,
) -> LoadedIdentity:
    try:
        private = serialization.load_pem_private_key(
            Path(workstation_key_path).read_bytes(),
            password=None,
        )
    except Exception as exc:
        raise IdentityStoreError("cannot load workstation private key") from exc
    if not isinstance(private, ec.EllipticCurvePrivateKey) or not isinstance(private.curve, ec.SECP256R1):
        raise IdentityStoreError("workstation private key must be EC P-256")

    try:
        trust = json.loads(Path(controller_trust_path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise IdentityStoreError("cannot load controller trust JSON") from exc
    if not isinstance(trust, dict) or trust.get("schema") != "VERAPORT_CONTROLLER_TRUST_V1":
        raise IdentityStoreError("wrong controller trust schema")
    entries = trust.get("controllers")
    if not isinstance(entries, list) or not entries:
        raise IdentityStoreError("controller trust list must be non-empty")

    allowed: dict[str, ec.EllipticCurvePublicKey] = {}
    policy: dict[str, frozenset[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise IdentityStoreError("controller trust entry must be object")
        public = _load_public_pem(str(entry.get("public_key_pem", "")))
        derived = principal_id(public, "controller")
        if entry.get("principal") != derived:
            raise IdentityStoreError("controller principal does not match public key")
        if entry.get("key_id") != key_id(public):
            raise IdentityStoreError("controller key_id does not match public key")
        capabilities = entry.get("capabilities")
        if not isinstance(capabilities, list):
            raise IdentityStoreError("controller capabilities must be list")
        if derived in allowed:
            raise IdentityStoreError("duplicate controller principal")
        allowed[derived] = public
        policy[derived] = frozenset(str(item) for item in capabilities)

    return LoadedIdentity(private, allowed, policy)
