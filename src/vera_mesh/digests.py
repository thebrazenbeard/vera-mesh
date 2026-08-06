"""Canonical JSON and strict SHA-256 helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

_SHA256_RE = re.compile(r"^[0-9a-f]{64}\Z")


class DigestValidationError(ValueError):
    """Raised when canonical bytes or digest evidence is invalid."""


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes with no insignificant whitespace."""
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise DigestValidationError(f"value is not canonical-JSON encodable: {exc}") from exc
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    return hashlib.sha256(data).hexdigest()


def validate_sha256(value: str, *, field: str = "sha256") -> str:
    """Require exactly 64 lowercase hexadecimal characters."""
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise DigestValidationError(
            f"{field} must contain exactly 64 lowercase hexadecimal characters"
        )
    return value


@dataclass(frozen=True, slots=True)
class DigestVector:
    name: str
    value: Any
    canonical_bytes: bytes
    sha256: str

    def verify(self) -> None:
        actual_bytes = canonical_json_bytes(self.value)
        if actual_bytes != self.canonical_bytes:
            raise DigestValidationError(f"{self.name}: canonical bytes do not match")
        actual_sha = sha256_hex(actual_bytes)
        if actual_sha != self.sha256:
            raise DigestValidationError(f"{self.name}: SHA-256 does not match")


def build_digest_vector(name: str, value: Any) -> DigestVector:
    if not isinstance(name, str) or not name:
        raise ValueError("name must be a non-empty string")
    encoded = canonical_json_bytes(value)
    return DigestVector(name=name, value=value, canonical_bytes=encoded, sha256=sha256_hex(encoded))
