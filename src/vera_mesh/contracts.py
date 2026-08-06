"""Closed runtime-profile and protocol binding contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .digests import canonical_json_bytes, sha256_hex, validate_sha256

RUNTIME_PROFILE_SCHEMA = "VERA_MESH_RUNTIME_PROFILE_MANIFEST_V1"
PAIR_CONTRACT_SCHEMA = "VERA_MESH_PAIR_CONTRACT_V2"
HELLO_SCHEMA = "VERA_MESH_HELLO_V2"
CAPABILITIES_SCHEMA = "VERA_MESH_CAPABILITIES_V2"
DIAGNOSTIC_RECEIPT_SCHEMA = "VERA_MESH_DIAGNOSTIC_RECEIPT_V2"

_PROFILE_FIELDS = frozenset(
    {
        "schema",
        "tls_profile_sha256",
        "state_machines_sha256",
        "database_contract_sha256",
        "resource_limits_sha256",
    }
)

_BOUND_MESSAGE_FIELDS: dict[str, frozenset[str]] = {
    PAIR_CONTRACT_SCHEMA: frozenset({"schema", "pair_id", "runtime_profile_sha256"}),
    HELLO_SCHEMA: frozenset({"schema", "node_id", "pair_id", "runtime_profile_sha256"}),
    CAPABILITIES_SCHEMA: frozenset(
        {"schema", "node_id", "pair_id", "capabilities", "runtime_profile_sha256"}
    ),
    DIAGNOSTIC_RECEIPT_SCHEMA: frozenset(
        {"schema", "node_id", "pair_id", "status", "runtime_profile_sha256"}
    ),
}


class ContractValidationError(ValueError):
    """Raised when a closed contract is missing, stale, or ambiguous."""


def _require_closed_fields(payload: Mapping[str, Any], required: frozenset[str]) -> None:
    keys = frozenset(payload)
    missing = sorted(required - keys)
    extra = sorted(keys - required)
    if missing or extra:
        raise ContractValidationError(f"closed contract mismatch: missing={missing}, extra={extra}")


def validate_runtime_profile_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ContractValidationError("runtime profile must be an object")
    _require_closed_fields(payload, _PROFILE_FIELDS)
    if payload["schema"] != RUNTIME_PROFILE_SCHEMA:
        raise ContractValidationError("unsupported runtime profile schema")
    for field in sorted(_PROFILE_FIELDS - {"schema"}):
        validate_sha256(payload[field], field=field)
    return dict(payload)


def runtime_profile_digest(payload: Mapping[str, Any]) -> str:
    validated = validate_runtime_profile_manifest(payload)
    return sha256_hex(canonical_json_bytes(validated))


def validate_bound_message(payload: Mapping[str, Any], profile: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ContractValidationError("message must be an object")
    schema = payload.get("schema")
    required = _BOUND_MESSAGE_FIELDS.get(schema)
    if required is None:
        raise ContractValidationError(f"unsupported bound message schema: {schema!r}")
    _require_closed_fields(payload, required)
    validate_sha256(payload["runtime_profile_sha256"], field="runtime_profile_sha256")
    expected = runtime_profile_digest(profile)
    if payload["runtime_profile_sha256"] != expected:
        raise ContractValidationError("stale or mismatched runtime profile binding")
    if schema == CAPABILITIES_SCHEMA:
        capabilities = payload["capabilities"]
        if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
            raise ContractValidationError("capabilities must be a list of strings")
    return dict(payload)


def bind_runtime_profile(payload: Mapping[str, Any], profile: Mapping[str, Any]) -> dict[str, Any]:
    if "runtime_profile_sha256" in payload:
        raise ContractValidationError("binding field must not be supplied by caller")
    bound = dict(payload)
    bound["runtime_profile_sha256"] = runtime_profile_digest(profile)
    return validate_bound_message(bound, profile)
