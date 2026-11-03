"""Readiness must never be inferred from offline doctor success alone."""

import asyncio
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from veraport_agent.doctor import DoctorCheck, doctor, remote_ready_from_checks


NAMES = (
    "service_config", "controller_config", "tunnel_config", "cross_binding",
    "process_policy", "windows_acl", "live_veraport", "tunnel_runtime",
)


def checks(*, override=None):
    override = override or {}
    return [DoctorCheck(name, override.get(name, "PASS"), "test") for name in NAMES]


class DoctorRemoteReadyContractTests(TestCase):
    def test_all_required_check_receipts_pass(self):
        self.assertTrue(remote_ready_from_checks(checks()))

    def test_any_skip_or_failure_denies_remote_readiness(self):
        for name in NAMES:
            for state in ("SKIP", "FAIL", "UNKNOWN", "ready", ""):
                with self.subTest(name=name, state=state):
                    self.assertFalse(remote_ready_from_checks(checks(override={name: state})))

    def test_missing_duplicate_unexpected_and_malformed_receipts_fail_closed(self):
        valid = checks()
        for malformed in (
            valid[:-1],
            valid + [valid[0]],
            valid + [DoctorCheck("synthetic", "PASS", "test")],
            [DoctorCheck("live_veraport", "PASS", "test"), DoctorCheck("tunnel_runtime", "PASS", "test")],
            valid[:-1] + [DoctorCheck("tunnel_runtime", True, "test")],
            valid[:-1] + ["tunnel_runtime"],
        ):
            with self.subTest(length=len(malformed)):
                self.assertFalse(remote_ready_from_checks(malformed))

    def test_offline_success_does_not_claim_remote_readiness(self):
        offline = checks()[:-2]
        with patch("veraport_agent.doctor.offline_doctor", return_value=(offline, None, None, None)):
            result = asyncio.run(doctor(live=False, tunnel_status=False))
        self.assertTrue(result["ok"])  # Existing offline-diagnostic semantics retained.
        self.assertFalse(result["remote_ready"])
        self.assertEqual(result["schema"], "VERAMESH_DOCTOR_V1")

    def test_two_live_checks_and_all_offline_checks_are_required(self):
        offline = checks()[:-2]

        class Runtime:
            async def machine_info(self):
                return {
                    "selected_path_id": "synthetic-path",
                    "paths": [{"path_id": "synthetic-path", "authenticated": True, "data_plane_verified": True}],
                }

            async def close(self):
                pass

        with (
            patch("veraport_agent.doctor.offline_doctor", return_value=(offline, None, object(), object())),
            patch("veraport_agent.doctor.ControllerRuntime", return_value=Runtime()),
            patch("veraport_agent.doctor.read_tunnel_status", return_value=SimpleNamespace(
                usable=True, process_running=True, healthy=True, ready=True,
            )),
        ):
            result = asyncio.run(doctor(live=True, tunnel_status=True))
        self.assertTrue(result["remote_ready"])
        self.assertTrue(result["ok"])
        # This is synthetic local diagnosis, not an attestation or authorization grant.

