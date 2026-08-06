"""Closed runtime-profile and protocol binding contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any

from .digests import canonical_json_bytes, sha256_hex, validate_sha256

RUNTIME_PROFILE_SCHEMA = "VERA_MESH_RUNTIME_PROFILE_MANIFEST_V1"
PAIR_CONTRACT_SCHEMA = "VERA_MESH_PAIR_CONTRACT_V2"
HELLO_SCHEMA = "VERA_MESH_HELLO_V2"
CAPABILITIES_SCHEMA = "VERA_MESH_CAPABILITIES_V2"
DIAGNOSTIC_RECEIPT_SCHEMA = "VERA_MESH_DIAGNOSTIC_RECEIPT_V2"
DIAGNOSTIC_STATUSES = frozenset({"READY", "DEGRADED", "ERROR", "BLOCKED"})
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_CONSTRUCTION_TOKEN = object()

_COMPONENT_FIELDS = (
    ("tls_profile", "tls_profile_sha256"),
    ("state_machines", "state_machines_sha256"),
    ("database_contract", "database_contract_sha256"),
    ("resource_limits", "resource_limits_sha256"),
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


@dataclass(frozen=True, slots=True)
class ComponentDigestEvidence:
    component: str
    canonical_bytes: bytes
    byte_count: int
    sha256: str

    def verify(self) -> None:
        if len(self.canonical_bytes) != self.byte_count:
            raise ContractValidationError(f"{self.component}: component byte count mismatch")
        if sha256_hex(self.canonical_bytes) != self.sha256:
            raise ContractValidationError(f"{self.component}: component digest mismatch")


class RuntimeProfileManifest:
    """Opaque manifest created only from actual component contract objects."""

    __slots__ = ("_fields", "_evidence")

    def __init__(
        self,
        fields: Mapping[str, str],
        evidence: tuple[ComponentDigestEvidence, ...],
        *,
        _token: object,
    ) -> None:
        if _token is not _CONSTRUCTION_TOKEN:
            raise TypeError("use build_runtime_profile_manifest")
        self._fields = dict(fields)
        self._evidence = evidence

    def as_dict(self) -> dict[str, str]:
        return dict(self._fields)

    @property
    def component_evidence(self) -> tuple[ComponentDigestEvidence, ...]:
        return self._evidence


def _require_closed_fields(payload: Mapping[str, Any], required: frozenset[str]) -> None:
    keys = frozenset(payload)
    missing = sorted(required - keys)
    extra = sorted(keys - required)
    if missing or extra:
        raise ContractValidationError(f"closed contract mismatch: missing={missing}, extra={extra}")


def _require_identifier(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ContractValidationError(
            f"{field} must be a non-empty canonical identifier using letters, digits, '.', '_', ':', or '-'"
        )
    return value


def build_runtime_profile_manifest(
    *,
    tls_profile: Any,
    state_machines: Any,
    database_contract: Any,
    resource_limits: Any,
) -> RuntimeProfileManifest:
    components = {
        "tls_profile": tls_profile,
        "state_machines": state_machines,
        "database_contract": database_contract,
        "resource_limits": resource_limits,
    }
    fields: dict[str, str] = {"schema": RUNTIME_PROFILE_SCHEMA}
    evidence: list[ComponentDigestEvidence] = []
    for component, digest_field in _COMPONENT_FIELDS:
        encoded = canonical_json_bytes(components[component])
        digest = sha256_hex(encoded)
        fields[digest_field] = digest
        evidence.append(
            ComponentDigestEvidence(
                component=component,
                canonical_bytes=encoded,
                byte_count=len(encoded),
                sha256=digest,
            )
        )
    return RuntimeProfileManifest(fields, tuple(evidence), _token=_CONSTRUCTION_TOKEN)


def validate_runtime_profile_manifest(payload: RuntimeProfileManifest) -> dict[str, str]:
    if not isinstance(payload, RuntimeProfileManifest):
        raise ContractValidationError(
            "runtime profile must be produced by build_runtime_profile_manifest"
        )
    fields = payload.as_dict()
    required = frozenset({"schema", *(field for _, field in _COMPONENT_FIELDS)})
    _require_closed_fields(fields, required)
    if fields["schema"] != RUNTIME_PROFILE_SCHEMA:
        raise ContractValidationError("unsupported runtime profile schema")
    evidence_by_component = {item.component: item for item in payload.component_evidence}
    if frozenset(evidence_by_component) != frozenset(component for component, _ in _COMPONENT_FIELDS):
        raise ContractValidationError("runtime profile component evidence is incomplete")
    for component, digest_field in _COMPONENT_FIELDS:
        validate_sha256(fields[digest_field], field=digest_field)
        item = evidence_by_component[component]
        item.verify()
        if item.sha256 != fields[digest_field]:
            raise ContractValidationError(f"{component}: manifest digest differs from component evidence")
    return fields


def runtime_profile_digest(payload: RuntimeProfileManifest) -> str:
    validated = validate_runtime_profile_manifest(payload)
    return sha256_hex(canonical_json_bytes(validated))


def validate_bound_message(
    payload: Mapping[str, Any], profile: RuntimeProfileManifest
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ContractValidationError("message must be an object")
    schema = payload.get("schema")
    required = _BOUND_MESSAGE_FIELDS.get(schema)
    if required is None:
        raise ContractValidationError(f"unsupported bound message schema: {schema!r}")
    _require_closed_fields(payload, required)
    _require_identifier(payload["pair_id"], field="pair_id")
    if "node_id" in required:
        _require_identifier(payload["node_id"], field="node_id")
    validate_sha256(payload["runtime_profile_sha256"], field="runtime_profile_sha256")
    expected = runtime_profile_digest(profile)
    if payload["runtime_profile_sha256"] != expected:
        raise ContractValidationError("stale or mismatched runtime profile binding")
    if schema == CAPABILITIES_SCHEMA:
        capabilities = payload["capabilities"]
        if (
            not isinstance(capabilities, list)
            or not capabilities
            or any(
                not isinstance(item, str) or not item or item.strip() != item
                for item in capabilities
            )
        ):
            raise ContractValidationError("capabilities must be a non-empty list of canonical strings")
    if schema == DIAGNOSTIC_RECEIPT_SCHEMA:
        status = payload["status"]
        if not isinstance(status, str) or status not in DIAGNOSTIC_STATUSES:
            raise ContractValidationError(
                f"diagnostic status must be one of {sorted(DIAGNOSTIC_STATUSES)}"
            )
    return dict(payload)


def bind_runtime_profile(
    payload: Mapping[str, Any], profile: RuntimeProfileManifest
) -> dict[str, Any]:
    if "runtime_profile_sha256" in payload:
        raise ContractValidationError("binding field must not be supplied by caller")
    bound = dict(payload)
    bound["runtime_profile_sha256"] = runtime_profile_digest(profile)
    return validate_bound_message(bound, profile)
