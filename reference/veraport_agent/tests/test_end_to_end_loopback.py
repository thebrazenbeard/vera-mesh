from __future__ import annotations

import asyncio
import datetime
import sys
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from veraport_agent.controller import HotSessionPool, SessionEndpoint
from veraport_agent.core import LaneRegistry
from veraport_agent.executor import LocalExecutor
from veraport_agent.gateway import VeraPortGateway
from veraport_agent.hot_session import WorkstationAuthenticator, principal_id
from veraport_agent.protocol import VeraPortAgent
from veraport_agent.runtime import WorkstationHandlerFactory
from veraport_agent.state import AgentStateStore
from veraport_agent.synchrony import PathMode, PathObservation
from veraport_agent.verarelay_live_edge import LiveEdgeConfig, VeraRelayLiveEdge
from veraport_agent.tls_transport import (
    make_client_context,
    make_server_context,
    open_tls_session,
    serve_tls_connection,
)


def certificate_files(tmp_path: Path):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "tls-cert.pem"
    key_path = tmp_path / "tls-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@pytest.mark.asyncio
async def test_full_direct_hot_path_file_round_trip(tmp_path: Path):
    root = tmp_path / "allowed"
    root.mkdir()
    cert, tls_key = certificate_files(tmp_path)

    workstation_key = ec.generate_private_key(ec.SECP256R1())
    controller_key = ec.generate_private_key(ec.SECP256R1())
    controller_principal = principal_id(controller_key.public_key(), "controller")

    state = AgentStateStore(tmp_path / "agent.sqlite3")
    registry = LaneRegistry(
        {"fs.read", "fs.write"},
        max_lanes=8,
        fence_allocator=state.next_fence,
    )
    executor = LocalExecutor(registry, allowed_roots=(root,))
    agent = VeraPortAgent(registry, executor, state)
    handler_factory = WorkstationHandlerFactory(agent, now_ms=lambda: 1_002)

    authenticator = WorkstationAuthenticator(
        workstation_private_key=workstation_key,
        allowed_controllers={controller_principal: controller_key.public_key()},
        capability_policy={
            controller_principal: frozenset({"fs.read", "fs.write"})
        },
    )

    server = await asyncio.start_server(
        lambda reader, writer: serve_tls_connection(
            reader,
            writer,
            authenticator=authenticator,
            handler_factory=handler_factory,
            now_ms=lambda: 1_000,
            session_ttl_ms=10_000,
        ),
        "127.0.0.1",
        0,
        ssl=make_server_context(certfile=cert, keyfile=tls_key),
    )
    port = server.sockets[0].getsockname()[1]

    client, binding = await open_tls_session(
        host="127.0.0.1",
        port=port,
        ssl_context=make_client_context(cafile=cert),
        server_hostname="localhost",
        controller_private_key=controller_key,
        workstation_public_key=workstation_key.public_key(),
        requested_capabilities={"fs.read", "fs.write"},
        now_ms=lambda: 1_001,
    )

    ids = iter(("open-1", "write-1", "read-1", "close-1"))
    pool = HotSessionPool()
    pool.register(
        SessionEndpoint(
            endpoint_id="direct",
            binding=binding,
            path=PathObservation(
                path_id="direct",
                mode=PathMode.DIRECT_STREAM,
                authenticated=True,
                healthy=True,
                observed_at_ms=1_001,
                rtt_ms=1.0,
            ),
            channel=client,
            durable_idempotency=True,
        )
    )
    gateway = VeraPortGateway(
        pool,
        workstation_principal=binding.workstation_principal,
        controller_principal=binding.controller_principal,
        allowed_operations={
            "lane.open",
            "lane.close",
            "fs.write_text",
            "fs.read_text",
        },
        now_ms=lambda: 1_002,
        request_id_factory=lambda: next(ids),
    )

    try:
        opened = await gateway.open_lane(
            lane_id="files",
            task_id="round-trip",
            capabilities=["fs.read", "fs.write"],
            claims=[{"key": f"fs:{root.as_posix()}", "mode": "write"}],
        )
        assert opened["ok"] is True
        fence = opened["result"]["fencing_token"]

        target = root / "hello.txt"
        written = await gateway.write_text(
            lane_id="files",
            fencing_token=fence,
            path=str(target),
            content="VeraPort hot path",
        )
        assert written["ok"] is True

        readback = await gateway.read_text(
            lane_id="files",
            fencing_token=fence,
            path=str(target),
        )
        assert readback["ok"] is True
        assert readback["result"]["content"] == "VeraPort hot path"
        assert target.read_text(encoding="utf-8") == "VeraPort hot path"

        closed = await gateway.close_lane(
            lane_id="files",
            fencing_token=fence,
        )
        assert closed["ok"] is True
    finally:
        await client.close()
        server.close()
        await server.wait_closed()
        state.close()



@pytest.mark.asyncio
async def test_full_verarelay_edge_hot_path_rdc_surface_round_trip(tmp_path: Path):
    root = tmp_path / "allowed-edge"
    root.mkdir()
    cert, tls_key = certificate_files(tmp_path)

    workstation_key = ec.generate_private_key(ec.SECP256R1())
    controller_key = ec.generate_private_key(ec.SECP256R1())
    controller_principal = principal_id(controller_key.public_key(), "controller")
    capabilities = {
        "fs.read",
        "fs.write",
        "process.exec",
        "process.inspect",
        "process.control",
    }

    state = AgentStateStore(tmp_path / "edge-agent.sqlite3")
    registry = LaneRegistry(
        capabilities,
        max_lanes=8,
        fence_allocator=state.next_fence,
    )
    executor = LocalExecutor(
        registry,
        allowed_roots=(root,),
        allow_process_exec=True,
    )
    agent = VeraPortAgent(registry, executor, state)
    handler_factory = WorkstationHandlerFactory(agent, now_ms=lambda: 2_002)

    authenticator = WorkstationAuthenticator(
        workstation_private_key=workstation_key,
        allowed_controllers={controller_principal: controller_key.public_key()},
        capability_policy={
            controller_principal: frozenset(capabilities)
        },
    )

    workstation_server = await asyncio.start_server(
        lambda reader, writer: serve_tls_connection(
            reader,
            writer,
            authenticator=authenticator,
            handler_factory=handler_factory,
            now_ms=lambda: 2_000,
            session_ttl_ms=10_000,
        ),
        "127.0.0.1",
        0,
        ssl=make_server_context(certfile=cert, keyfile=tls_key),
    )
    workstation_port = workstation_server.sockets[0].getsockname()[1]

    edge = VeraRelayLiveEdge(
        LiveEdgeConfig(
            listen_host="127.0.0.1",
            listen_port=free_port_for_edge_test(),
            upstream_host="127.0.0.1",
            upstream_port=workstation_port,
        )
    )
    edge_server = await edge.start()
    edge_port = edge_server.sockets[0].getsockname()[1]

    client, binding = await open_tls_session(
        host="127.0.0.1",
        port=edge_port,
        ssl_context=make_client_context(cafile=cert),
        server_hostname="localhost",
        controller_private_key=controller_key,
        workstation_public_key=workstation_key.public_key(),
        requested_capabilities=capabilities,
        now_ms=lambda: 2_001,
    )

    ids = iter(
        (
            "edge-open",
            "edge-write",
            "edge-search",
            "edge-start",
            "edge-status-1",
            "edge-status-2",
            "edge-status-3",
            "edge-status-4",
            "edge-status-5",
            "edge-output",
            "edge-close",
        )
    )
    pool = HotSessionPool()
    pool.register(
        SessionEndpoint(
            endpoint_id="verarelay-edge",
            binding=binding,
            path=PathObservation(
                path_id="verarelay-edge",
                mode=PathMode.EDGE_STREAM,
                authenticated=True,
                healthy=True,
                observed_at_ms=2_001,
                rtt_ms=2.0,
            ),
            channel=client,
            durable_idempotency=True,
        )
    )
    gateway = VeraPortGateway(
        pool,
        workstation_principal=binding.workstation_principal,
        controller_principal=binding.controller_principal,
        allowed_operations={
            "lane.open",
            "lane.close",
            "fs.write_text",
            "fs.search",
            "process.start",
            "process.status",
            "process.output",
        },
        now_ms=lambda: 2_002,
        request_id_factory=lambda: next(ids),
    )

    try:
        opened = await gateway.open_lane(
            lane_id="edge-rdc",
            task_id="edge-round-trip",
            capabilities=sorted(capabilities),
            claims=[
                {"key": f"fs:{root.as_posix()}", "mode": "write"},
                {"key": f"cwd:{root.as_posix()}", "mode": "write"},
            ],
        )
        assert opened["ok"] is True
        fence = opened["result"]["fencing_token"]

        target = root / "needle.txt"
        written = await gateway.write_text(
            lane_id="edge-rdc",
            fencing_token=fence,
            path=str(target),
            content="edge-ok",
        )
        assert written["ok"] is True

        found = await gateway.call_operation(
            "fs.search",
            lane_id="edge-rdc",
            fencing_token=fence,
            root=str(root),
            query="needle",
        )
        assert found["ok"] is True
        assert [
            item["relative_path"]
            for item in found["result"]["matches"]
        ] == ["needle.txt"]

        started = await gateway.call_operation(
            "process.start",
            lane_id="edge-rdc",
            fencing_token=fence,
            argv=[sys.executable, "-c", "print('verarelay-edge-ok')"],
            cwd=str(root),
            max_runtime_s=5,
        )
        assert started["ok"] is True
        handle = started["result"]["process_handle"]

        status = None
        for _ in range(5):
            status = await gateway.call_operation(
                "process.status",
                lane_id="edge-rdc",
                fencing_token=fence,
                process_handle=handle,
            )
            assert status["ok"] is True
            if not status["result"]["running"]:
                break
            await asyncio.sleep(0.05)
        assert status is not None
        assert status["result"]["running"] is False

        output = await gateway.call_operation(
            "process.output",
            lane_id="edge-rdc",
            fencing_token=fence,
            process_handle=handle,
        )
        assert output["ok"] is True
        assert output["result"]["stdout"] == "verarelay-edge-ok\n"

        closed = await gateway.close_lane(
            lane_id="edge-rdc",
            fencing_token=fence,
        )
        assert closed["ok"] is True
    finally:
        await client.close()
        await edge.close()
        workstation_server.close()
        await workstation_server.wait_closed()
        state.close()


def free_port_for_edge_test() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
