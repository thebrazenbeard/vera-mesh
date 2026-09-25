# VeraPort V1 whole-chain loopback test

Status: EXECUTED PASS WITH ENVIRONMENT QUALIFICATION CAVEAT.

Exact tested source:
- head: `fe6245fc9bf6392355bb9f13a88e010476ebdb89`
- tree: `91d123408201b80a8d6edd7c4fbc20824fc3b330`

The whole-chain integration test exercised the actual source modules together:

```text
TLS 1.3 listener
 -> VeraPort P-256 mutual application authentication
 -> SessionBinding
 -> persistent MultiplexClient
 -> HotSessionPool DIRECT_STREAM
 -> thin VeraPortGateway
 -> SessionBoundHandler
 -> shared VeraPortAgent
 -> LaneRegistry / durable fence
 -> LocalExecutor
 -> real allowed-root filesystem write/read
 -> result over the same hot connection
```

The test generated only ephemeral test keys/certificate and bound loopback.

On Lappy, plain pytest skipped the async case because `pytest-asyncio` is not installed. No package installation was performed. The exact checked-out test function was then loaded from source and executed directly with `asyncio.run()`; it passed.

Environment caveat: Lappy had Python 3.11.9 and `cryptography 48.0.1`, while this candidate declares `cryptography>=42,<47`. The passing result therefore establishes actual-source whole-chain interoperability on the observed environment, but does not qualify the declared dependency range.

The checkout was read back at the same exact head/tree and returned clean after removing only generated `__pycache__` directories.

This result does not establish Windows SCM installation/start/stop, non-loopback network exposure, firewall behavior under installation, persistent production credential handling, Synology edge operation, ChatGPT/MCP connectivity, or deployment.
