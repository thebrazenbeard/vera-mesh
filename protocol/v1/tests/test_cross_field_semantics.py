from __future__ import annotations

import json
from pathlib import Path

import pytest

from semantic_checks import (
    validate_message_envelope_cross_fields,
    validate_receipt_cross_fields,
)


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "fixtures"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_valid_message_fixtures_satisfy_outer_to_aad_binding():
    for name in ("valid-phone-to-host.json", "valid-host-to-phone.json"):
        message = read_json(FIXTURES / "messages" / name)
        validate_message_envelope_cross_fields(message)


def test_outer_recipient_cannot_diverge_from_authenticated_aad_binding():
    message = read_json(FIXTURES / "messages" / "valid-phone-to-host.json")
    message["recipient_principal"] = "host:alternate"
    assert message["recipient_principal"] != message["aad_binding"]["recipient_principal"]
    with pytest.raises(ValueError, match="aad_binding mismatch for recipient_principal"):
        validate_message_envelope_cross_fields(message)


def test_valid_recipient_receipts_bind_signer_to_recipient_and_signature_key():
    for name in ("valid-recipient-storage.json", "valid-recipient-processed.json"):
        receipt = read_json(FIXTURES / "receipts" / name)
        validate_receipt_cross_fields(receipt)


def test_recipient_receipt_rejects_schema_valid_wrong_signer_principal():
    receipt = read_json(FIXTURES / "receipts" / "valid-recipient-storage.json")
    original = receipt["signer"]["principal"]
    receipt["signer"]["principal"] = (
        "host:alternate" if original.startswith("host:") else "phone:alternate"
    )
    assert receipt["signer"]["principal"] != receipt["recipient_principal"]
    with pytest.raises(
        ValueError, match="signer principal must equal recipient_principal"
    ):
        validate_receipt_cross_fields(receipt)


def test_recipient_receipt_rejects_schema_valid_signature_key_mismatch():
    receipt = read_json(FIXTURES / "receipts" / "valid-recipient-storage.json")
    replacement = "f" * 64
    if replacement == receipt["signer"]["key_id"]:
        replacement = "e" * 64
    receipt["signature"]["key_id"] = replacement
    assert receipt["signature"]["key_id"] != receipt["signer"]["key_id"]
    with pytest.raises(ValueError, match="signature key_id must equal signer key_id"):
        validate_receipt_cross_fields(receipt)
