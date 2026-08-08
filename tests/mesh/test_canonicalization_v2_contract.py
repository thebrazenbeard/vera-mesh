import pytest

from vera_mesh.digests import DigestValidationError, canonical_json_bytes


@pytest.mark.parametrize("value", [0.0, -1.5, 1e6])
def test_canonical_json_rejects_all_floating_point_numbers(value) -> None:
    with pytest.raises(DigestValidationError, match="floating-point numbers are forbidden"):
        canonical_json_bytes(value)


@pytest.mark.parametrize(
    "value",
    [
        chr(0xD800),
        chr(0xDFFF),
        {chr(0xD800): "value"},
        {"key": chr(0xDFFF)},
    ],
)
def test_canonical_json_rejects_unpaired_surrogates_with_typed_error(value) -> None:
    with pytest.raises(DigestValidationError, match="invalid Unicode scalar"):
        canonical_json_bytes(value)


def test_canonical_json_preserves_valid_astral_scalars_as_literal_utf8() -> None:
    value = {"emoji": "\U0001F600", "key\U0001F4A9": "value"}
    assert canonical_json_bytes(value) == (
        '{"emoji":"\U0001F600","key\U0001F4A9":"value"}'.encode("utf-8")
    )
