import hashlib

import pytest

from vera_mesh.canonical_json_v2 import (
    CANONICALIZATION_ID,
    CanonicalJsonV2Error,
    canonicalize_json_v2,
)


POSITIVE_FIXTURES = (
    (
        "CJV2-001-ASCII-ORDER",
        "7b227a223a22782079222c2261223a317d",
        "7b2261223a312c227a223a22782079227d",
        17,
        "630b686346c4f65cef2c20f3b38452c3feb48c4e03a19e7e7004f5aa4c70f3cd",
    ),
    (
        "CJV2-002-HTML-CHARS",
        "7b2278223a223c3e26227d",
        "7b2278223a223c3e26227d",
        11,
        "807c07d6f1cb39b4c87e34312ece8b87016fdfab365a17ec0687f81fc8cc029e",
    ),
    (
        "CJV2-003-U2028-U2029",
        "7b2278223a225c75323032385c7532303239227d",
        "7b2278223a22e280a8e280a9227d",
        14,
        "dfd90bc01a3641c8b6b5cc1b1161de1f91d63ef197c1184b501ee3f4e5d7549d",
    ),
    (
        "CJV2-004-ASTRAL-SURROGATE-PAIR-INPUT",
        "7b2278223a225c75643833645c7564653030227d",
        "7b2278223a22f09f9880227d",
        12,
        "c10cb8a0c573e0eafbed00d33a65f0a83da0cb03445908aed65f7deecc63019e",
    ),
    (
        "CJV2-005-CONTROLS",
        "7b2278223a225c75303030305c625c745c6e5c665c725c7530303166227d",
        "7b2278223a225c75303030305c625c745c6e5c665c725c7530303166227d",
        30,
        "c553de3e2c67b87c3da40bcd0ac7a191b7ad83aa7b069b8cfe647a0c6ac8aa9a",
    ),
    (
        "CJV2-006-SCALAR-KEY-ORDER",
        "7b225c75643833645c7564653030223a2261737472616c222c225c7565303030223a22626d70227d",
        "7b22ee8080223a22626d70222c22f09f9880223a2261737472616c227d",
        29,
        "4874d36535a1b5dfdad7f2b3af83f87debd2f0795ae7d2d09447e17223c42dd0",
    ),
    (
        "CJV2-007-NO-NORMALIZATION",
        "7b2279223a2265cc81222c2278223a22c3a9227d",
        "7b2278223a22c3a9222c2279223a2265cc81227d",
        20,
        "60916069ce11e17c78d8ace441b499ffde5fd3aeb5c441fe8e886f17cd980925",
    ),
    (
        "CJV2-008-BIG-INTEGER",
        "7b226e223a3132333435363738393031323334353637383930313233343536373839302c226d223a2d3132333435363738393031323334353637383930313233343536373839307d",
        "7b226d223a2d3132333435363738393031323334353637383930313233343536373839302c226e223a3132333435363738393031323334353637383930313233343536373839307d",
        72,
        "1385303b348444fcc004e2ae9c60afa2e4ed29a28d6db76ebb939c024ab340ba",
    ),
    (
        "CJV2-009-ARRAY-ORDER",
        "7b2278223a5b332c322c312c22c3a9222c22f09f9880222c747275652c66616c73652c6e756c6c5d7d",
        "7b2278223a5b332c322c312c22c3a9222c22f09f9880222c747275652c66616c73652c6e756c6c5d7d",
        41,
        "5838ff729a33c9b065ec2962d6db23a38a66c7e3f7171f21167e5466e393e2f2",
    ),
    (
        "CJV2-010-ESCAPE-QUOTE-BACKSLASH-SLASH",
        "7b2278223a225c225c5c2f227d",
        "7b2278223a225c225c5c2f227d",
        13,
        "92e28b2a4be5895370b79551467fc8702f98478fa315da4c140049e98bcfbb11",
    ),
    (
        "CJV2-011-NO-NORMALIZATION-DISTINCT-KEYS",
        "7b22c3a9223a312c2265cc81223a327d",
        "7b2265cc81223a322c22c3a9223a317d",
        16,
        "a7962fb10dc1255be368ece9c22b2256605921dc6d0a8c9409d3ee406bcb86e5",
    ),
)

HOSTILE_FIXTURES = (
    ("CJV2-H01-DUPLICATE-KEY", "7b2261223a312c2261223a327d", "DUPLICATE_OBJECT_KEY"),
    ("CJV2-H02-FLOAT-DECIMAL", "7b2278223a312e307d", "FLOAT_FORBIDDEN"),
    ("CJV2-H03-FLOAT-EXP", "7b2278223a31652d367d", "FLOAT_FORBIDDEN"),
    ("CJV2-H04-NEGZERO-FLOAT", "7b2278223a2d302e307d", "FLOAT_FORBIDDEN"),
    ("CJV2-H05-NAN", "7b2278223a4e614e7d", "MALFORMED_JSON"),
    ("CJV2-H06-UNPAIRED-HIGH-SURROGATE-VALUE", "7b2278223a225c7564383030227d", "INVALID_UNICODE_SCALAR"),
    ("CJV2-H07-UNPAIRED-LOW-SURROGATE-KEY", "7b225c7564633030223a317d", "INVALID_UNICODE_SCALAR"),
    ("CJV2-H08-INVALID-UTF8", "7b2278223a22ff227d", "INVALID_UTF8"),
    ("CJV2-H09-TRAILING-GARBAGE", "7b2278223a317d78", "MALFORMED_JSON"),
    ("CJV2-H10-ESCAPED-EQUIVALENT-DUPLICATE-ASCII", "7b2261223a312c225c7530303631223a327d", "DUPLICATE_OBJECT_KEY"),
    ("CJV2-H11-ESCAPED-EQUIVALENT-DUPLICATE-ASTRAL", "7b22f09f9880223a312c225c75643833645c7564653030223a327d", "DUPLICATE_OBJECT_KEY"),
)


def assert_code(raw: bytes, code: str) -> None:
    with pytest.raises(CanonicalJsonV2Error) as exc_info:
        canonicalize_json_v2(raw)
    assert exc_info.value.code == code


def test_profile_identity_is_exact() -> None:
    assert CANONICALIZATION_ID == "VERA_MESH_CANONICAL_JSON_V2"


@pytest.mark.parametrize("name,input_hex,expected_hex,byte_count,sha256", POSITIVE_FIXTURES)
def test_accepted_positive_fixture_bytes_and_sha(
    name: str, input_hex: str, expected_hex: str, byte_count: int, sha256: str
) -> None:
    del name
    actual = canonicalize_json_v2(bytes.fromhex(input_hex))
    assert actual == bytes.fromhex(expected_hex)
    assert len(actual) == byte_count
    assert hashlib.sha256(actual).hexdigest() == sha256


@pytest.mark.parametrize("name,input_hex,code", HOSTILE_FIXTURES)
def test_accepted_hostile_fixture_typed_failure(name: str, input_hex: str, code: str) -> None:
    del name
    assert_code(bytes.fromhex(input_hex), code)


def test_trailing_json_whitespace_is_accepted_but_not_emitted() -> None:
    assert canonicalize_json_v2(b' { "b" : 2, "a" : 1 } \r\n\t') == b'{"a":1,"b":2}'


def test_bom_is_rejected_as_malformed_json() -> None:
    assert_code(b"\xef\xbb\xbf{}", "MALFORMED_JSON")


def test_negative_integer_zero_canonicalizes_to_zero() -> None:
    assert canonicalize_json_v2(b"-0") == b"0"


def test_raw_input_exact_65536_is_eligible_when_other_limits_hold() -> None:
    # 32,768-byte decoded string plus JSON quotes, with trailing JSON whitespace
    # bringing the received body to the exact inclusive raw-input ceiling.
    core = b'"' + (b"a" * 32768) + b'"'
    raw = core + (b" " * (65536 - len(core)))
    assert len(raw) == 65536
    assert canonicalize_json_v2(raw) == core


def test_raw_input_limit_precedes_invalid_utf8() -> None:
    assert_code(b"\xff" + (b" " * 65536), "CANONICAL_RESOURCE_LIMIT_EXCEEDED")


def test_invalid_utf8_before_later_string_limit_wins() -> None:
    raw = b'{"x":"\xff' + (b"a" * 32769) + b'"}'
    assert len(raw) <= 65536
    assert_code(raw, "INVALID_UTF8")


def test_string_limit_before_later_invalid_utf8_wins() -> None:
    raw = b'{"x":"' + (b"a" * 32769) + b'"\xff}'
    assert len(raw) <= 65536
    assert_code(raw, "CANONICAL_RESOURCE_LIMIT_EXCEEDED")


def test_malformed_token_before_later_invalid_utf8_wins() -> None:
    assert_code(b'{"x":}\xff', "MALFORMED_JSON")


def test_invalid_utf8_before_later_malformed_token_wins() -> None:
    assert_code(b'{"x":\xff}', "INVALID_UTF8")


def nested_array(depth: int) -> bytes:
    return (b"[" * depth) + b"0" + (b"]" * depth)


def test_depth_limit_exact_8_passes_and_9_fails() -> None:
    assert canonicalize_json_v2(nested_array(8)) == nested_array(8)
    assert_code(nested_array(9), "CANONICAL_RESOURCE_LIMIT_EXCEEDED")


def object_with_members(count: int) -> bytes:
    body = b",".join(f'"k{i:02d}":0'.encode() for i in range(count))
    return b"{" + body + b"}"


def test_object_member_limit_exact_32_passes_and_33_fails() -> None:
    assert canonicalize_json_v2(object_with_members(32)) == object_with_members(32)
    assert_code(object_with_members(33), "CANONICAL_RESOURCE_LIMIT_EXCEEDED")


def array_with_elements(count: int) -> bytes:
    return b"[" + b",".join(b"0" for _ in range(count)) + b"]"


def test_array_element_limit_exact_32_passes_and_33_fails() -> None:
    assert canonicalize_json_v2(array_with_elements(32)) == array_with_elements(32)
    assert_code(array_with_elements(33), "CANONICAL_RESOURCE_LIMIT_EXCEEDED")


def test_decoded_string_utf8_limit_exact_32768_passes_and_32769_fails() -> None:
    exact = b'{"x":"' + (b"a" * 32768) + b'"}'
    over = b'{"x":"' + (b"a" * 32769) + b'"}'
    assert canonicalize_json_v2(exact) == exact
    assert_code(over, "CANONICAL_RESOURCE_LIMIT_EXCEEDED")


def test_decoded_string_limit_counts_utf8_bytes_not_code_points() -> None:
    text = "é" * 16384
    raw = ('{"x":"' + text + '"}').encode("utf-8")
    assert canonicalize_json_v2(raw) == raw
    over = ('{"x":"' + text + 'a"}').encode("utf-8")
    assert_code(over, "CANONICAL_RESOURCE_LIMIT_EXCEEDED")


def test_large_escaped_source_is_measured_after_decode_for_string_limit() -> None:
    raw = b'{"x":"' + (b"\\u0061" * 6000) + b'"}'
    assert len(raw) > 32768
    assert len(raw) <= 65536
    expected = b'{"x":"' + (b"a" * 6000) + b'"}'
    assert canonicalize_json_v2(raw) == expected


def test_duplicate_detection_precedes_member_limit_at_member_32() -> None:
    parts = [b'"a":0']
    parts.extend(f'"k{i:02d}":0'.encode() for i in range(1, 31))
    parts.append(b'"\\u0061":1')
    raw = b"{" + b",".join(parts) + b"}"
    assert len(parts) == 32
    assert_code(raw, "DUPLICATE_OBJECT_KEY")


def test_noncanonical_integer_forms_are_malformed_not_silently_coerced() -> None:
    for raw in (b"+1", b"01", b"-01"):
        assert_code(raw, "MALFORMED_JSON")


def test_valid_utf8_non_json_token_is_malformed_json() -> None:
    assert_code("é".encode("utf-8"), "MALFORMED_JSON")


def array_prefix_32() -> bytes:
    return b"[" + b",".join(b"0" for _ in range(32))


def object_prefix_32() -> bytes:
    return b"{" + b",".join(f'"k{i:02d}":0'.encode() for i in range(32))


def test_array_element_33_must_be_complete_before_resource_rejection() -> None:
    assert_code(array_prefix_32() + b',"unterminated', "MALFORMED_JSON")
    assert_code(array_prefix_32() + b',"\xff"]', "INVALID_UTF8")
    assert_code(array_prefix_32() + b',"\\ud800"]', "INVALID_UNICODE_SCALAR")
    assert_code(array_prefix_32() + b',[1,2]]', "CANONICAL_RESOURCE_LIMIT_EXCEEDED")


def test_object_member_33_must_be_complete_before_resource_rejection() -> None:
    prefix = object_prefix_32()
    assert_code(prefix + b',"k32"}', "MALFORMED_JSON")
    assert_code(prefix + b',"k32":"unterminated', "MALFORMED_JSON")
    assert_code(prefix + b',"k32":"\xff"}', "INVALID_UTF8")
    assert_code(prefix + b',"k32":"\\ud800"}', "INVALID_UNICODE_SCALAR")
    assert_code(prefix + b',"k32":{"x":1}}', "CANONICAL_RESOURCE_LIMIT_EXCEEDED")
