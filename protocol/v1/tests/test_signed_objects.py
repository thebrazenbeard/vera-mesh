from __future__ import annotations

import base64, hashlib, json, unittest
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

ROOT = Path(__file__).parents[1]
FIX = ROOT / 'fixtures'


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def jcs(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def verify_p1363(spki_b64, signature_b64, data):
    key = serialization.load_der_public_key(base64.b64decode(spki_b64, validate=True))
    assert isinstance(key, ec.EllipticCurvePublicKey)
    assert key.curve.name == 'secp256r1'
    raw = base64.b64decode(signature_b64, validate=True)
    assert len(raw) == 64
    r = int.from_bytes(raw[:32], 'big')
    s = int.from_bytes(raw[32:], 'big')
    key.verify(utils.encode_dss_signature(r, s), data, ec.ECDSA(hashes.SHA256()))


class SignedObjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = load(ROOT / 'vectors' / 'object-signing-keys.json')

    def test_pairing_proof_is_real_and_verifiable(self):
        doc = load(FIX / 'pairing' / 'valid-redeem.json')
        unsigned = {k:v for k,v in doc.items() if k != 'proof'}
        verify_p1363(
            doc['signing_public_key_spki_base64'],
            doc['proof']['signature_base64'],
            b'veramesh-v1/pairing-redeem\n' + jcs(unsigned),
        )

    def test_pairing_session_pins_relay_custody_key(self):
        doc = load(FIX / 'pairing' / 'valid-create-session.json')
        raw = base64.b64decode(doc['relay_custody_public_key_spki_base64'], validate=True)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), doc['relay_custody_key_id'])
        self.assertGreaterEqual(doc['relay_custody_key_epoch'], 1)
        self.assertTrue(doc['relay_installation_id'])

    def test_receipt_signatures_are_real_and_verifiable(self):
        endpoint = self.keys['endpoint_test_signing_key']
        relay = self.keys['relay_test_custody_key']
        for name in ('valid-relay-custody.json','valid-recipient-storage.json','valid-recipient-processed.json'):
            with self.subTest(name=name):
                doc = load(FIX / 'receipts' / name)
                key = relay if doc['receipt_type'] == 'relay_custody' else endpoint
                self.assertEqual(doc['signer']['key_id'], key['key_id'])
                unsigned = {k:v for k,v in doc.items() if k != 'signature'}
                verify_p1363(key['spki_base64'], doc['signature']['signature_base64'], b'veramesh-v1/receipt\n' + jcs(unsigned))

    def test_inner_profile_requires_exact_jws_bytes(self):
        text = (ROOT / 'inner-envelope-profile.md').read_text(encoding='utf-8')
        self.assertIn('JWS Compact Serialization', text)
        self.assertIn('exact original application payload bytes', text)
        self.assertIn('MUST NOT parse the payload and reserialize it', text)



    def test_relay_custody_receipt_matches_pinned_trust_tuple(self):
        pairing = load(FIX / "pairing" / "valid-create-session.json")
        receipt = load(FIX / "receipts" / "valid-relay-custody.json")
        trusted = (pairing["relay_installation_id"],pairing["relay_custody_key_id"],pairing["relay_custody_key_epoch"])
        asserted = (receipt["relay_installation_id"],receipt["signer"]["key_id"],receipt["signer"]["key_epoch"])
        self.assertEqual(asserted, trusted)
        self.assertEqual(receipt["signer"]["principal"], "relay:" + trusted[0])
        self.assertEqual(receipt["signature"]["key_id"], receipt["signer"]["key_id"])
        scenario = load(FIX / "scenarios" / "relay-custody-trust-mismatch.json")
        for mismatch in scenario["mismatches"]:
            self.assertNotEqual((mismatch["relay_installation_id"],mismatch["signer_key_id"],mismatch["signer_key_epoch"]),trusted)

    def test_inner_jws_vector_verifies_original_payload_bytes_only(self):
        vector = load(ROOT / "vectors" / "inner-envelope-vectors.json")["vectors"][0]
        self.assertEqual(vector["signing_input"],vector["protected_header_base64url"]+"."+vector["payload_base64url"])
        self.assertEqual(vector["compact_jws"],vector["signing_input"]+"."+vector["signature_base64url"])
        signature_b64 = base64.urlsafe_b64encode(base64.urlsafe_b64decode(vector["signature_base64url"]+"="*(-len(vector["signature_base64url"])%4))).decode("ascii")
        verify_p1363(vector["key"]["spki_base64"],signature_b64,vector["signing_input"].encode("ascii"))
        reserialized = base64.urlsafe_b64encode(vector["reserialized_payload_utf8"].encode("utf-8")).rstrip(b"=")
        with self.assertRaises(Exception):
            verify_p1363(vector["key"]["spki_base64"],signature_b64,(vector["protected_header_base64url"]+"."+reserialized.decode("ascii")).encode("ascii"))

    def test_pairing_session_hashes_and_custody_key_are_bound(self):
        pairing = load(FIX / "pairing" / "valid-create-session.json")
        token = load(FIX / "pairing" / "valid-redeem.json")["pairing_token"]
        token_bytes = base64.urlsafe_b64decode(token+"="*(-len(token)%4))
        self.assertEqual(pairing["token_sha256"],hashlib.sha256(token_bytes).hexdigest())
        custody_spki = base64.b64decode(pairing["relay_custody_public_key_spki_base64"],validate=True)
        self.assertEqual(pairing["relay_custody_key_id"],hashlib.sha256(custody_spki).hexdigest())

if __name__ == '__main__':
    unittest.main(verbosity=2)
