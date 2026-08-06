import pytest

from vera_mesh.digests import (
    DigestValidationError,
    build_digest_vector,
    canonical_json_bytes,
    sha256_hex,
    validate_sha256,
)


def test_exact_canonical_vector_bytes_and_digest() -> None:
    vector = build_digest_vector("ordered-object", {"b": 2, "a": 1})
    assert vector.canonical_bytes == b'{"a":1,"b":2}'
    assert len(vector.canonical_bytes) == 13
    assert vector.sha256 == "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"
    vector.verify()


def test_escaped_json_text_is_not_canonical_json_object() -> None:
    object_bytes = canonical_json_bytes({"a": 1})
    escaped_text_bytes = canonical_json_bytes('{"a":1}')
    assert object_bytes == b'{"a":1}'
    assert escaped_text_bytes == b'"{\\"a\\":1}"'
    assert sha256_hex(object_bytes) != sha256_hex(escaped_text_bytes)


@pytest.mark.parametrize(
    "value",
    [
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "a" + "Z" * 63,
        "g" * 64,
        "0" * 63 + "-",
        123,
        None,
    ],
)
def test_sha256_validator_rejects_malformed_values(value) -> None:
    with pytest.raises(DigestValidationError):
        validate_sha256(value)  # type: ignore[arg-type]


def test_sha256_validator_accepts_exact_lowercase_hex() -> None:
    assert validate_sha256("0123456789abcdef" * 4) == "0123456789abcdef" * 4
