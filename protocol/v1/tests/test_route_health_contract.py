"""Source-contract tests for VeraMesh route health/convergence V1."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[3]
CONTRACT = ROOT / "protocol/v1/route-health-contract.json"


class RouteHealthContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.value = json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_handshake_cannot_establish_data_plane_health(self):
        self.assertFalse(
            self.value["data_plane_verification"]["handshake_only_is_sufficient"]
        )
        self.assertIn(
            "HANDSHAKE_SUCCESS_NE_DATA_PLANE_HEALTH",
            self.value["separations"],
        )

    def test_last_known_good_requires_fresh_pass(self):
        lkg = self.value["last_known_good"]
        self.assertTrue(lkg["may_be_probe_priority"])
        self.assertFalse(lkg["may_become_current_without_fresh_data_plane_pass"])
        self.assertFalse(lkg["stale_success_is_currentness_evidence"])

    def test_failover_requires_data_plane_convergence(self):
        convergence = self.value["convergence"]
        self.assertTrue(convergence["alternate_route_requires_fresh_data_plane_pass"])
        self.assertFalse(
            convergence["control_plane_reachability_alone_may_promote_alternate"]
        )
        self.assertEqual(
            convergence["no_verified_alternate_result"],
            "ROUTE_UNAVAILABLE_FAIL_CLOSED",
        )

    def test_freshness_policy_is_bound_not_hardcoded(self):
        freshness = self.value["data_plane_verification"]["freshness"]
        self.assertTrue(freshness["required"])
        self.assertTrue(freshness["numeric_window_is_policy_defined"])
        self.assertTrue(freshness["policy_digest_required"])

    def test_route_health_never_grants_effect_authority(self):
        authority = self.value["authority"]
        self.assertFalse(authority["route_health_grants_application_authority"])
        self.assertFalse(authority["route_selection_grants_protected_effect_authority"])
        self.assertFalse(authority["transport_membership_grants_vera_authority"])

    def test_external_sources_are_research_only(self):
        by_repo = {
            row["repository"]: row
            for row in self.value["external_pattern_provenance"]
        }
        self.assertFalse(by_repo["encodeous/nylon"]["code_imported"])
        self.assertFalse(by_repo["CluvexStudio/Aether"]["code_imported"])
        self.assertIn("NO_CODE_IMPORT", by_repo["CluvexStudio/Aether"]["license_note"])


if __name__ == "__main__":
    unittest.main()
