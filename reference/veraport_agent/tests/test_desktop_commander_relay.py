from __future__ import annotations

import asyncio
import json
import socket

import pytest

from veraport_agent.desktop_commander_relay import DesktopCommanderRelay
from veraport_agent.verarelay_live_edge import LiveEdgeConfig


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.asyncio
async def test_desktop_commander_command_string_round_trips_byte_exact():
    request = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 77,
                "method": "tools/call",
                "params": {
                    "name": "start_process",
                    "arguments": {
                        "command": 'powershell -NoProfile -Command "Get-ChildItem C:\\\\"',
                        "timeout_ms": 5000,
                    },
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    response = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 77,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Process started with PID 4242",
                        }
                    ]
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    observed = bytearray()

    async def upstream(reader, writer):
        try:
            incoming = await reader.readexactly(len(request))
            observed.extend(incoming)
            writer.write(response)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    upstream_port = server.sockets[0].getsockname()[1]
    relay_port = free_port()
    relay = DesktopCommanderRelay(
        LiveEdgeConfig(
            listen_host="127.0.0.1",
            listen_port=relay_port,
            upstream_host="127.0.0.1",
            upstream_port=upstream_port,
        )
    )
    await relay.start()

    reader, writer = await asyncio.open_connection("127.0.0.1", relay_port)
    writer.write(request)
    await writer.drain()
    returned = await asyncio.wait_for(
        reader.readexactly(len(response)),
        timeout=2,
    )

    assert bytes(observed) == request
    assert returned == response
    decoded = json.loads(observed.decode("utf-8"))
    assert decoded["params"]["name"] == "start_process"
    assert decoded["params"]["arguments"]["command"].startswith("powershell ")

    writer.close()
    await writer.wait_closed()
    await relay.close()
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_desktop_commander_relay_does_not_require_json():
    payload = bytes(range(256)) + b"\x00\xffopaque-mcp-transport"

    async def echo(reader, writer):
        try:
            incoming = await reader.readexactly(len(payload))
            writer.write(incoming)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    upstream_port = server.sockets[0].getsockname()[1]
    relay_port = free_port()
    relay = DesktopCommanderRelay(
        LiveEdgeConfig(
            listen_host="127.0.0.1",
            listen_port=relay_port,
            upstream_host="127.0.0.1",
            upstream_port=upstream_port,
        )
    )
    await relay.start()

    reader, writer = await asyncio.open_connection("127.0.0.1", relay_port)
    writer.write(payload)
    await writer.drain()
    assert await asyncio.wait_for(
        reader.readexactly(len(payload)),
        timeout=2,
    ) == payload

    writer.close()
    await writer.wait_closed()
    await relay.close()
    server.close()
    await server.wait_closed()
