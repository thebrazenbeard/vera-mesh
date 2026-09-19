from __future__ import annotations

import os
from pathlib import Path


class WindowsAclError(PermissionError):
    code = "WINDOWS_ACL_ERROR"


def _modules():
    if os.name != "nt":
        raise WindowsAclError("Windows ACL validation is only available on Windows")
    import ntsecuritycon
    import win32con
    import win32security
    return ntsecuritycon, win32con, win32security


def _sid(name: str):
    _, _, win32security = _modules()
    sid, _, _ = win32security.LookupAccountName(None, name)
    return sid


def _sid_string(sid) -> str:
    _, _, win32security = _modules()
    return win32security.ConvertSidToStringSid(sid)


def expected_service_sids() -> frozenset[str]:
    return frozenset({
        _sid_string(_sid("SYSTEM")),
        _sid_string(_sid(r"BUILTIN\Administrators")),
    })


def _set_protected_dacl(path: Path, *, service_mask: int, directory: bool) -> None:
    ntsecuritycon, win32con, win32security = _modules()
    system_sid = _sid("SYSTEM")
    admins_sid = _sid(r"BUILTIN\Administrators")

    inherit_flags = 0
    if directory:
        inherit_flags = win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE

    dacl = win32security.ACL()
    dacl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION_DS,
        inherit_flags,
        ntsecuritycon.FILE_ALL_ACCESS,
        admins_sid,
    )
    dacl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION_DS,
        inherit_flags,
        service_mask,
        system_sid,
    )
    win32security.SetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION
        | win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        admins_sid,
        None,
        dacl,
        None,
    )


def harden_private_file(path: str | Path) -> None:
    ntsecuritycon, _, _ = _modules()
    target = Path(path)
    if not target.is_file():
        raise WindowsAclError(f"private material file missing: {target}")
    _set_protected_dacl(
        target,
        service_mask=ntsecuritycon.FILE_GENERIC_READ,
        directory=False,
    )


def harden_private_directory(path: str | Path) -> None:
    ntsecuritycon, _, _ = _modules()
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    _set_protected_dacl(
        target,
        service_mask=ntsecuritycon.FILE_ALL_ACCESS,
        directory=True,
    )


def _validate_path_acl(path: Path, *, require_directory: bool) -> None:
    _, _, win32security = _modules()
    if require_directory:
        if not path.is_dir():
            raise WindowsAclError(f"protected directory missing: {path}")
    elif not path.is_file():
        raise WindowsAclError(f"private material file missing: {path}")

    sd = win32security.GetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.OWNER_SECURITY_INFORMATION,
    )
    control, _ = sd.GetSecurityDescriptorControl()
    if not control & win32security.SE_DACL_PROTECTED:
        raise WindowsAclError(f"DACL inheritance is not protected: {path}")

    allowed = expected_service_sids()
    owner = sd.GetSecurityDescriptorOwner()
    if owner is None or _sid_string(owner) not in allowed:
        raise WindowsAclError(f"owner must be SYSTEM or Administrators: {path}")

    dacl = sd.GetSecurityDescriptorDacl()
    if dacl is None:
        raise WindowsAclError(f"NULL DACL is forbidden: {path}")

    seen: set[str] = set()
    for index in range(dacl.GetAceCount()):
        ace = dacl.GetAce(index)
        ace_type = ace[0][0]
        if ace_type != win32security.ACCESS_ALLOWED_ACE_TYPE:
            raise WindowsAclError(f"unexpected non-allow ACE: {path}")
        sid_string = _sid_string(ace[2])
        if sid_string not in allowed:
            raise WindowsAclError(f"unexpected principal {sid_string}: {path}")
        seen.add(sid_string)

    if seen != set(allowed):
        raise WindowsAclError(
            f"ACL must contain exactly SYSTEM and Administrators: {path}"
        )


def validate_private_file(path: str | Path) -> None:
    _validate_path_acl(Path(path), require_directory=False)


def validate_private_directory(path: str | Path) -> None:
    _validate_path_acl(Path(path), require_directory=True)


def harden_service_materials(config_path: str | Path, config) -> None:
    """Apply the current LocalSystem production ACL profile."""
    config_path = Path(config_path)
    files = (
        config_path,
        config.tls_cert,
        config.tls_key,
        config.workstation_key,
        config.controller_trust,
    )
    private_dirs = {
        config_path.parent,
        config.state_db.parent,
        *(Path(path).parent for path in files),
    }
    for directory in sorted(private_dirs, key=lambda p: len(str(p))):
        harden_private_directory(directory)
    for path in files:
        harden_private_file(path)


def validate_service_materials(config_path: str | Path, config) -> None:
    config_path = Path(config_path)
    files = (
        config_path,
        config.tls_cert,
        config.tls_key,
        config.workstation_key,
        config.controller_trust,
    )
    private_dirs = {
        config_path.parent,
        config.state_db.parent,
        *(Path(path).parent for path in files),
    }
    for directory in private_dirs:
        validate_private_directory(directory)
    for path in files:
        validate_private_file(path)
