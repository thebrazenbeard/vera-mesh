from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from typing import Iterable

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature


DOMAIN_CLIENT = b"veramesh-veraport-v1/session-client-auth\n"
DOMAIN_SERVER = b"veramesh-veraport-v1/session-server-accept\n"


class SessionAuthError(RuntimeError):
    code = "SESSION_AUTH_ERROR"


class PrincipalMismatch(SessionAuthError):
    code = "PRINCIPAL_MISMATCH"


class KeyMismatch(SessionAuthError):
    code = "KEY_MISMATCH"


class CapabilityEscalation(SessionAuthError):
    code = "CAPABILITY_ESCALATION"


class ChallengeMismatch(SessionAuthError):
    code = "CHALLENGE_MISMATCH"


class SignatureInvalid(SessionAuthError):
    code = "SIGNATURE_INVALID"


def b64u(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64u_decode(value: str) -> bytes:
    pad = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + pad).encode("ascii"))


def _require_p256_public(public_key: ec.EllipticCurvePublicKey) -> None:
    if not isinstance(public_key.curve, ec.SECP256R1):
        raise KeyMismatch("VeraPort V1 requires an EC P-256 public key")


def _require_p256_private(private_key: ec.EllipticCurvePrivateKey) -> None:
    if not isinstance(private_key.curve, ec.SECP256R1):
        raise KeyMismatch("VeraPort V1 requires an EC P-256 private key")


def principal_id(public_key: ec.EllipticCurvePublicKey, prefix: str) -> str:
    _require_p256_public(public_key)
    spki = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return f"{prefix}:{hashlib.sha256(spki).hexdigest()}"


def key_id(public_key: ec.EllipticCurvePublicKey) -> str:
    _require_p256_public(public_key)
    spki = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(spki).hexdigest()


def _field(name: str, value: str) -> bytes:
    raw = value.encode("utf-8")
    return name.encode("ascii") + b"=" + str(len(raw)).encode("ascii") + b":" + raw + b"\n"


def _capabilities(values: Iterable[str]) -> str:
    return ",".join(sorted(set(values)))


def _sign_p1363(private_key: ec.EllipticCurvePrivateKey, payload: bytes) -> str:
    _require_p256_private(private_key)
    der = private_key.sign(payload, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def _verify_p1363(public_key: ec.EllipticCurvePublicKey, signature: str, payload: bytes) -> None:
    _require_p256_public(public_key)
    try:
        raw = b64u_decode(signature)
        if len(raw) != 64:
            raise ValueError("signature length")
        r = int.from_bytes(raw[:32], "big")
        s = int.from_bytes(raw[32:], "big")
        public_key.verify(encode_dss_signature(r, s), payload, ec.ECDSA(hashes.SHA256()))
    except Exception as exc:
        raise SignatureInvalid("P-256 signature verification failed") from exc


@dataclass(frozen=True)
class ServerChallenge:
    protocol_version: str
    workstation_principal: str
    workstation_key_id: str
    challenge: str

    @classmethod
    def create(cls, workstation_public_key: ec.EllipticCurvePublicKey) -> "ServerChallenge":
        return cls(
            protocol_version="veraport-v1",
            workstation_principal=principal_id(workstation_public_key, "workstation"),
            workstation_key_id=key_id(workstation_public_key),
            challenge=b64u(os.urandom(32)),
        )


@dataclass(frozen=True)
class ClientAuth:
    protocol_version: str
    controller_principal: str
    controller_key_id: str
    workstation_principal: str
    server_challenge: str
    client_nonce: str
    requested_capabilities: tuple[str, ...]
    signature: str

    @classmethod
    def create(
        cls,
        *,
        controller_private_key: ec.EllipticCurvePrivateKey,
        workstation_principal: str,
        server_challenge: str,
        requested_capabilities: Iterable[str],
        client_nonce: bytes | None = None,
    ) -> "ClientAuth":
        _require_p256_private(controller_private_key)
        public = controller_private_key.public_key()
        nonce = b64u(os.urandom(32) if client_nonce is None else client_nonce)
        unsigned = cls(
            protocol_version="veraport-v1",
            controller_principal=principal_id(public, "controller"),
            controller_key_id=key_id(public),
            workstation_principal=workstation_principal,
            server_challenge=server_challenge,
            client_nonce=nonce,
            requested_capabilities=tuple(sorted(set(requested_capabilities))),
            signature="",
        )
        return cls(**{**unsigned.__dict__, "signature": _sign_p1363(controller_private_key, unsigned.signature_base())})

    def signature_base(self) -> bytes:
        return b"".join((
            DOMAIN_CLIENT,
            _field("protocol_version", self.protocol_version),
            _field("controller_principal", self.controller_principal),
            _field("controller_key_id", self.controller_key_id),
            _field("workstation_principal", self.workstation_principal),
            _field("server_challenge", self.server_challenge),
            _field("client_nonce", self.client_nonce),
            _field("requested_capabilities", _capabilities(self.requested_capabilities)),
        ))


@dataclass(frozen=True)
class ServerAccept:
    protocol_version: str
    session_id: str
    controller_principal: str
    workstation_principal: str
    workstation_key_id: str
    server_challenge: str
    client_nonce: str
    granted_capabilities: tuple[str, ...]
    expires_at_ms: int
    signature: str

    def signature_base(self) -> bytes:
        return b"".join((
            DOMAIN_SERVER,
            _field("protocol_version", self.protocol_version),
            _field("session_id", self.session_id),
            _field("controller_principal", self.controller_principal),
            _field("workstation_principal", self.workstation_principal),
            _field("workstation_key_id", self.workstation_key_id),
            _field("server_challenge", self.server_challenge),
            _field("client_nonce", self.client_nonce),
            _field("granted_capabilities", _capabilities(self.granted_capabilities)),
            _field("expires_at_ms", str(self.expires_at_ms)),
        ))


@dataclass(frozen=True)
class SessionBinding:
    session_id: str
    controller_principal: str
    workstation_principal: str
    granted_capabilities: frozenset[str]
    expires_at_ms: int


class WorkstationAuthenticator:
    def __init__(
        self,
        *,
        workstation_private_key: ec.EllipticCurvePrivateKey,
        allowed_controllers: dict[str, ec.EllipticCurvePublicKey],
        capability_policy: dict[str, frozenset[str]],
    ) -> None:
        self.workstation_private_key = workstation_private_key
        self.workstation_public_key = workstation_private_key.public_key()
        self.workstation_principal = principal_id(self.workstation_public_key, "workstation")
        self.workstation_key_id = key_id(self.workstation_public_key)
        self.allowed_controllers = dict(allowed_controllers)
        self.capability_policy = dict(capability_policy)

    def challenge(self) -> ServerChallenge:
        return ServerChallenge.create(self.workstation_public_key)

    def accept(
        self,
        challenge: ServerChallenge,
        client: ClientAuth,
        *,
        now_ms: int,
        ttl_ms: int = 300_000,
    ) -> tuple[ServerAccept, SessionBinding]:
        if challenge.protocol_version != "veraport-v1" or client.protocol_version != "veraport-v1":
            raise SessionAuthError("protocol_version must be veraport-v1")
        if len(b64u_decode(challenge.challenge)) != 32 or len(b64u_decode(client.client_nonce)) != 32:
            raise ChallengeMismatch("session nonces must be exactly 32 bytes")
        if challenge.workstation_principal != self.workstation_principal:
            raise PrincipalMismatch("challenge is for a different workstation")
        if challenge.workstation_key_id != self.workstation_key_id:
            raise KeyMismatch("challenge key does not match workstation")
        if client.workstation_principal != self.workstation_principal:
            raise PrincipalMismatch("client targeted a different workstation")
        if client.server_challenge != challenge.challenge:
            raise ChallengeMismatch("client did not bind the current workstation challenge")

        public = self.allowed_controllers.get(client.controller_principal)
        if public is None:
            raise PrincipalMismatch("controller principal is not enrolled")
        if key_id(public) != client.controller_key_id:
            raise KeyMismatch("controller key id does not match enrolled principal")
        if principal_id(public, "controller") != client.controller_principal:
            raise PrincipalMismatch("controller principal does not match enrolled key")
        _verify_p1363(public, client.signature, client.signature_base())

        ceiling = self.capability_policy.get(client.controller_principal, frozenset())
        requested = frozenset(client.requested_capabilities)
        if not requested.issubset(ceiling):
            raise CapabilityEscalation("requested capability exceeds local controller policy")
        if ttl_ms <= 0:
            raise ValueError("ttl_ms must be positive")

        session_id = b64u(os.urandom(24))
        unsigned = ServerAccept(
            protocol_version="veraport-v1",
            session_id=session_id,
            controller_principal=client.controller_principal,
            workstation_principal=self.workstation_principal,
            workstation_key_id=self.workstation_key_id,
            server_challenge=challenge.challenge,
            client_nonce=client.client_nonce,
            granted_capabilities=tuple(sorted(requested)),
            expires_at_ms=now_ms + ttl_ms,
            signature="",
        )
        accept = ServerAccept(
            **{**unsigned.__dict__, "signature": _sign_p1363(self.workstation_private_key, unsigned.signature_base())}
        )
        binding = SessionBinding(
            session_id=session_id,
            controller_principal=client.controller_principal,
            workstation_principal=self.workstation_principal,
            granted_capabilities=requested,
            expires_at_ms=accept.expires_at_ms,
        )
        return accept, binding


def verify_server_accept(
    challenge: ServerChallenge,
    client: ClientAuth,
    accept: ServerAccept,
    *,
    workstation_public_key: ec.EllipticCurvePublicKey,
    now_ms: int,
) -> SessionBinding:
    if (
        challenge.protocol_version != "veraport-v1"
        or client.protocol_version != "veraport-v1"
        or accept.protocol_version != "veraport-v1"
    ):
        raise SessionAuthError("protocol_version must be veraport-v1")
    if len(b64u_decode(challenge.challenge)) != 32 or len(b64u_decode(client.client_nonce)) != 32:
        raise ChallengeMismatch("session nonces must be exactly 32 bytes")
    expected_workstation = principal_id(workstation_public_key, "workstation")
    if accept.workstation_principal != expected_workstation:
        raise PrincipalMismatch("server accept principal does not match pinned workstation key")
    if accept.workstation_key_id != key_id(workstation_public_key):
        raise KeyMismatch("server accept key id does not match pinned workstation key")
    if accept.controller_principal != client.controller_principal:
        raise PrincipalMismatch("server accept bound a different controller")
    if accept.server_challenge != challenge.challenge or accept.client_nonce != client.client_nonce:
        raise ChallengeMismatch("server accept does not bind both session nonces")
    if not frozenset(accept.granted_capabilities).issubset(frozenset(client.requested_capabilities)):
        raise CapabilityEscalation("server granted capability not requested by controller")
    if now_ms >= accept.expires_at_ms:
        raise SessionAuthError("server accept is expired")
    _verify_p1363(workstation_public_key, accept.signature, accept.signature_base())
    return SessionBinding(
        session_id=accept.session_id,
        controller_principal=accept.controller_principal,
        workstation_principal=accept.workstation_principal,
        granted_capabilities=frozenset(accept.granted_capabilities),
        expires_at_ms=accept.expires_at_ms,
    )
