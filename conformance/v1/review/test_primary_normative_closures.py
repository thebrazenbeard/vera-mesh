"""Independent executable checks for open VeraMesh V1 normative closures.

Set VERAMESH_PRIMARY_TREE to a checkout of the primary work branch.  These
checks intentionally inspect only protocol/v1: review prose cannot satisfy a
normative closure.
"""

from __future__ import annotations

import base64
import json
import os
import re
import unittest
from pathlib import Path


TARGET_ENV = "VERAMESH_PRIMARY_TREE"


def _all_signature_values(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "signature_base64" and isinstance(child, str):
                yield child
            yield from _all_signature_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_signature_values(child)


@unittest.skipUnless(os.environ.get(TARGET_ENV), f"set {TARGET_ENV} to run")
class PrimaryNormativeClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(os.environ[TARGET_ENV]).resolve()
        cls.protocol = cls.root / "protocol" / "v1"
        if not cls.protocol.is_dir():
            raise AssertionError(f"missing protocol/v1 under {cls.root}")

    def read(self, relative: str) -> str:
        return (self.protocol / relative).read_text(encoding="utf-8")

    def all_normative_text(self) -> str:
        chunks = []
        for path in self.protocol.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".md", ".json"}:
                chunks.append(path.read_text(encoding="utf-8"))
        return "\n".join(chunks).lower()

    def test_cross_relay_binding_is_in_signed_components(self):
        profile = self.read("http-signature-profile.md").lower()
        match = re.search(
            r"covered component list is exactly:\s*```text\s*(.*?)```",
            profile,
            flags=re.S,
        )
        self.assertIsNotNone(match, "mailbox covered-component list not found")
        covered = match.group(1)
        self.assertRegex(
            covered,
            r"(@authority|relay[-_][a-z0-9_-]*|x-[a-z0-9_-]*relay[a-z0-9_-]*)",
            "protected mailbox request is not signed to a relay/destination identity",
        )

    def test_content_digest_extra_member_policy_is_explicit(self):
        profile = self.read("http-signature-profile.md").lower()
        digest_sentences = " ".join(
            sentence
            for sentence in re.split(r"(?<=[.!?])\s+", profile)
            if "content-digest" in sentence or "digest" in sentence
        )
        self.assertRegex(
            digest_sentences,
            r"(extra|additional|other).{0,120}(reject|ignore)|(reject|ignore).{0,120}(extra|additional|other)",
            "SHA-256-only does not say what to do with additional RFC 9530 members",
        )

    def test_pairing_bootstrap_pins_relay_custody_identity(self):
        pairing = self.read("pairing.schema.json").lower()
        for token in ("relay_installation", "custody", "key_epoch"):
            with self.subTest(token=token):
                self.assertIn(token, pairing)

    def test_inner_signature_exact_bytes_are_normative(self):
        normative = self.all_normative_text()
        self.assertRegex(normative, r"inner.{0,80}signature|signature.{0,80}inner")
        self.assertRegex(
            normative,
            r"(exact|original).{0,80}(utf-8|bytes)|(?:utf-8|bytes).{0,80}(exact|original)",
            "inner authorship signature lacks an exact-byte verification rule",
        )
        self.assertRegex(
            normative,
            r"(before parsing|before.*json|do not reserial|must not reserial|without reserial)",
            "inner signature verification does not forbid parse/reserialize verification",
        )

    def test_pairing_proof_has_defined_signature_base(self):
        normative = self.all_normative_text()
        self.assertRegex(
            normative,
            r"pairing.{0,240}(signature base|bytes to sign|signed bytes)|"
            r"(signature base|bytes to sign|signed bytes).{0,240}pairing",
            "pairing proof has a signature value but no normative bytes-to-sign contract",
        )

    def test_receipts_have_defined_signature_base(self):
        normative = self.all_normative_text()
        self.assertRegex(
            normative,
            r"receipt.{0,240}(signature base|bytes to sign|signed bytes)|"
            r"(signature base|bytes to sign|signed bytes).{0,240}receipt",
            "receipt signatures have no normative bytes-to-sign contract",
        )

    def test_valid_crypto_fixtures_do_not_use_zero_signatures(self):
        fixture_roots = [
            self.protocol / "fixtures" / "pairing",
            self.protocol / "fixtures" / "receipts",
        ]
        checked = 0
        for root in fixture_roots:
            for path in sorted(root.glob("valid-*.json")):
                document = json.loads(path.read_text(encoding="utf-8"))
                for encoded in _all_signature_values(document):
                    raw = base64.b64decode(encoded, validate=True)
                    checked += 1
                    with self.subTest(path=path.name):
                        self.assertTrue(any(raw), f"{path.name} uses an all-zero signature")
        self.assertGreater(checked, 0, "no valid cryptographic fixture signatures were checked")

    def test_runner_pair_role_is_not_remote_authority(self):
        contract_path = self.root / "conformance" / "v1" / "runner-contract.md"
        if not contract_path.is_file():
            self.skipTest("primary tree has no runner contract")
        contract = contract_path.read_text(encoding="utf-8").lower()
        self.assertIn("pair(role)", contract)
        self.assertRegex(
            contract,
            r"pair\(role\).{0,500}(local[- ]admin|predetermined|test harness)|"
            r"(local[- ]admin|predetermined|test harness).{0,500}pair\(role\)",
            "runner adapter must say role is local-admin/test setup, not caller authority",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
