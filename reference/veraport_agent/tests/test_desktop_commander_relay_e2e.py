from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from veraport_agent.desktop_commander_relay import DesktopCommanderRelay
from veraport_agent.desktop_commander_stdio_host import (
    DesktopCommanderStdioHost,
    DesktopCommanderStdioHostConfig,
)
from veraport_agent.verarelay_live_edge import LiveEdgeConfig


async def read_response(
    reader: asyncio.StreamReader,
    request_id: int,
    *,
    timeout: float = 20.0,
) -> dict:
    async def _read() -> dict:
        while True:
            line = await reader.readline()
            if not line:
                raise EOFError(f"stream closed waiting for response {request_id}")
            message = json.loads(line)
            if message.get("id") == request_id:
                return message

    return await asyncio.wait_for(_read(), timeout=timeout)


async def send(writer: asyncio.StreamWriter, message: dict) -> None:
    writer.write(
        json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
    )
    await writer.drain()


@pytest.mark.asyncio
async def test_exact_desktop_commander_executes_command_through_verarelay():
    node = os.environ.get("DESKTOP_COMMANDER_NODE")
    entrypoint = os.environ.get("DESKTOP_COMMANDER_ENTRYPOINT")
    if not node or not entrypoint:
        pytest.skip("exact Desktop Commander CI paths are not configured")

    entry = Path(entrypoint).resolve()
    executable = Path(node).resolve()

    host = DesktopCommanderStdioHost(
        DesktopCommanderStdioHostConfig(
            listen_host="127.0.0.1",
            listen_port=0,
            executable=executable,
            entrypoint=entry,
            cwd=entry.parent.parent,
        )
    )
    host_server = await host.start()
    host_port = int(host_server.sockets[0].getsockname()[1])

    relay = DesktopCommanderRelay(
        LiveEdgeConfig(
            listen_host="127.0.0.1",
            listen_port=0,
            upstream_host="127.0.0.1",
            upstream_port=host_port,
        )
    )
    relay_server = await relay.start()
    relay_port = int(relay_server.sockets[0].getsockname()[1])

    reader, writer = await asyncio.open_connection("127.0.0.1", relay_port)
    try:
        await send(
            writer,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "verarelay-duplicate-ci",
                        "version": "1.0.0",
                    },
                },
            },
        )
        initialized = await read_response(reader, 1)
        assert "result" in initialized
        await send(
            writer,
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        )

        await send(
            writer,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        )
        listed = await read_response(reader, 2)
        tools = {
            item["name"]: item
            for item in listed["result"]["tools"]
        }
        assert "start_process" in tools
        schema = tools["start_process"]["inputSchema"]
        assert "command" in schema["properties"]
        assert "command" in schema["required"]

        marker = "VERARELAY_DESKTOP_COMMANDER_OK"
        command = (
            'node -e "process.stdout.write(\''
            + marker
            + '\')"'
        )
        await send(
            writer,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "start_process",
                    "arguments": {
                        "command": command,
                        "timeout_ms": 5000,
                    },
                },
            },
        )
        started = await read_response(reader, 3)
        assert "result" in started
        text = "\n".join(
            item.get("text", "")
            for item in started["result"].get("content", [])
            if item.get("type") == "text"
        )
        assert marker in text, text
    finally:
        writer.close()
        await writer.wait_closed()
        await relay.close()
        await host.close()
