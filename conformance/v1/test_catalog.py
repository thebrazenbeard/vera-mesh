"""Checks the implementation-independent V1 hostile conformance catalog."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
CATALOG = ROOT / "conformance/v1/cases.json"


class ConformanceCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads(CATALOG.read_text(encoding="utf-8"))

    def test_required_case_set_is_complete_and_unique(self):
        cases = self.catalog["cases"]
        ids = [case["id"] for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(self.catalog["required_case_ids"]), set(ids))
        self.assertEqual(len(self.catalog["required_case_ids"]), 24)

    def test_cases_are_unqualified_until_a_target_is_observed(self):
        permitted = set(self.catalog["statuses"])
        for case in self.catalog["cases"]:
            with self.subTest(case=case["id"]):
                self.assertIn(case["status"], permitted)
                self.assertEqual(case["status"], "UNRUN")
                self.assertIn("expected", case)
                self.assertIn("requires_external_target", case)

    def test_fixture_references_resolve(self):
        for case in self.catalog["cases"]:
            for reference in case["fixture_refs"]:
                path = reference.split("#", 1)[0]
                with self.subTest(case=case["id"], reference=reference):
                    self.assertTrue((ROOT / path).is_file(), reference)

    def test_runner_contract_is_present_and_safe(self):
        contract = ROOT / "conformance/v1/runner-contract.md"
        text = contract.read_text(encoding="utf-8")
        self.assertIn("black-box", text)
        self.assertIn("never installs a package", text)
        self.assertIn("must not record endpoint private keys", text)


if __name__ == "__main__":
    unittest.main()
