from pathlib import Path
import pytest

from veraport_agent.service_config import WindowsServiceConfig, ServiceConfigError
from veraport_agent.edge_adapter import LiveEdgeProxy, DurableRelayFallback, EdgeUnauthenticated


def config(tmp_path, **changes):
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    base = {
        "bind_host": "127.0.0.1", "bind_port": 17444,
        "allowed_roots": [str(root)],
        "state_db": str(tmp_path / "state" / "agent.sqlite3"),
        "tls_cert": str(tmp_path / "tls.crt"),
        "tls_key": str(tmp_path / "tls.key"),
        "workstation_key": str(tmp_path / "workstation.pem"),
        "controller_trust": str(tmp_path / "controllers.json"),
    }
    base.update(changes)
    return base


def test_service_config_defaults_fail_closed(tmp_path):
    cfg = WindowsServiceConfig.from_dict(config(tmp_path))
    assert cfg.bind_host == "127.0.0.1"
    assert cfg.allow_process_exec is False
    assert cfg.allow_non_loopback_listener is False


def test_non_loopback_requires_explicit_override(tmp_path):
    with pytest.raises(ServiceConfigError):
        WindowsServiceConfig.from_dict(config(tmp_path, bind_host="0.0.0.0"))


def test_runtime_validation_refuses_missing_identity_material(tmp_path):
    cfg = WindowsServiceConfig.from_dict(config(tmp_path))
    with pytest.raises(ServiceConfigError):
        cfg.validate_runtime_files()


def test_runtime_validation_accepts_explicit_existing_material(tmp_path):
    value = config(tmp_path)
    for key in ("tls_cert", "tls_key", "workstation_key", "controller_trust"):
        Path(value[key]).write_text("placeholder", encoding="utf-8")
    cfg = WindowsServiceConfig.from_dict(value)
    cfg.validate_runtime_files()
    assert cfg.state_db.parent.is_dir()


@pytest.mark.asyncio
async def test_live_edge_proxy_is_synchronous_forward_not_queue():
    calls = []
    async def forward(request):
        calls.append(request)
        return {"request_id": request["request_id"], "ok": True, "result": {"live": True}}
    result = await LiveEdgeProxy(forward, authenticated=True).request({"request_id": "r1"})
    assert result["result"]["live"] is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_durable_fallback_only_claims_queue_custody():
    async def submit(request):
        return "receipt-7"
    result = await DurableRelayFallback(submit, authenticated=True).enqueue(
        {"request_id": "r2", "operation": "fs.read_text"}
    )
    assert result.state == "QUEUED_NOT_EXECUTED"
    assert result.relay_receipt_id == "receipt-7"


@pytest.mark.asyncio
async def test_unauthenticated_edge_paths_reject():
    async def no(request):
        return "x"
    with pytest.raises(EdgeUnauthenticated):
        await LiveEdgeProxy(no, authenticated=False).request({"request_id": "a"})
    with pytest.raises(EdgeUnauthenticated):
        await DurableRelayFallback(no, authenticated=False).enqueue({"request_id": "b"})

def test_read_bound_defaults_and_is_policy_bounded(tmp_path):
    cfg = WindowsServiceConfig.from_dict(config(tmp_path))
    assert cfg.max_read_bytes == 1_048_576
    with pytest.raises(ServiceConfigError, match="max_read_bytes"):
        WindowsServiceConfig.from_dict(config(tmp_path, max_read_bytes=0))
    with pytest.raises(ServiceConfigError, match="max_read_bytes"):
        WindowsServiceConfig.from_dict(config(tmp_path, max_read_bytes=16_777_217))

