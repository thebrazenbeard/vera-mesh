from pathlib import Path

import pytest

from veraport_agent.controller_config import ControllerConfig, ControllerConfigError


def base():
    return {
        "schema": "VERAPORT_CONTROLLER_MCP_CONFIG_V1",
        "controller_key": "controller.pem",
        "tls_ca": "tls-ca.pem",
        "workstation_public_key": "workstation-public.pem",
        "requested_capabilities": ["fs.read"],
        "gateway_operations": [
            "lane.list",
            "lane.open",
            "lane.renew",
            "lane.close",
            "fs.read_text",
            "fs.read_bytes",
        ],
        "endpoints": [
            {
                "endpoint_id": "direct",
                "mode": "DIRECT_STREAM",
                "host": "127.0.0.1",
                "port": 17444,
                "server_hostname": "localhost",
                "durable_idempotency": True,
            },
            {
                "endpoint_id": "edge",
                "mode": "EDGE_STREAM",
                "host": "100.115.152.20",
                "port": 17445,
                "server_hostname": "localhost",
                "durable_idempotency": True,
            },
        ],
    }


def test_read_only_controller_config():
    cfg = ControllerConfig.from_dict(base(), base_dir=Path("/tmp"))
    assert cfg.requested_capabilities == frozenset({"fs.read"})
    assert [item.mode.name for item in cfg.endpoints] == [
        "DIRECT_STREAM",
        "EDGE_STREAM",
    ]


def test_write_tool_requires_write_session_capability():
    value = base()
    value["gateway_operations"].append("fs.write_text")
    with pytest.raises(ControllerConfigError, match="fs.write"):
        ControllerConfig.from_dict(value)


def test_process_operation_requires_process_session_capability():
    value = base()
    value["gateway_operations"].append("process.exec")
    with pytest.raises(ControllerConfigError, match="process.exec"):
        ControllerConfig.from_dict(value)


def test_durable_relay_rejected_until_implemented():
    value = base()
    value["endpoints"][0]["mode"] = "DURABLE_RELAY"
    with pytest.raises(ControllerConfigError, match="durable relay"):
        ControllerConfig.from_dict(value)


def test_discovery_tools_require_fs_read_capability():
    value = base()
    value["gateway_operations"].append("fs.search")
    value["requested_capabilities"] = ["fs.write"]
    with pytest.raises(ControllerConfigError, match="fs.read"):
        ControllerConfig.from_dict(value)


def test_managed_process_tools_require_narrow_capabilities():
    value = base()
    value["gateway_operations"] = ["process.start", "process.status", "process.terminate"]
    value["requested_capabilities"] = ["process.exec"]
    with pytest.raises(ControllerConfigError, match="process.inspect"):
        ControllerConfig.from_dict(value)

    value["requested_capabilities"] = ["process.exec", "process.inspect"]
    with pytest.raises(ControllerConfigError, match="process.control"):
        ControllerConfig.from_dict(value)

    value["requested_capabilities"] = [
        "process.exec", "process.inspect", "process.control"
    ]
    cfg = ControllerConfig.from_dict(value)
    assert cfg.gateway_operations == frozenset({
        "process.start", "process.status", "process.terminate"
    })
