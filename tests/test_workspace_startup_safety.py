"""Prevent unreviewed editor-triggered execution in this repository checkout.

This checks source configuration only. It does not attest installed VS Code
behavior, user settings, already-executed processes, or host compromise.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_jsonc(path: Path) -> dict:
    # Both checked-in files are small JSONC-like VS Code configs, with a
    # trailing comma accepted by VS Code. Do not execute workspace tasks.
    # A UTF-8 BOM must not mask a malicious folder-open task from review.
    raw = path.read_text(encoding="utf-8-sig")
    return json.loads(re.sub(r",\s*(?=[}\]])", "", raw))


def is_executable_font_task(task: dict) -> bool:
    # VS Code lets platform blocks override command and/or args. Inspect each
    # effective invocation, not only the generic command (or its first token).
    for platform in (None, "windows", "osx", "linux"):
        variant = task if platform is None else task.get(platform, {})
        if not isinstance(variant, dict):
            return True
        command = str(variant.get("command", task.get("command", "")))
        args = variant.get("args", task.get("args", []))
        if not isinstance(args, list):
            return True
        invocation = " ".join([command, *(str(arg) for arg in args)]).replace("\\", "/").lower()
        if ("public/fonts/fa-solid.lkf" in invocation or re.search(
            r"\bnode(?:\.exe)?\b[^\n]*\.(?:ttf|otf|woff2?|lkf)\b", invocation
        )):
            return True
    return False


def has_plausible_sfnt_directory(data: bytes) -> bool:
    """Validate container structure, not merely four magic bytes.

    This intentionally does not claim cryptographic authenticity or validate
    every font table; it rejects arbitrary JavaScript disguised as a .ttf.
    """
    if len(data) < 12:
        return False
    if data[:4] == b"ttcf":
        if len(data) < 16:
            return False
        count = int.from_bytes(data[8:12], "big")
        if not 1 <= count <= 64 or 12 + 4 * count > len(data):
            return False
        offsets = [
            int.from_bytes(data[12 + i * 4:16 + i * 4], "big")
            for i in range(count)
        ]
    else:
        offsets = [0]

    for base in offsets:
        if base + 12 > len(data) or data[base:base + 4] not in (
            b"\x00\x01\x00\x00", b"OTTO", b"true",
        ):
            return False
        num = int.from_bytes(data[base + 4:base + 6], "big")
        if not 1 <= num <= 256:
            return False
        power = 1 << (num.bit_length() - 1)
        search_range = int.from_bytes(data[base + 6:base + 8], "big")
        entry_selector = int.from_bytes(data[base + 8:base + 10], "big")
        range_shift = int.from_bytes(data[base + 10:base + 12], "big")
        if (search_range, entry_selector, range_shift) != (
            power * 16, power.bit_length() - 1, num * 16 - power * 16
        ):
            return False
        directory_end = base + 12 + num * 16
        if directory_end > len(data):
            return False
        tags = set()
        for entry in range(base + 12, directory_end, 16):
            tag = data[entry:entry + 4]
            table_offset = int.from_bytes(data[entry + 8:entry + 12], "big")
            length = int.from_bytes(data[entry + 12:entry + 16], "big")
            if tag in tags or table_offset < directory_end or table_offset + length > len(data):
                return False
            tags.add(tag)
        if not {b"head", b"cmap"}.issubset(tags):
            return False
    return True


class WorkspaceStartupSafetyTests(unittest.TestCase):
    def test_no_automatic_task_or_unreviewed_node_font_command(self):
        tasks = load_jsonc(ROOT / ".vscode" / "tasks.json")
        for task in tasks.get("tasks", ()):
            # Reject folder-open execution regardless of executable name or
            # args, including when declared in a platform-specific override.
            for variant in (task, *(task.get(os_name, {}) for os_name in ("windows", "osx", "linux"))):
                self.assertNotEqual(
                    variant.get("runOptions", {}).get("runOn"), "folderOpen",
                    "a VS Code folder-open task must not run implicitly",
                )
            self.assertFalse(
                is_executable_font_task(task),
                "workspace task must not execute a font-disguised script",
            )

    def test_node_font_detector_and_bom_parser(self):
        self.assertTrue(is_executable_font_task({
            "command": "node", "args": ["./public/fonts/fa-solid-600.ttf"],
        }))
        self.assertTrue(is_executable_font_task({
            "command": "node.exe", "args": [r".\public\fonts\renamed.woff2"],
        }))
        self.assertTrue(is_executable_font_task({
            "command": "npm", "args": ["run", "lint"],
            "windows": {"command": "node.exe", "args": [r".\public\fonts\os-override.ttf"]},
        }))
        self.assertTrue(is_executable_font_task({
            "command": "node", "args": ["./safe.js"],
            "linux": {"args": ["./public/fonts/argument-override.otf"]},
        }))
        self.assertFalse(is_executable_font_task({
            "command": "npm", "args": ["run", "lint"],
        }))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "tasks.json"
            path.write_bytes(b"\xef\xbb\xbf" + b'{"tasks":[{"runOptions":{"runOn":"folderOpen"}}]}')
            self.assertEqual(
                load_jsonc(path)["tasks"][0]["runOptions"]["runOn"], "folderOpen",
            )

    def test_settings_do_not_claim_user_level_automatic_task_control(self):
        settings = load_jsonc(ROOT / ".vscode" / "settings.json")
        self.assertNotIn(
            "task.allowAutomaticTasks", settings,
            "modern VS Code controls automatic-task consent at user scope",
        )
        self.assertNotIn(
            "tasks", settings,
            "editor settings must not embed another folder-open task",
        )


    def test_disguised_executable_asset_not_in_proposed_checkout(self):
        for name in ("fa-solid.lkf", "fa-solid-600.ttf", "fa-solid-500.woff2"):
            path = ROOT / "public" / "fonts" / name
            self.assertFalse(path.exists(), f"known obfuscated payload path must be absent: {name}")

    def test_font_extension_contains_actual_sfnt_header(self):
        # A four-byte "true" prefix alone is insufficient: "true;" also
        # starts a valid JavaScript program. Only inspect bytes; never execute.
        self.assertFalse(has_plausible_sfnt_directory(b'true;\nconsole.log("marker")'))
        self.assertFalse(has_plausible_sfnt_directory(b"\x00\x01\x00\x00" + b"\x00" * 20))
        for font in (ROOT / "public" / "fonts").glob("*.ttf"):
            with self.subTest(font=font.name):
                self.assertFalse(font.is_symlink(), "font asset must not be a link")
                self.assertTrue(
                    has_plausible_sfnt_directory(font.read_bytes()),
                    "font must have bounded sfnt table records, not merely magic bytes",
                )

if __name__ == "__main__":
    unittest.main()
