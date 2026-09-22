from __future__ import annotations

import asyncio
import socket

import pytest

from veraport_agent.verarelay_live_edge import (
    LiveEdgeConfig,
    VeraRelayLiveEdge,
)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.asyncio
async def test_live_edge_forwards_opaque_binary_without_parsing():
    async def echo(reader, writer):
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    return
                writer.write(chunk)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
    upstream_port = upstream.sockets[0].getsockname()[1]
    edge_port = free_port()
    edge = VeraRelayLiveEdge(
        LiveEdgeConfig(
            listen_host="127.0.0.1",
            listen_port=edge_port,
            upstream_host="127.0.0.1",
            upstream_port=upstream_port,
        )
    )
    await edge.start()

    payload = (
        b"\x16\x03\x01\x00\xff"
        + bytes(range(256))
        + b"not-json\x00\xff"
    )
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", edge_port
    )
    writer.write(payload)
    await writer.drain()
    received = await asyncio.wait_for(
        reader.readexactly(len(payload)),
        timeout=2,
    )
    assert received == payload

    writer.close()
    await writer.wait_closed()
    await edge.close()
    upstream.close()
    await upstream.wait_closed()


@pytest.mark.asyncio
async def test_live_edge_fails_closed_when_upstream_is_absent():
    upstream_port = free_port()
    edge_port = free_port()
    edge = VeraRelayLiveEdge(
        LiveEdgeConfig(
            listen_host="127.0.0.1",
            listen_port=edge_port,
            upstream_host="127.0.0.1",
            upstream_port=upstream_port,
            connect_timeout_s=0.2,
        )
    )
    await edge.start()

    reader, writer = await asyncio.open_connection(
        "127.0.0.1", edge_port
    )
    writer.write(b"controller-hello")
    await writer.drain()
    assert await asyncio.wait_for(reader.read(), timeout=2) == b""

    writer.close()
    await writer.wait_closed()
    await edge.close()


def test_live_edge_config_bounds_resources():
    with pytest.raises(ValueError, match="max_connections"):
        LiveEdgeConfig(
            "127.0.0.1", 17445, "127.0.0.1", 17444,
            max_connections=0,
        )
    with pytest.raises(ValueError, match="io_chunk_bytes"):
        LiveEdgeConfig(
            "127.0.0.1", 17445, "127.0.0.1", 17444,
            io_chunk_bytes=1,
        )



@pytest.mark.asyncio
async def test_live_edge_rejects_connections_above_admission_bound():
    hold = asyncio.Event()

    async def hold_open(reader, writer):
        try:
            await hold.wait()
        finally:
            writer.close()
            await writer.wait_closed()

    upstream = await asyncio.start_server(hold_open, "127.0.0.1", 0)
    upstream_port = upstream.sockets[0].getsockname()[1]
    edge_port = free_port()
    edge = VeraRelayLiveEdge(
        LiveEdgeConfig(
            listen_host="127.0.0.1",
            listen_port=edge_port,
            upstream_host="127.0.0.1",
            upstream_port=upstream_port,
            max_connections=1,
        )
    )
    await edge.start()

    first_reader, first_writer = await asyncio.open_connection(
        "127.0.0.1", edge_port
    )
    first_writer.write(b"first")
    await first_writer.drain()
    await asyncio.sleep(0.05)

    second_reader, second_writer = await asyncio.open_connection(
        "127.0.0.1", edge_port
    )
    second_writer.write(b"second")
    try:
        await second_writer.drain()
    except ConnectionResetError:
        pass
    try:
        rejected = await asyncio.wait_for(second_reader.read(), timeout=2)
    except ConnectionResetError:
        rejected = b""
    assert rejected == b""

    second_writer.close()
    try:
        await second_writer.wait_closed()
    except ConnectionResetError:
        pass
    hold.set()
    first_writer.close()
    await first_writer.wait_closed()
    await edge.close()
    upstream.close()
    await upstream.wait_closed()
