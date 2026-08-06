"""Canonical JSON, packet identity, and strict SHA-256 helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any

_SHA256_RE = re.compile(r"^[0-9a-f]{64}\Z")
PACKET_METADATA_EXCLUSIONS = frozenset(
    {"packet_bytes", "packet_canonicalization", "packet_sha256"}
)


class DigestValidationError(ValueError):
    """Raised when canonical bytes or digest evidence is invalid."""


def _validate_json_value(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise DigestValidationError(
                    f"{path}: canonical JSON object keys must be strings"
                )
            _validate_json_value(item, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DigestValidationError(f"{path}: non-finite numbers are forbidden")
        return
    raise DigestValidationError(
        f"{path}: unsupported canonical JSON value type {type(value).__name__}"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Return unambiguous deterministic UTF-8 JSON bytes."""
    _validate_json_value(value)
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


@dataclass(frozen=True, slots=True)
class PacketDigest:
    canonical_bytes: bytes
    byte_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class PacketDigestVector:
    name: str
    canonical_bytes: bytes
    byte_count: int
    sha256: str

    def verify(self) -> None:
        if len(self.canonical_bytes) != self.byte_count:
            raise DigestValidationError(f"{self.name}: packet byte count does not match")
        if sha256_hex(self.canonical_bytes) != self.sha256:
            raise DigestValidationError(f"{self.name}: packet SHA-256 does not match")


def build_digest_vector(name: str, value: Any) -> DigestVector:
    if not isinstance(name, str) or not name:
        raise ValueError("name must be a non-empty string")
    encoded = canonical_json_bytes(value)
    return DigestVector(name=name, value=value, canonical_bytes=encoded, sha256=sha256_hex(encoded))


def packet_canonical_bytes(packet: Mapping[str, Any]) -> bytes:
    """Hash packet content after excluding exactly the three identity metadata fields."""
    if not isinstance(packet, Mapping):
        raise DigestValidationError("packet must be a JSON object")
    _validate_json_value(packet)
    projected = {key: value for key, value in packet.items() if key not in PACKET_METADATA_EXCLUSIONS}
    return canonical_json_bytes(projected)


def packet_digest(packet: Mapping[str, Any]) -> PacketDigest:
    encoded = packet_canonical_bytes(packet)
    return PacketDigest(
        canonical_bytes=encoded,
        byte_count=len(encoded),
        sha256=sha256_hex(encoded),
    )


PACKET_DIGEST_VECTORS: tuple[PacketDigestVector, ...] = (
    PacketDigestVector(
        name="PASS_CORE",
        canonical_bytes=b'{"a":1,"z":"x y"}',
        byte_count=17,
        sha256="630b686346c4f65cef2c20f3b38452c3feb48c4e03a19e7e7004f5aa4c70f3cd",
    ),
    PacketDigestVector(
        name="PASS_EXACT_METADATA_EXCLUSION",
        canonical_bytes=b'{"a":1,"z":"x y"}',
        byte_count=17,
        sha256="630b686346c4f65cef2c20f3b38452c3feb48c4e03a19e7e7004f5aa4c70f3cd",
    ),
    PacketDigestVector(
        name="HOSTILE_EXTRA_METADATA_KEY",
        canonical_bytes=b'{"a":1,"packet_extra":7,"z":"x y"}',
        byte_count=34,
        sha256="8d000693ea8f26cd6899b3fed56e892e8cd9d7cb8032859fa2d58f5bc4727f5b",
    ),
    PacketDigestVector(
        name="HOSTILE_OMITTED_EXCLUSION",
        canonical_bytes=b'{"a":1,"packet_canonicalization":"x","z":"x y"}',
        byte_count=47,
        sha256="926c45021a073c36618feb3cba6e18047e307fb05e8d6b34afdfe02084f53c3c",
    ),
    PacketDigestVector(
        name="HOSTILE_RENAMED_METADATA_KEY",
        canonical_bytes=b'{"a":1,"packet_hash":"y","z":"x y"}',
        byte_count=35,
        sha256="0d3197c525491fea8e20d9025a974a806b02e145883a44d2e39aa5baa653d36a",
    ),
)
