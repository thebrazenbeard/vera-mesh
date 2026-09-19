from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from veraport_agent.hot_session import (
    CapabilityEscalation,
    ChallengeMismatch,
    ClientAuth,
    KeyMismatch,
    PrincipalMismatch,
    SessionAuthError,
    SignatureInvalid,
    WorkstationAuthenticator,
    principal_id,
    verify_server_accept,
)


def fixture():
    workstation_key = ec.generate_private_key(ec.SECP256R1())
    controller_key = ec.generate_private_key(ec.SECP256R1())
    controller_principal = principal_id(controller_key.public_key(), "controller")
    authenticator = WorkstationAuthenticator(
        workstation_private_key=workstation_key,
        allowed_controllers={controller_principal: controller_key.public_key()},
        capability_policy={controller_principal: frozenset({"fs.read", "fs.write"})},
    )
    return workstation_key, controller_key, authenticator


def test_mutual_auth_binds_both_nonces_and_capability_ceiling():
    workstation_key, controller_key, authenticator = fixture()
    challenge = authenticator.challenge()
    client = ClientAuth.create(
        controller_private_key=controller_key,
        workstation_principal=challenge.workstation_principal,
        server_challenge=challenge.challenge,
        requested_capabilities={"fs.read"},
    )
    accept, local_binding = authenticator.accept(challenge, client, now_ms=1000, ttl_ms=5000)
    remote_binding = verify_server_accept(
        challenge,
        client,
        accept,
        workstation_public_key=workstation_key.public_key(),
        now_ms=1001,
    )
    assert local_binding == remote_binding
    assert remote_binding.granted_capabilities == frozenset({"fs.read"})


def test_capability_escalation_rejected():
    _, controller_key, authenticator = fixture()
    challenge = authenticator.challenge()
    client = ClientAuth.create(
        controller_private_key=controller_key,
        workstation_principal=challenge.workstation_principal,
        server_challenge=challenge.challenge,
        requested_capabilities={"process.exec"},
    )
    with pytest.raises(CapabilityEscalation):
        authenticator.accept(challenge, client, now_ms=1000)


def test_replayed_client_auth_against_new_challenge_rejected():
    _, controller_key, authenticator = fixture()
    first = authenticator.challenge()
    second = authenticator.challenge()
    client = ClientAuth.create(
        controller_private_key=controller_key,
        workstation_principal=first.workstation_principal,
        server_challenge=first.challenge,
        requested_capabilities={"fs.read"},
    )
    with pytest.raises(ChallengeMismatch):
        authenticator.accept(second, client, now_ms=1000)


def test_unknown_controller_rejected():
    _, _, authenticator = fixture()
    rogue = ec.generate_private_key(ec.SECP256R1())
    challenge = authenticator.challenge()
    client = ClientAuth.create(
        controller_private_key=rogue,
        workstation_principal=challenge.workstation_principal,
        server_challenge=challenge.challenge,
        requested_capabilities={"fs.read"},
    )
    with pytest.raises(PrincipalMismatch):
        authenticator.accept(challenge, client, now_ms=1000)


def test_tampered_client_signature_rejected():
    _, controller_key, authenticator = fixture()
    challenge = authenticator.challenge()
    client = ClientAuth.create(
        controller_private_key=controller_key,
        workstation_principal=challenge.workstation_principal,
        server_challenge=challenge.challenge,
        requested_capabilities={"fs.read"},
    )
    tampered = replace(client, requested_capabilities=("fs.write",))
    with pytest.raises(SignatureInvalid):
        authenticator.accept(challenge, tampered, now_ms=1000)


def test_server_accept_cannot_add_capability():
    workstation_key, controller_key, authenticator = fixture()
    challenge = authenticator.challenge()
    client = ClientAuth.create(
        controller_private_key=controller_key,
        workstation_principal=challenge.workstation_principal,
        server_challenge=challenge.challenge,
        requested_capabilities={"fs.read"},
    )
    accept, _ = authenticator.accept(challenge, client, now_ms=1000)
    tampered = replace(accept, granted_capabilities=("fs.read", "fs.write"))
    with pytest.raises(CapabilityEscalation):
        verify_server_accept(
            challenge,
            client,
            tampered,
            workstation_public_key=workstation_key.public_key(),
            now_ms=1001,
        )


def test_non_p256_controller_key_rejected():
    _, _, authenticator = fixture()
    p384 = ec.generate_private_key(ec.SECP384R1())
    challenge = authenticator.challenge()
    with pytest.raises(KeyMismatch):
        ClientAuth.create(
            controller_private_key=p384,
            workstation_principal=challenge.workstation_principal,
            server_challenge=challenge.challenge,
            requested_capabilities={"fs.read"},
        )


def test_wrong_protocol_version_rejected():
    _, controller_key, authenticator = fixture()
    challenge = authenticator.challenge()
    client = ClientAuth.create(
        controller_private_key=controller_key,
        workstation_principal=challenge.workstation_principal,
        server_challenge=challenge.challenge,
        requested_capabilities={"fs.read"},
    )
    with pytest.raises(SessionAuthError):
        authenticator.accept(replace(challenge, protocol_version="veraport-v2"), client, now_ms=1000)
