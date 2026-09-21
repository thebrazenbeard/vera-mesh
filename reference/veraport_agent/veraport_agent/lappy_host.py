from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from .service_config import WindowsServiceConfig


@dataclass(frozen=True)
class HostDependencies:
    load_identity: Callable[..., Any]
    state_store_cls: Callable[..., Any]
    lane_registry_cls: Callable[..., Any]
    executor_cls: Callable[..., Any]
    agent_cls: Callable[..., Any]
    authenticator_cls: Callable[..., Any]
    handler_factory_cls: Callable[..., Any]
    make_server_context: Callable[..., Any]
    serve_tls_connection: Callable[..., Any]
    start_server: Callable[..., Any] = asyncio.start_server


@dataclass
class PreparedHost:
    config: WindowsServiceConfig
    state_store: Any
    agent: Any
    authenticator: Any
    handler_factory: Any
    ssl_context: Any
    deps: HostDependencies


def default_dependencies() -> HostDependencies:
    from .core import LaneRegistry
    from .executor import LocalExecutor
    from .hot_session import WorkstationAuthenticator
    from .identity_store import load_identity
    from .protocol import VeraPortAgent
    from .runtime import WorkstationHandlerFactory
    from .state import AgentStateStore
    from .tls_transport import make_server_context, serve_tls_connection

    return HostDependencies(
        load_identity=load_identity,
        state_store_cls=AgentStateStore,
        lane_registry_cls=LaneRegistry,
        executor_cls=LocalExecutor,
        agent_cls=VeraPortAgent,
        authenticator_cls=WorkstationAuthenticator,
        handler_factory_cls=WorkstationHandlerFactory,
        make_server_context=make_server_context,
        serve_tls_connection=serve_tls_connection,
    )


def prepare_host(
    config: WindowsServiceConfig,
    *,
    deps: HostDependencies | None = None,
) -> PreparedHost:
    deps = deps or default_dependencies()

    # Fail closed before creating network state.
    config.validate_static()
    config.validate_runtime_files()
    identity = deps.load_identity(
        workstation_key_path=config.workstation_key,
        controller_trust_path=config.controller_trust,
    )

    state_store = deps.state_store_cls(config.state_db)
    try:
        capabilities = {"fs.read", "fs.write"}
        if config.allow_process_exec:
            capabilities.add("process.exec")
        registry = deps.lane_registry_cls(
            capabilities,
            max_lanes=config.max_lanes,
            fence_allocator=state_store.next_fence,
        )
        executor = deps.executor_cls(
            registry,
            allowed_roots=config.allowed_roots,
            max_read_bytes=config.max_read_bytes,
            allow_process_exec=config.allow_process_exec,
        )
        agent = deps.agent_cls(registry, executor, state_store)
        authenticator = deps.authenticator_cls(
            workstation_private_key=identity.workstation_private_key,
            allowed_controllers=identity.allowed_controllers,
            capability_policy=identity.capability_policy,
        )
        handler_factory = deps.handler_factory_cls(agent)
        ssl_context = deps.make_server_context(
            certfile=config.tls_cert,
            keyfile=config.tls_key,
        )
        return PreparedHost(
            config=config,
            state_store=state_store,
            agent=agent,
            authenticator=authenticator,
            handler_factory=handler_factory,
            ssl_context=ssl_context,
            deps=deps,
        )
    except Exception:
        close = getattr(state_store, "close", None)
        if callable(close):
            close()
        raise


async def start_host(
    config: WindowsServiceConfig,
    *,
    deps: HostDependencies | None = None,
):
    prepared = prepare_host(config, deps=deps)

    async def client_connected(reader, writer):
        await prepared.deps.serve_tls_connection(
            reader,
            writer,
            authenticator=prepared.authenticator,
            handler_factory=prepared.handler_factory,
            max_inflight=prepared.config.max_inflight,
        )

    try:
        server = await prepared.deps.start_server(
            client_connected,
            prepared.config.bind_host,
            prepared.config.bind_port,
            ssl=prepared.ssl_context,
        )
    except Exception:
        prepared.state_store.close()
        raise

    return prepared, server
