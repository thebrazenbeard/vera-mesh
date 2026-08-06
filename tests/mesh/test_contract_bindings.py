import pytest

from vera_mesh.contracts import (
    CAPABILITIES_SCHEMA,
    DIAGNOSTIC_RECEIPT_SCHEMA,
    HELLO_SCHEMA,
    PAIR_CONTRACT_SCHEMA,
    RUNTIME_PROFILE_SCHEMA,
    ContractValidationError,
    bind_runtime_profile,
    runtime_profile_digest,
    validate_bound_message,
    validate_runtime_profile_manifest,
)


def digest(character: str) -> str:
    return character * 64


def profile() -> dict[str, str]:
    return {
        "schema": RUNTIME_PROFILE_SCHEMA,
        "tls_profile_sha256": digest("1"),
        "state_machines_sha256": digest("2"),
        "database_contract_sha256": digest("3"),
        "resource_limits_sha256": digest("4"),
    }


def test_profile_schema_is_closed_and_reproducibly_hashed() -> None:
    manifest = profile()
    assert validate_runtime_profile_manifest(manifest) == manifest
    assert runtime_profile_digest(manifest) == runtime_profile_digest(dict(reversed(list(manifest.items()))))


@pytest.mark.parametrize("field", [
    "tls_profile_sha256",
    "state_machines_sha256",
    "database_contract_sha256",
    "resource_limits_sha256",
])
def test_profile_rejects_malformed_component_digest(field: str) -> None:
    manifest = profile()
    manifest[field] = "a" + "Z" * 63
    with pytest.raises(ValueError):
        validate_runtime_profile_manifest(manifest)


def test_profile_rejects_unknown_or_missing_fields() -> None:
    extra = profile() | {"phantom_digest": digest("5")}
    with pytest.raises(ContractValidationError):
        validate_runtime_profile_manifest(extra)
    missing = profile()
    del missing["database_contract_sha256"]
    with pytest.raises(ContractValidationError):
        validate_runtime_profile_manifest(missing)


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (PAIR_CONTRACT_SCHEMA, {"pair_id": "pair-1"}),
        (HELLO_SCHEMA, {"node_id": "node-a", "pair_id": "pair-1"}),
        (
            CAPABILITIES_SCHEMA,
            {"node_id": "node-a", "pair_id": "pair-1", "capabilities": ["mesh.ping"]},
        ),
        (
            DIAGNOSTIC_RECEIPT_SCHEMA,
            {"node_id": "node-a", "pair_id": "pair-1", "status": "READY"},
        ),
    ],
)
def test_every_protocol_surface_binds_same_runtime_profile(schema: str, payload: dict) -> None:
    manifest = profile()
    bound = bind_runtime_profile({"schema": schema, **payload}, manifest)
    assert bound["runtime_profile_sha256"] == runtime_profile_digest(manifest)
    assert validate_bound_message(bound, manifest) == bound


def test_stale_runtime_profile_binding_is_rejected() -> None:
    manifest = profile()
    bound = bind_runtime_profile(
        {"schema": HELLO_SCHEMA, "node_id": "node-a", "pair_id": "pair-1"}, manifest
    )
    changed = profile()
    changed["tls_profile_sha256"] = digest("a")
    with pytest.raises(ContractValidationError, match="stale or mismatched"):
        validate_bound_message(bound, changed)


def test_caller_cannot_override_or_add_claimed_binding_fields() -> None:
    manifest = profile()
    with pytest.raises(ContractValidationError, match="must not be supplied"):
        bind_runtime_profile(
            {
                "schema": PAIR_CONTRACT_SCHEMA,
                "pair_id": "pair-1",
                "runtime_profile_sha256": digest("f"),
            },
            manifest,
        )
    bound = bind_runtime_profile({"schema": PAIR_CONTRACT_SCHEMA, "pair_id": "pair-1"}, manifest)
    bound["tls_profile_digest"] = digest("1")
    with pytest.raises(ContractValidationError, match="closed contract mismatch"):
        validate_bound_message(bound, manifest)
