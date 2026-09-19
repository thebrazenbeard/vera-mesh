from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows ACL test")


def _modules():
    import ntsecuritycon
    import win32security
    return ntsecuritycon, win32security


def test_harden_private_file_round_trip(tmp_path: Path):
    from veraport_agent.windows_acl import (
        harden_private_file,
        validate_private_file,
    )
    target = tmp_path / "secret.pem"
    target.write_text("secret", encoding="utf-8")
    harden_private_file(target)
    validate_private_file(target)


def test_broad_users_ace_is_rejected(tmp_path: Path):
    from veraport_agent.windows_acl import (
        WindowsAclError,
        harden_private_file,
        validate_private_file,
    )
    ntsecuritycon, win32security = _modules()
    target = tmp_path / "secret.pem"
    target.write_text("secret", encoding="utf-8")
    harden_private_file(target)

    sd = win32security.GetNamedSecurityInfo(
        str(target),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION,
    )
    dacl = sd.GetSecurityDescriptorDacl()
    users, _, _ = win32security.LookupAccountName(None, r"BUILTIN\Users")
    dacl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION_DS,
        0,
        ntsecuritycon.FILE_GENERIC_READ,
        users,
    )
    win32security.SetNamedSecurityInfo(
        str(target),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        dacl,
        None,
    )

    with pytest.raises(WindowsAclError, match="unexpected principal"):
        validate_private_file(target)
