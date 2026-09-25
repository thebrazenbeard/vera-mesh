# VeraPort V1 Lappy host / Windows Service lifecycle

Status: implementation candidate.

## Composition order

The Lappy host is deliberately assembled before any listener exists:

1. validate static service policy;
2. verify required runtime files/roots;
3. load fixed workstation identity and explicit controller trust;
4. open durable state store;
5. construct the one shared lane registry/executor/agent;
6. construct application authenticator and shared session handler factory;
7. construct TLS context;
8. only then create the listener.

A failure before step 8 means no network listener. Listener creation failure closes the durable state store.

## Windows Service shell

The Windows Service wrapper is intentionally thin. It contains no independent authorization or routing policy.

Service name: `VeraPortAgent`

Fixed service config path:
`C:\ProgramData\VeraMesh\veraport.json`

The service wrapper does not search the current directory, user profile, registry, environment, or network for alternate configuration.

Windows Service mode has an explicit optional dependency on `pywin32`. If unavailable, service mode fails explicitly; it does not fall back to a different background mechanism.

A console runner exists for development/manual qualification, but invoking or installing either mode remains a separate protected effect.

## Stop semantics

Service stop requests signal the async host loop, close the network server, wait for listener shutdown, and then close the durable state store.

No source state here claims Windows installation, startup persistence, firewall changes, credentials, ACLs, or a running Lappy endpoint.
