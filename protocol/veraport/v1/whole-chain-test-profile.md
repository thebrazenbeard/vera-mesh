# VeraPort V1 whole-chain loopback test

Status: TEST AUTHORED / EXECUTION PENDING.

The whole-chain integration test is intended to exercise the actual source modules together rather than dependency-injected substitutes:

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

The test generates only ephemeral test keys/certificate and binds loopback on an OS-assigned ephemeral port.

It deliberately excludes process execution.

Passing this test will establish local whole-chain integration evidence. It still will not establish Windows service installation, real Lappy networking, Synology edge operation, ChatGPT/MCP connectivity, production certificate handling, or deployment.
