"""Deterministic contract primitives for Vera Mesh."""

from .contracts import (
    ContractValidationError,
    bind_runtime_profile,
    runtime_profile_digest,
    validate_bound_message,
    validate_runtime_profile_manifest,
)
from .digests import (
    DigestValidationError,
    DigestVector,
    build_digest_vector,
    canonical_json_bytes,
    sha256_hex,
    validate_sha256,
)

__all__ = [
    "ContractValidationError",
    "DigestValidationError",
    "DigestVector",
    "bind_runtime_profile",
    "build_digest_vector",
    "canonical_json_bytes",
    "runtime_profile_digest",
    "sha256_hex",
    "validate_bound_message",
    "validate_runtime_profile_manifest",
    "validate_sha256",
]
