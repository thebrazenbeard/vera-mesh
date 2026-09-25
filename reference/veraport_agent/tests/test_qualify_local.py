from __future__ import annotations

from pathlib import Path

from veraport_agent.executor import LocalExecutor
from veraport_agent.qualify_local import _fs_claim_key


def test_local_qualification_uses_executor_resource_canonicalization(tmp_path: Path):
    target = (tmp_path / "allowed" / "sentinel.txt").resolve()
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")

    assert _fs_claim_key(target) == LocalExecutor._fs_resource(target)
    assert _fs_claim_key(target).startswith("fs:")
