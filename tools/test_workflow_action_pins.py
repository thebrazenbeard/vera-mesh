"""Reject mutable external action refs in repository GitHub workflows."""

from pathlib import Path
import re
import unittest


USES_LINE = re.compile(r"^\s*(?:-\s*)?uses\s*:\s*([^\s#]+)")
PINNED_REMOTE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def is_immutable_action_ref(ref: str) -> bool:
    """Local actions are source-bound; external actions must use commit SHAs."""
    return ref.startswith("./") or PINNED_REMOTE.fullmatch(ref) is not None


class WorkflowActionPinTests(unittest.TestCase):
    def test_tag_and_short_sha_are_rejected(self) -> None:
        self.assertFalse(is_immutable_action_ref("actions/checkout@v4"))
        self.assertFalse(is_immutable_action_ref("actions/checkout@123abc"))
        self.assertTrue(is_immutable_action_ref("./.github/actions/local-check"))
        self.assertTrue(is_immutable_action_ref("actions/checkout@" + "a" * 40))

    def test_uses_key_spacing_is_detected(self) -> None:
        match = USES_LINE.match("      - uses : actions/checkout@v4")
        self.assertIsNotNone(match)
        self.assertEqual("actions/checkout@v4", match.group(1))

    def test_all_workflow_actions_are_immutable(self) -> None:
        workflows = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        files = sorted((*workflows.glob("*.yml"), *workflows.glob("*.yaml")))
        self.assertTrue(files, "Expected GitHub workflows to check")

        refs = 0
        errors: list[str] = []
        for file in files:
            for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
                match = USES_LINE.match(line)
                if match is None:
                    continue
                refs += 1
                ref = match.group(1)
                if not is_immutable_action_ref(ref):
                    errors.append(f"{file.name}:{number}: {ref}")

        self.assertGreater(refs, 0, "Expected at least one action reference")
        self.assertEqual([], errors, "Unpinned GitHub Actions:\n" + "\n".join(errors))


if __name__ == "__main__":
    unittest.main()
