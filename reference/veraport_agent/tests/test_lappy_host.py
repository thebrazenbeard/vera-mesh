from dataclasses import dataclass
import pytest

from veraport_agent.lappy_host import HostDependencies, prepare_host, start_host
from veraport_agent.service_config import WindowsServiceConfig


class State:
    def __init__(self, path):
        self.path = path
        self.closed = False
    def next_fence(self):
        return 1
    def close(self):
        self.closed = True


@dataclass
class Identity:
    workstation_private_key: object
    allowed_controllers: dict
    capability_policy: dict


def cfg(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    paths = {}
    for name in ("tls_cert", "tls_key", "workstation_key", "controller_trust"):
        p = tmp_path / name
        p.write_text("x")
        paths[name] = p
    return WindowsServiceConfig.from_dict({
        "bind_host": "127.0.0.1", "bind_port": 17444,
        "allowed_roots": [str(root)],
        "state_db": str(tmp_path / "state" / "db.sqlite"),
        **{k: str(v) for k, v in paths.items()},
    })


def deps(events, *, load_fails=False, start_fails=False):
    def load_identity(**kwargs):
        events.append("identity")
        if load_fails:
            raise RuntimeError("bad trust")
        return Identity(object(), {}, {})
    def state_cls(path):
        events.append("state")
        return State(path)
    def registry(caps, **kwargs):
        events.append("registry")
        return ("registry", frozenset(caps), kwargs)
    def executor(registry, **kwargs):
        events.append("executor")
        return ("executor", kwargs)
    def agent(registry, executor, state):
        events.append("agent")
        return object()
    def auth(**kwargs):
        events.append("auth")
        return object()
    def factory(agent):
        events.append("handler")
        return object()
    def tls(**kwargs):
        events.append("tls")
        return object()
    async def serve(*args, **kwargs):
        return None
    async def start(*args, **kwargs):
        events.append("listen")
        if start_fails:
            raise RuntimeError("listen failed")
        return object()
    return HostDependencies(
        load_identity, state_cls, registry, executor, agent, auth,
        factory, tls, serve, start
    )


def test_prepare_orders_identity_and_state_before_runtime(tmp_path):
    events = []
    prepared = prepare_host(cfg(tmp_path), deps=deps(events))
    assert events == ["identity","state","registry","executor","agent","auth","handler","tls"]
    assert prepared.config.allow_process_exec is False


def test_identity_failure_occurs_before_state_or_listener(tmp_path):
    events = []
    with pytest.raises(RuntimeError):
        prepare_host(cfg(tmp_path), deps=deps(events, load_fails=True))
    assert events == ["identity"]


@pytest.mark.asyncio
async def test_listener_created_only_after_full_prepare(tmp_path):
    events = []
    await start_host(cfg(tmp_path), deps=deps(events))
    assert events[-1] == "listen"
    assert events.index("tls") < events.index("listen")


@pytest.mark.asyncio
async def test_listener_failure_closes_durable_state(tmp_path):
    events = []
    d = deps(events, start_fails=True)
    captured = {}
    original = d.state_store_cls
    def state(path):
        obj = original(path)
        captured["state"] = obj
        return obj
    d = HostDependencies(
        d.load_identity, state, d.lane_registry_cls, d.executor_cls, d.agent_cls,
        d.authenticator_cls, d.handler_factory_cls, d.make_server_context,
        d.serve_tls_connection, d.start_server
    )
    with pytest.raises(RuntimeError):
        await start_host(cfg(tmp_path), deps=d)
    assert captured["state"].closed is True


def test_process_exec_not_added_without_local_config_grant(tmp_path):
    events = []
    prepared = prepare_host(cfg(tmp_path), deps=deps(events))
    assert prepared.config.allow_process_exec is False


def test_process_policy_expands_to_exec_inspect_and_control(tmp_path):
    events = []
    value = cfg(tmp_path)
    value = WindowsServiceConfig.from_dict({
        "bind_host": value.bind_host,
        "bind_port": value.bind_port,
        "allowed_roots": [str(root) for root in value.allowed_roots],
        "state_db": str(value.state_db),
        "tls_cert": str(value.tls_cert),
        "tls_key": str(value.tls_key),
        "workstation_key": str(value.workstation_key),
        "controller_trust": str(value.controller_trust),
        "allow_process_exec": True,
    })

    base = deps(events)
    captured = {}
    original_registry = base.lane_registry_cls

    def registry(caps, **kwargs):
        captured["caps"] = frozenset(caps)
        return original_registry(caps, **kwargs)

    wrapped = HostDependencies(
        base.load_identity,
        base.state_store_cls,
        registry,
        base.executor_cls,
        base.agent_cls,
        base.authenticator_cls,
        base.handler_factory_cls,
        base.make_server_context,
        base.serve_tls_connection,
        base.start_server,
    )
    prepare_host(value, deps=wrapped)
    assert captured["caps"] == frozenset({
        "fs.read",
        "fs.write",
        "process.exec",
        "process.inspect",
        "process.control",
    })
