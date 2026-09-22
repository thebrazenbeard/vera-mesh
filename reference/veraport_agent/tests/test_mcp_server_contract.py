from __future__ import annotations

from types import SimpleNamespace

import pytest


mcp_fast = pytest.importorskip("mcp.server.fastmcp")

from veraport_agent.mcp_server import build_mcp_server


class RuntimeStub:
    def __init__(self, operations=()):
        self.config = SimpleNamespace(
            gateway_operations=frozenset(operations),
        )


@pytest.mark.asyncio
async def test_fastmcp_constructor_receives_http_bind_settings():
    server = build_mcp_server(
        RuntimeStub(),
        host="127.0.0.1",
        port=18446,
        stateless_http=False,
    )
    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 18446
    assert server.settings.stateless_http is False

    tools = {tool.name: tool for tool in await server.list_tools()}
    names = set(tools)
    assert tools["machine_info"].annotations.readOnlyHint is True
    assert tools["lane_list"].annotations.readOnlyHint is True
    assert tools["fs_read_text"].annotations.readOnlyHint is True
    assert {
        "machine_info",
        "lane_list",
        "lane_open",
        "lane_renew",
        "lane_close",
        "fs_read_text",
    }.issubset(names)


@pytest.mark.asyncio
async def test_mcp_surface_registers_replacement_tools_from_policy():
    operations = {
        "fs.read_bytes",
        "fs.stat",
        "fs.list_dir",
        "fs.search",
        "fs.write_text",
        "fs.append_text",
        "fs.mkdir",
        "fs.move",
        "fs.replace_text",
        "process.exec",
        "process.start",
        "process.list",
        "process.status",
        "process.output",
        "process.input",
        "process.terminate",
    }
    server = build_mcp_server(RuntimeStub(operations))
    tools = {tool.name: tool for tool in await server.list_tools()}
    names = set(tools)
    for name in {
        "fs_read_bytes",
        "fs_stat",
        "fs_list_dir",
        "fs_search",
        "process_list",
        "process_status",
        "process_output",
    }:
        assert tools[name].annotations.readOnlyHint is True
    assert {
        "fs_read_bytes",
        "fs_stat",
        "fs_list_dir",
        "fs_search",
        "fs_write_text",
        "fs_append_text",
        "fs_mkdir",
        "fs_move",
        "fs_replace_text",
        "process_exec",
        "process_start",
        "process_list",
        "process_status",
        "process_output",
        "process_input",
        "process_terminate",
    }.issubset(names)
