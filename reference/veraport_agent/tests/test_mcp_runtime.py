from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import veraport_agent.mcp_server as mcp_server


class RuntimeStub:
    def __init__(self):
        self.config = SimpleNamespace(
            requested_capabilities=frozenset({"fs.read"}),
            gateway_operations=frozenset(),
        )


def test_real_fastmcp_build_uses_constructor_http_settings():
    runtime = RuntimeStub()
    server = mcp_server.build_mcp_server(
        runtime,
        host="127.0.0.1",
        port=18446,
        stateless_http=False,
    )

    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 18446
    assert server.settings.stateless_http is False
    app = server.streamable_http_app()
    assert any(getattr(route, "path", None) == "/mcp" for route in app.routes)


def test_real_fastmcp_build_rejects_remote_bind():
    with pytest.raises(ValueError, match="loopback"):
        mcp_server.build_mcp_server(
            RuntimeStub(),
            host="0.0.0.0",
            port=18446,
        )


def test_main_passes_http_settings_to_builder_not_run(monkeypatch):
    observed = {}

    class FakeMCP:
        def run(self, *args, **kwargs):
            observed["run_args"] = args
            observed["run_kwargs"] = kwargs

    def fake_build(runtime, **kwargs):
        observed["build_kwargs"] = kwargs
        return FakeMCP()

    monkeypatch.setenv("VERAPORT_CONTROLLER_CONFIG", "controller.json")
    monkeypatch.setenv("VERAPORT_MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("VERAPORT_MCP_PORT", "19446")
    monkeypatch.setattr(
        mcp_server.ControllerConfig,
        "load",
        lambda path: object(),
    )
    monkeypatch.setattr(
        mcp_server,
        "ControllerRuntime",
        lambda config: object(),
    )
    monkeypatch.setattr(mcp_server, "build_mcp_server", fake_build)

    mcp_server.main()

    assert observed["build_kwargs"] == {
        "host": "127.0.0.1",
        "port": 19446,
        "stateless_http": False,
    }
    assert observed["run_args"] == ()
    assert observed["run_kwargs"] == {"transport": "streamable-http"}
