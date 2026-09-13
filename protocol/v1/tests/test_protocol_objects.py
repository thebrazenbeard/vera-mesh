"""Independent black-box checks for the VeraMesh V1 protocol objects."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "fixtures"
FORMAT_CHECKER = FormatChecker()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_json(value) -> bytes:
    """JCS-compatible for the protocol's restricted string/integer objects."""

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def signature_parameter(signature_input: str, name: str) -> str:
    match = re.search(rf";{name}=([^;]+)", signature_input)
    if not match:
        raise AssertionError(f"missing {name} in {signature_input!r}")
    value = match.group(1)
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def signature_bytes(signature_header: str) -> bytes:
    match = re.fullmatch(r"vera=:([^:]+):", signature_header)
    if not match:
        raise AssertionError(f"invalid Signature header: {signature_header!r}")
    return base64.b64decode(match.group(1), validate=True)


class ProtocolObjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = {
            name: read_json(ROOT / name)
            for name in (
                "message-envelope.schema.json",
                "receipt.schema.json",
                "pairing.schema.json",
            )
        }
        for schema in cls.schemas.values():
            Draft202012Validator.check_schema(schema)

    def validator(self, schema_name: str) -> Draft202012Validator:
        return Draft202012Validator(
            self.schemas[schema_name], format_checker=FORMAT_CHECKER
        )

    def test_manifest_validates_all_schema_fixtures(self):
        manifest = read_json(FIXTURES / "manifest.json")
        for entry in manifest["fixtures"]:
            with self.subTest(path=entry["path"]):
                instance = read_json(FIXTURES / entry["path"])
                errors = list(self.validator(entry["schema"]).iter_errors(instance))
                if entry["expected"] == "valid":
                    self.assertEqual(errors, [], errors)
                else:
                    self.assertTrue(errors, f"hostile fixture unexpectedly validated: {entry['path']}")

    def test_message_hashes_and_aad_are_bound(self):
        fields = (
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
        for name in ("valid-phone-to-host.json", "valid-host-to-phone.json"):
            with self.subTest(message=name):
                message = read_json(FIXTURES / "messages" / name)
                self.assertEqual(
                    {field: message[field] for field in fields}, message["aad_binding"]
                )
                self.assertEqual(
                    message["ciphertext_sha256"],
                    sha256_hex(base64.b64decode(message["ciphertext_base64"], validate=True)),
                )
                self.assertEqual(
                    message["aad_sha256"], sha256_hex(canonical_json(message["aad_binding"]))
                )
                without_hash = dict(message)
                without_hash.pop("envelope_sha256")
                self.assertEqual(
                    message["envelope_sha256"], sha256_hex(canonical_json(without_hash))
                )
                self.assertNotIn("direction", message)
                if message["sender_principal"].startswith("phone:"):
                    self.assertTrue(message["recipient_principal"].startswith("host:"))
                else:
                    self.assertTrue(message["sender_principal"].startswith("host:"))
                    self.assertTrue(message["recipient_principal"].startswith("phone:"))

    def test_authorization_matrix_derives_direction_and_forbids_remote_admin(self):
        matrix = read_json(ROOT / "authorization-matrix.json")
        self.assertTrue(matrix["authentication_grants_nothing"])
        classes = matrix["principal_classes"]
        self.assertEqual(classes["phone-client"]["derived_direction"], "phone-to-host")
        self.assertEqual(classes["phone-client"]["legal_recipient_class"], "vera-host")
        self.assertEqual(classes["vera-host"]["derived_direction"], "host-to-phone")
        self.assertEqual(classes["vera-host"]["legal_recipient_class"], "phone-client")
        self.assertFalse(classes["relay-admin"]["remote_application"])
        submit = next(item for item in matrix["operations"] if item["id"] == "submit_envelope")
        self.assertEqual(
            {rule["direction"] for rule in submit["allow"]}, {"phone-to-host", "host-to-phone"}
        )
        self.assertIn("body contains direction", submit["reject_if"])
        redeem = next(item for item in matrix["operations"] if item["id"] == "redeem_pairing")
        self.assertIn("redeem body requests a role", redeem["reject_if"])
        self.assertIn("redeem body requests scopes", redeem["reject_if"])

    def test_receipt_signer_and_transition_propositions_remain_distinct(self):
        custody = read_json(FIXTURES / "receipts/valid-relay-custody.json")
        storage = read_json(FIXTURES / "receipts/valid-recipient-storage.json")
        processed = read_json(FIXTURES / "receipts/valid-recipient-processed.json")

        self.assertEqual(custody["signer"]["role"], "relay-custody")
        self.assertEqual(custody["signer_binding"], "relay_custody")
        self.assertEqual(custody["transition"], {"from": "none", "to": "relay_accepted"})
        self.assertEqual(storage["signer"]["principal"], storage["recipient_principal"])
        self.assertEqual(processed["signer"]["principal"], processed["recipient_principal"])
        self.assertEqual(storage["transition"], {"from": "relay_accepted", "to": "recipient_stored"})
        self.assertEqual(processed["transition"], {"from": "recipient_stored", "to": "recipient_processed"})
        self.assertNotEqual(custody["receipt_type"], storage["receipt_type"])

    def test_pairing_key_id_is_derived_and_redeem_has_no_authority_request(self):
        redeem = read_json(FIXTURES / "pairing/valid-redeem.json")
        spki = base64.b64decode(redeem["signing_public_key_spki_base64"], validate=True)
        self.assertEqual(redeem["signing_key_id"], sha256_hex(spki))
        self.assertNotIn("enrollment_role", redeem)
        self.assertNotIn("enrollment_scopes", redeem)
        self.assertNotIn("requested_role", redeem)
        self.assertNotIn("direction", redeem)

    def test_idempotency_collision_is_a_conflict_not_a_duplicate(self):
        scenario = read_json(FIXTURES / "scenarios/idempotency-conflict.json")
        first = scenario["first"]
        retry = scenario["retry"]
        self.assertEqual(first["sender_principal"], retry["sender_principal"])
        self.assertEqual(first["message_id"], retry["message_id"])
        self.assertNotEqual(first["envelope_sha256"], retry["envelope_sha256"])
        self.assertEqual(scenario["expected_status"], 409)
        self.assertEqual(scenario["expected_error"], "idempotency_conflict")

    def test_sequence_gap_stays_observable_without_deadlocking_later_delivery(self):
        scenario = read_json(FIXTURES / "scenarios/sequence-gap-observable.json")
        self.assertEqual(scenario["received_sequences"], [1, 3])
        self.assertEqual(scenario["expected_delivery"], "sequence_3_remains_deliverable")
        self.assertEqual(scenario["expected_evidence"], "sequence_gap")

    def test_http_signature_vectors(self):
        document = read_json(ROOT / "vectors/http-signature-vectors.json")
        seen_nonces = set()
        by_id = {vector["id"]: vector for vector in document["vectors"]}
        for vector in document["vectors"]:
            with self.subTest(vector=vector["id"]):
                key = vector["key"]
                spki = base64.b64decode(key["spki_base64"], validate=True)
                self.assertEqual(key["key_id"], sha256_hex(spki))
                request = vector["request"]
                body = request["body_utf8"].encode("utf-8")
                digest = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
                digest_header = request["content_digest"]
                self.assertTrue(digest_header.startswith("sha-256=:"))
                supplied_digest = digest_header[len("sha-256=:") : -1]
                self.assertEqual(request["content_digest"].endswith(":") , True)
                digest_matches = supplied_digest == digest

                signature_input = request["signature_input"]
                self.assertTrue(signature_input.startswith("vera=("))
                self.assertIn('alg="ecdsa-p256-sha256"', signature_input)
                self.assertIn('tag="veramesh-v1"', signature_input)
                nonce = signature_parameter(signature_input, "nonce")

                if vector["expected"] == "ACCEPT":
                    self.assertTrue(digest_matches)
                    self.assertEqual(request["method"], "POST")
                    self.assertEqual(request["path"], "/v1/mailbox/envelopes")
                    self.assertEqual(request["query"], "")
                    self.assertNotIn(nonce, seen_nonces)
                    seen_nonces.add(nonce)
                    created = int(signature_parameter(signature_input, "created"))
                    expires = int(signature_parameter(signature_input, "expires"))
                    now = vector["evaluation_time"]
                    self.assertGreater(expires, created)
                    self.assertLessEqual(expires - created, 300)
                    self.assertLessEqual(created, now + 30)
                    self.assertGreaterEqual(expires, now)

                    public_key = serialization.load_der_public_key(spki)
                    self.assertIsInstance(public_key, ec.EllipticCurvePublicKey)
                    self.assertEqual(public_key.curve.name, "secp256r1")
                    raw_signature = signature_bytes(request["signature"])
                    self.assertEqual(len(raw_signature), 64)
                    r = int.from_bytes(raw_signature[:32], "big")
                    s = int.from_bytes(raw_signature[32:], "big")
                    der_signature = utils.encode_dss_signature(r, s)
                    self.assertEqual(vector["signature_base"], self.signature_base(vector))
                    public_key.verify(
                        der_signature,
                        vector["signature_base"].encode("utf-8"),
                        ec.ECDSA(hashes.SHA256()),
                    )
                else:
                    reason = vector["rejection"]
                    if reason == "wrong_key_type_or_curve":
                        public_key = serialization.load_der_public_key(spki)
                        is_p256 = isinstance(public_key, ec.EllipticCurvePublicKey) and public_key.curve.name == "secp256r1"
                        self.assertFalse(is_p256)
                    elif reason == "content_digest_mismatch":
                        self.assertFalse(digest_matches)
                    elif reason == "freshness":
                        created = int(signature_parameter(signature_input, "created"))
                        expires = int(signature_parameter(signature_input, "expires"))
                        self.assertLess(expires, vector["evaluation_time"])
                        self.assertLessEqual(expires - created, 300)
                    elif reason == "nonce_replay":
                        replay_of = by_id[vector["replay_of"]]
                        replay_nonce = signature_parameter(
                            replay_of["request"]["signature_input"], "nonce"
                        )
                        self.assertIn(replay_nonce, seen_nonces)
                    else:
                        self.fail(f"unhandled vector rejection: {reason}")

    @staticmethod
    def signature_base(vector) -> str:
        request = vector["request"]
        params = request["signature_input"][len("vera=") :]
        return "\n".join(
            (
                f'"@method": {request["method"]}',
                f'"@path": {request["path"]}',
                f'"content-digest": {request["content_digest"]}',
                f'"content-type": {request["content_type"]}',
                f'"@signature-params": {params}',
            )
        )


if __name__ == "__main__":
    unittest.main()
