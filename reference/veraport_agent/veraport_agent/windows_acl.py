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
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
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


def harden_state_directory(path: str | Path) -> None:
    ntsecuritycon, _, _ = _modules()
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    _set_protected_dacl(
        target,
        service_mask=ntsecuritycon.FILE_ALL_ACCESS,
        directory=True,
    )


def validate_private_file(path: str | Path) -> None:
    _, _, win32security = _modules()
    target = Path(path)
    if not target.is_file():
        raise WindowsAclError(f"private material file missing: {target}")

    sd = win32security.GetNamedSecurityInfo(
        str(target),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.OWNER_SECURITY_INFORMATION,
    )
    control, _ = sd.GetSecurityDescriptorControl()
    if not control & win32security.SE_DACL_PROTECTED:
        raise WindowsAclError(f"DACL inheritance is not protected: {target}")

    dacl = sd.GetSecurityDescriptorDacl()
    if dacl is None:
        raise WindowsAclError(f"NULL DACL is forbidden: {target}")

    allowed = expected_service_sids()
    seen: set[str] = set()
    for index in range(dacl.GetAceCount()):
        ace = dacl.GetAce(index)
        ace_type = ace[0][0]
        if ace_type != win32security.ACCESS_ALLOWED_ACE_TYPE:
            raise WindowsAclError(f"unexpected non-allow ACE on private material: {target}")
        sid_string = _sid_string(ace[2])
        if sid_string not in allowed:
            raise WindowsAclError(
                f"unexpected principal {sid_string} on private material: {target}"
            )
        seen.add(sid_string)

    if seen != set(allowed):
        raise WindowsAclError(
            f"private material ACL must contain exactly SYSTEM and Administrators: {target}"
        )


def harden_service_materials(config_path: str | Path, config) -> None:
    """Apply production service ACLs before first listener start.

    Current VeraPort Windows service runs as LocalSystem. Sensitive material is
    therefore readable only by SYSTEM and Built-in Administrators. State gets
    SYSTEM/Admin full control.
    """
    config_path = Path(config_path)
    for path in (
        config_path,
        config.tls_cert,
        config.tls_key,
        config.workstation_key,
        config.controller_trust,
    ):
        harden_private_file(path)
    harden_state_directory(config.state_db.parent)


def validate_service_materials(config_path: str | Path, config) -> None:
    for path in (
        Path(config_path),
        config.tls_cert,
        config.tls_key,
        config.workstation_key,
        config.controller_trust,
    ):
        validate_private_file(path)
