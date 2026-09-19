# VeraPort Windows production ACL profile

Status: implementation candidate.

The current Windows Service shell runs under the SCM default LocalSystem account. Until that service-account model is deliberately changed, sensitive VeraPort service material must be accessible only to:

- `NT AUTHORITY\SYSTEM`;
- `BUILTIN\Administrators`.

## Protected material

Before service start, the installer/qualification path applies protected DACLs to:

- fixed service config;
- TLS certificate;
- TLS private key;
- VeraPort workstation private key;
- controller trust file.

DACL inheritance is disabled on those files. Any additional principal or NULL DACL causes validation failure.

The state database directory is separately protected and gives SYSTEM/Administrators full control because the service must create/update SQLite state there.

## Startup enforcement

The Windows Service lifecycle validates protected material ACLs before calling `start_host()`. An ACL failure occurs before the VeraPort listener is created.

## Service-account boundary

This profile qualifies the current LocalSystem service shell only. Running a future service under LocalService, NetworkService, a virtual service account, or a user account requires a new ACL/service-account profile and qualification.

## Installation boundary

Source support for applying or validating ACLs is not itself an installation. SCM registration, credential creation, ProgramData writes, and ACL mutation remain explicit effects.
