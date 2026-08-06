import pytest

from vera_mesh.contracts import (
    CAPABILITIES_SCHEMA,
    DIAGNOSTIC_RECEIPT_SCHEMA,
    HELLO_SCHEMA,
    PAIR_CONTRACT_SCHEMA,
    ContractValidationError,
    RuntimeProfileManifest,
    bind_runtime_profile,
    build_runtime_profile_manifest,
    runtime_profile_digest,
    validate_bound_message,
    validate_runtime_profile_manifest,
)
from vera_mesh.digests import canonical_json_bytes, sha256_hex


def components() -> dict[str, object]:
    return {
        "tls_profile": {"version": "TLS1.3", "cipher": "TLS_AES_256_GCM_SHA384"},
        "state_machines": {"PAIR": ["NEW", "READY"], "DELIVERY": ["QUEUED", "ACKED"]},
        "database_contract": {"schema": 1, "tables": ["pairs", "deliveries"]},
        "resource_limits": {"max_frame_bytes": 1_048_576, "max_inflight": 32},
    }


def profile():
    return build_runtime_profile_manifest(**components())


def test_profile_is_built_from_actual_components_and_reproducibly_hashed() -> None:
    manifest = profile()
    fields = validate_runtime_profile_manifest(manifest)
    assert fields["tls_profile_sha256"] == sha256_hex(
        canonical_json_bytes(components()["tls_profile"])
    )
    assert fields["state_machines_sha256"] == sha256_hex(
        canonical_json_bytes(components()["state_machines"])
    )
    assert fields["database_contract_sha256"] == sha256_hex(
        canonical_json_bytes(components()["database_contract"])
    )
    assert fields["resource_limits_sha256"] == sha256_hex(
        canonical_json_bytes(components()["resource_limits"])
    )
    assert len(manifest.component_evidence) == 4
    for evidence in manifest.component_evidence:
        evidence.verify()
        assert evidence.byte_count == len(evidence.canonical_bytes)


def test_direct_predigested_manifest_construction_is_rejected() -> None:
    with pytest.raises(TypeError, match="build_runtime_profile_manifest"):
        RuntimeProfileManifest({}, (), _token=object())
    with pytest.raises(ContractValidationError, match="must be produced"):
        validate_runtime_profile_manifest(  # type: ignore[arg-type]
            {
                "schema": "VERA_MESH_RUNTIME_PROFILE_MANIFEST_V1",
                "tls_profile_sha256": "1" * 64,
                "state_machines_sha256": "2" * 64,
                "database_contract_sha256": "3" * 64,
                "resource_limits_sha256": "4" * 64,
            }
        )


@pytest.mark.parametrize(
    "component",
    ["tls_profile", "state_machines", "database_contract", "resource_limits"],
)
def test_each_component_change_changes_profile_and_rejects_stale_messages(component: str) -> None:
    original_components = components()
    original = build_runtime_profile_manifest(**original_components)
    message = bind_runtime_profile(
        {"schema": HELLO_SCHEMA, "node_id": "node-a", "pair_id": "pair-1"},
        original,
    )

    changed_components = components()
    changed_components[component] = {"changed": component}
    changed = build_runtime_profile_manifest(**changed_components)
    assert runtime_profile_digest(changed) != runtime_profile_digest(original)
    with pytest.raises(ContractValidationError, match="stale or mismatched"):
        validate_bound_message(message, changed)


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


def test_caller_cannot_override_or_add_claimed_binding_fields() -> None:
    manifest = profile()
    with pytest.raises(ContractValidationError, match="must not be supplied"):
        bind_runtime_profile(
            {
                "schema": PAIR_CONTRACT_SCHEMA,
                "pair_id": "pair-1",
                "runtime_profile_sha256": "f" * 64,
            },
            manifest,
        )
    bound = bind_runtime_profile({"schema": PAIR_CONTRACT_SCHEMA, "pair_id": "pair-1"}, manifest)
    bound["tls_profile_digest"] = "1" * 64
    with pytest.raises(ContractValidationError, match="closed contract mismatch"):
        validate_bound_message(bound, manifest)


@pytest.mark.parametrize(
    "bad_identity",
    [None, {}, [], 123, "", " ", " leading", "trailing ", "contains space", "slash/value"],
)
def test_pair_and_node_identities_reject_malformed_values(bad_identity) -> None:
    manifest = profile()
    with pytest.raises(ContractValidationError, match="pair_id"):
        bind_runtime_profile(
            {"schema": PAIR_CONTRACT_SCHEMA, "pair_id": bad_identity}, manifest
        )
    with pytest.raises(ContractValidationError, match="node_id"):
        bind_runtime_profile(
            {"schema": HELLO_SCHEMA, "node_id": bad_identity, "pair_id": "pair-1"},
            manifest,
        )


@pytest.mark.parametrize("status", [None, {}, [], 123, "", "UNKNOWN", "ready"])
def test_diagnostic_status_is_closed_enum(status) -> None:
    manifest = profile()
    with pytest.raises(ContractValidationError, match="diagnostic status"):
        bind_runtime_profile(
            {
                "schema": DIAGNOSTIC_RECEIPT_SCHEMA,
                "node_id": "node-a",
                "pair_id": "pair-1",
                "status": status,
            },
            manifest,
        )


@pytest.mark.parametrize("status", ["READY", "DEGRADED", "ERROR", "BLOCKED"])
def test_diagnostic_status_accepts_declared_values(status: str) -> None:
    manifest = profile()
    bound = bind_runtime_profile(
        {
            "schema": DIAGNOSTIC_RECEIPT_SCHEMA,
            "node_id": "node-a",
            "pair_id": "pair-1",
            "status": status,
        },
        manifest,
    )
    assert bound["status"] == status
