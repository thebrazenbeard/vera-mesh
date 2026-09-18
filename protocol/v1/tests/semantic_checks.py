from __future__ import annotations


AAD_FIELDS = (
    "protocol_version",
    "message_id",
    "stream_id",
    "message_sequence",
    "message_type",
    "sender_principal",
    "sender_signing_key_id",
    "sender_signing_key_epoch",
    "recipient_principal",
    "recipient_hpke_key_id",
    "recipient_hpke_key_epoch",
)


def validate_message_envelope_cross_fields(message: dict) -> None:
    aad = message.get("aad_binding")
    if not isinstance(aad, dict):
        raise ValueError("aad_binding must be an object")
    for field in AAD_FIELDS:
        if message.get(field) != aad.get(field):
            raise ValueError(f"aad_binding mismatch for {field}")


def validate_receipt_cross_fields(receipt: dict) -> None:
    signer = receipt.get("signer")
    signature = receipt.get("signature")
    if not isinstance(signer, dict) or not isinstance(signature, dict):
        raise ValueError("receipt signer and signature must be objects")

    if signature.get("key_id") != signer.get("key_id"):
        raise ValueError("receipt signature key_id must equal signer key_id")

    if receipt.get("receipt_type") in {"recipient_storage", "recipient_processed"}:
        if signer.get("principal") != receipt.get("recipient_principal"):
            raise ValueError(
                "recipient receipt signer principal must equal recipient_principal"
            )
