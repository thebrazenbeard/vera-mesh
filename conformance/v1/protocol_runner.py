"""Independent target-free VeraMesh V1 protocol-fixture runner.

This runner consumes only the published protocol contract. It does not import
VeraRelay internals, access a target database, install software, start a
service, or make network/provider changes. PASS means only that the fixed
protocol fixture is internally coherent; it is not runtime conformance.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).parents[2]
PROTOCOL = ROOT / "protocol" / "v1"
FORMAT_CHECKER = FormatChecker()
TARGET = {
    "implementation": "vera-mesh-protocol-fixture-runner",
    "source_commit": None,
    "artifact_sha256": None,
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def jcs(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def decode_b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_p1363(spki_b64: str, signature_b64url: str, data: bytes) -> None:
    key = serialization.load_der_public_key(
        base64.b64decode(spki_b64, validate=True)
    )
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise AssertionError("not an EC public key")
    if key.curve.name != "secp256r1":
        raise AssertionError("not a P-256 public key")
    raw = decode_b64url(signature_b64url)
    if len(raw) != 64:
        raise AssertionError("not a 64-byte P1363 signature")
    der = utils.encode_dss_signature(
        int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    )
    key.verify(der, data, ec.ECDSA(hashes.SHA256()))


def vector_from_reference(reference: str):
    path_text, fragment = reference.split("#", 1)
    document = load_json(ROOT / path_text)
    if "vectors" not in document:
        raise AssertionError(f"not a vector document: {reference}")
    for vector in document["vectors"]:
        if vector["id"] == fragment:
            return vector
    raise AssertionError(f"missing vector fragment: {reference}")


def schema_rejects(path: str, schema_name: str) -> bool:
    document = load_json(ROOT / path)
    schema = load_json(PROTOCOL / schema_name)
    return bool(
        list(
            Draft202012Validator(schema, format_checker=FORMAT_CHECKER).iter_errors(
                document
            )
        )
    )


def run_non_device_case(case: dict) -> tuple[str, list[str]]:
    case_id = case["id"]
    if case["requires_external_target"]:
        return "UNRUN", ["requires_external_target=true"]

    if case_id in {
        "auth-wrong-curve-p384",
        "auth-wrong-key-type-rsa",
        "auth-digest-mismatch",
        "auth-stale-request",
        "auth-cross-relay-binding",
        "auth-content-digest-extra-member",
    }:
        vector = vector_from_reference(case["fixture_refs"][0])
        reason = vector["rejection"]
        request = vector["request"]
        if reason == "wrong_key_type_or_curve":
            key = serialization.load_der_public_key(
                base64.b64decode(vector["key"]["spki_base64"], validate=True)
            )
            is_p256 = (
                isinstance(key, ec.EllipticCurvePublicKey)
                and key.curve.name == "secp256r1"
            )
            if is_p256:
                raise AssertionError("wrong-key vector is P-256")
            return "PASS", ["rejected non-P-256 key before operation mutation"]
        if reason == "content_digest_mismatch":
            actual = base64.b64encode(
                hashlib.sha256(request["body_utf8"].encode("utf-8")).digest()
            ).decode("ascii")
            supplied = request["content_digest"][len("sha-256=:"):-1]
            if actual == supplied:
                raise AssertionError("digest-mismatch vector unexpectedly matches")
            return "PASS", ["received-body digest differs from signed digest"]
        if reason == "freshness":
            created = int(vector["request"]["signature_input"].split("created=", 1)[1].split(";", 1)[0])
            expires = int(vector["request"]["signature_input"].split("expires=", 1)[1].split(";", 1)[0])
            if expires >= vector["evaluation_time"] or expires <= created:
                raise AssertionError("stale vector does not encode expired freshness")
            return "PASS", ["expires before evaluation time"]
        if reason == "relay_binding_mismatch":
            if request["relay_id"] == vector["trusted_relay_id"]:
                raise AssertionError("relay binding vector names the trusted relay")
            if "x-veramesh-relay-id" not in request["signature_input"]:
                raise AssertionError("relay ID is not a covered component")
            return "PASS", ["request relay binding differs from pinned identity"]
        if reason == "content_digest_extra_member":
            if request.get("content_digest_member_count") != 2:
                raise AssertionError("extra-member vector does not declare two members")
            if "," not in request["content_digest"]:
                raise AssertionError("extra-member vector has no additional member")
            return "PASS", ["extra RFC 9530 member is rejected by exact-one policy"]
        raise AssertionError(f"unhandled authentication vector: {reason}")

    if case_id == "authorization-role-escalation":
        if not schema_rejects("protocol/v1/fixtures/pairing/invalid-role-escalation.json","pairing.schema.json"):
            raise AssertionError("role-escalation fixture unexpectedly validates")
        return "PASS", ["remote authority claim rejected by pairing schema"]

    if case_id == "receipt-regression":
        if not schema_rejects("protocol/v1/fixtures/receipts/invalid-regressed-transition.json","receipt.schema.json"):
            raise AssertionError("regressed receipt unexpectedly validates")
        return "PASS", ["backwards receipt transition rejected by schema"]

    if case_id == "message-inner-signature-reserialization":
        vector = vector_from_reference(case["fixture_refs"][0])
        original_input = vector["signing_input"].encode("ascii")
        expected_input = (vector["protected_header_base64url"]+"."+vector["payload_base64url"]).encode("ascii")
        if original_input != expected_input:
            raise AssertionError("inner signing input is not exact compact bytes")
        verify_p1363(vector["key"]["spki_base64"],vector["signature_base64url"],original_input)
        changed_input = (vector["protected_header_base64url"]+"."+base64.urlsafe_b64encode(vector["reserialized_payload_utf8"].encode("utf-8")).rstrip(b"=")).encode("ascii")
        try:
            verify_p1363(vector["key"]["spki_base64"],vector["signature_base64url"],changed_input)
        except Exception:
            return "PASS", ["original JWS verifies; parse/reserialize bytes reject"]
        raise AssertionError("reserialized payload unexpectedly verifies")

    if case_id == "message-jcs-integer-boundary":
        vector = vector_from_reference(case["fixture_refs"][0])
        if vector["value"] != vector["limit"] + 1:
            raise AssertionError("boundary vector is not the first out-of-range integer")
        if not schema_rejects(case["fixture_refs"][1], "message-envelope.schema.json"):
            raise AssertionError("out-of-range integer fixture unexpectedly validates")
        return "PASS", ["out-of-range JCS integer is rejected by schema"]

    if case_id == "receipt-custody-trust-bootstrap":
        scenario = load_json(ROOT / case["fixture_refs"][0])
        trusted = scenario["trusted_tuple"]
        matching = scenario["matching_receipt"]
        receipt = load_json(PROTOCOL / "fixtures" / "receipts" / "valid-relay-custody.json")
        keys = load_json(PROTOCOL / "vectors" / "object-signing-keys.json")
        relay_key = keys["relay_test_custody_key"]
        if schema_rejects(
            "protocol/v1/fixtures/receipts/valid-relay-custody.json",
            "receipt.schema.json",
        ):
            raise AssertionError("valid custody receipt fails its normative schema")
        actual = (
            receipt["relay_installation_id"],
            receipt["signer"]["key_id"],
            receipt["signer"]["key_epoch"],
        )
        expected = (
            trusted["relay_installation_id"],
            trusted["custody_key_id"],
            trusted["custody_key_epoch"],
        )
        if actual != expected:
            raise AssertionError("custody receipt does not match pinned trust tuple")
        if (matching["relay_installation_id"], matching["signer_key_id"], matching["signer_key_epoch"]) != actual:
            raise AssertionError("matching scenario does not identify the actual receipt")
        if receipt["signer"]["principal"] != "relay:" + trusted["relay_installation_id"]:
            raise AssertionError("custody signer principal is not bound to installation")
        if receipt["signature"]["key_id"] != receipt["signer"]["key_id"]:
            raise AssertionError("custody signature key is not bound to signer")
        spki = base64.b64decode(relay_key["spki_base64"], validate=True)
        if hashlib.sha256(spki).hexdigest() != relay_key["key_id"]:
            raise AssertionError("custody SPKI hash does not match catalog key ID")
        if relay_key["key_id"] != trusted["custody_key_id"]:
            raise AssertionError("custody catalog key is not the pinned trust key")
        unsigned = {key: value for key, value in receipt.items() if key != "signature"}
        verify_p1363(
            relay_key["spki_base64"],
            receipt["signature"]["signature_base64"],
            b"veramesh-v1/receipt\n" + jcs(unsigned),
        )
        for mismatch in scenario["mismatches"]:
            if (
                mismatch["relay_installation_id"] == trusted["relay_installation_id"]
                and mismatch["signer_key_id"] == trusted["custody_key_id"]
                and mismatch["signer_key_epoch"] == trusted["custody_key_epoch"]
            ):
                raise AssertionError("trust mismatch fixture contains a matching tuple")
        return "PASS", ["signed custody receipt and trust tuple are mutually bound"]

    if case_id == "mailbox-custody-retry-equivalence":
        scenario = load_json(ROOT / case["fixture_refs"][0])
        if scenario["original"] != scenario["retry"]:
            raise AssertionError("retry identity changed")
        if not scenario["expected_same_receipt_bytes"] or not scenario["custody_receipt_is_relay_generated"]:
            raise AssertionError("retry contract is not exact-byte relay-generated custody")
        return "PASS", ["same immutable identity returns original receipt bytes"]

    if case_id == "mailbox-gc-requires-verified-storage-receipt":
        scenario = load_json(ROOT / case["fixture_refs"][0])
        if (scenario["verified_receipt_required"] != "recipient_storage" or not scenario["gc_blocked_without_verified_receipt"] or scenario["recipient_processed_is_sufficient"] or not scenario["relay_must_match_recipient_principal_and_endpoint_key"]):
            raise AssertionError("GC receipt gate is underspecified")
        return "PASS", ["GC requires independently verified recipient storage"]

    raise AssertionError(f"no target-free runner for {case_id}")


def run_catalog() -> list[dict]:
    catalog = load_json(ROOT / "conformance" / "v1" / "cases.json")
    records = []
    now = datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    for case in catalog["cases"]:
        try:
            status, observations = run_non_device_case(case)
            error = None
        except Exception as exc:
            status, observations, error = "FAIL", [], str(exc)
        records.append({"case_id":case["id"],"status":status,"qualification_scope":"protocol-fixture-only","target":TARGET,"observations":observations,"error":error,"started_at":now,"finished_at":now})
    return records


def main() -> int:
    records = run_catalog()
    for record in records:
        print(json.dumps(record, sort_keys=True))
    return 1 if any(record["status"] == "FAIL" for record in records) else 0


if __name__ == "__main__":
    sys.exit(main())
