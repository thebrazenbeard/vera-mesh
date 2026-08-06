import pytest

from vera_mesh.digests import (
    PACKET_DIGEST_VECTORS,
    PACKET_METADATA_EXCLUSIONS,
    DigestValidationError,
    build_digest_vector,
    canonical_json_bytes,
    packet_digest,
    sha256_hex,
    validate_sha256,
)


def vector(name: str):
    return next(item for item in PACKET_DIGEST_VECTORS if item.name == name)


def test_exact_canonical_vector_bytes_and_digest() -> None:
    item = build_digest_vector("ordered-object", {"b": 2, "a": 1})
    assert item.canonical_bytes == b'{"a":1,"b":2}'
    assert len(item.canonical_bytes) == 13
    assert item.sha256 == "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"
    item.verify()


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


@pytest.mark.parametrize(
    "value",
    [
        {1: "x"},
        {True: "x"},
        {None: "x"},
        {"nested": {1: "x"}},
        {"items": [{"ok": 1}, {2: "bad"}]},
        {"mixed": {"1": "string", 1: "integer"}},
    ],
)
def test_canonical_json_rejects_non_string_object_keys(value) -> None:
    with pytest.raises(DigestValidationError, match="keys must be strings"):
        canonical_json_bytes(value)


def test_prior_five_packet_vectors_are_exact_and_reproducible() -> None:
    assert len(PACKET_DIGEST_VECTORS) == 5
    for item in PACKET_DIGEST_VECTORS:
        item.verify()
        assert len(item.canonical_bytes) == item.byte_count
        assert sha256_hex(item.canonical_bytes) == item.sha256


def test_packet_digest_excludes_exact_identity_metadata() -> None:
    packet = {
        "a": 1,
        "z": "x y",
        "packet_bytes": 999,
        "packet_canonicalization": "x",
        "packet_sha256": "y",
    }
    result = packet_digest(packet)
    expected = vector("PASS_EXACT_METADATA_EXCLUSION")
    assert PACKET_METADATA_EXCLUSIONS == frozenset(
        {"packet_bytes", "packet_canonicalization", "packet_sha256"}
    )
    assert result.canonical_bytes == expected.canonical_bytes
    assert result.byte_count == expected.byte_count
    assert result.sha256 == expected.sha256


def test_packet_digest_retains_extra_and_renamed_metadata_keys() -> None:
    extra = packet_digest(
        {
            "a": 1,
            "z": "x y",
            "packet_extra": 7,
            "packet_bytes": 999,
            "packet_canonicalization": "x",
            "packet_sha256": "y",
        }
    )
    extra_expected = vector("HOSTILE_EXTRA_METADATA_KEY")
    assert extra.canonical_bytes == extra_expected.canonical_bytes
    assert extra.byte_count == extra_expected.byte_count
    assert extra.sha256 == extra_expected.sha256

    renamed = packet_digest(
        {
            "a": 1,
            "z": "x y",
            "packet_hash": "y",
            "packet_bytes": 999,
            "packet_canonicalization": "x",
            "packet_sha256": "y",
        }
    )
    renamed_expected = vector("HOSTILE_RENAMED_METADATA_KEY")
    assert renamed.canonical_bytes == renamed_expected.canonical_bytes
    assert renamed.byte_count == renamed_expected.byte_count
    assert renamed.sha256 == renamed_expected.sha256


def test_packet_digest_cannot_omit_required_exclusion() -> None:
    packet = {
        "a": 1,
        "z": "x y",
        "packet_bytes": 999,
        "packet_canonicalization": "x",
        "packet_sha256": "y",
    }
    result = packet_digest(packet)
    hostile = vector("HOSTILE_OMITTED_EXCLUSION")
    assert result.sha256 != hostile.sha256
    assert result.byte_count != hostile.byte_count


def test_packet_digest_detects_metadata_and_byte_count_changes() -> None:
    core = packet_digest({"a": 1, "z": "x y"})
    changed = packet_digest({"a": 1, "z": "x y", "packet_extra": 7})
    assert core.sha256 != changed.sha256
    assert core.byte_count != changed.byte_count

    wrong_declared_count = core.byte_count + 1
    wrong_declared_digest = "0" * 64
    assert wrong_declared_count != core.byte_count
    assert wrong_declared_digest != core.sha256
