# VeraPort Windows production ACL profile

Status: implementation candidate.

The current Windows Service shell runs under the SCM default LocalSystem account. Until that service-account model is deliberately changed, sensitive VeraPort service material and its containing private directories must be accessible only to:

- `NT AUTHORITY\SYSTEM`;
- `BUILTIN\Administrators`.

## Protected material and directories

Before service start, the installer/qualification path protects:

- the service config directory;
- state database directory;
- any private directories containing VeraPort TLS/key/trust material;
- fixed service config;
- TLS certificate;
- TLS private key;
- VeraPort workstation private key;
- controller trust file.

DACL inheritance is disabled. The owner must be SYSTEM or Built-in Administrators. Any additional principal, NULL DACL, or unprotected inheritance causes validation failure.

Protecting directories is required because file-only ACLs do not prevent replacement through a writable parent directory.

## Startup enforcement

The Windows Service lifecycle validates protected material and directory ACLs before calling `start_host()`. An ACL failure occurs before the VeraPort listener is created.

## Service-account boundary

This profile qualifies the current LocalSystem service shell only. Running under LocalService, NetworkService, a virtual service account, or a user account requires a new ACL/service-account profile and qualification.

## Installation boundary

Source support for applying or validating ACLs is not itself an installation. SCM registration, ProgramData writes, ACL mutation, and service start remain explicit effects.
