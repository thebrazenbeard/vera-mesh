from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from typing import Any


class LiveEdgeError(RuntimeError):
    code = "LIVE_EDGE_ERROR"


@dataclass(frozen=True)
class LiveEdgeConfig:
    listen_host: str
    listen_port: int
    upstream_host: str
    upstream_port: int
    max_connections: int = 32
    connect_timeout_s: float = 5.0
    io_chunk_bytes: int = 65_536

    def __post_init__(self) -> None:
        if not self.listen_host:
            raise ValueError("listen_host is required")
        if not self.upstream_host:
            raise ValueError("upstream_host is required")
        for name, value in {
            "listen_port": self.listen_port,
            "upstream_port": self.upstream_port,
        }.items():
            if type(value) is not int or not 1 <= value <= 65_535:
                raise ValueError(f"{name} must be in 1..65535")
        if type(self.max_connections) is not int or not 1 <= self.max_connections <= 1024:
            raise ValueError("max_connections must be in 1..1024")
        if not 0.05 <= float(self.connect_timeout_s) <= 60.0:
            raise ValueError("connect_timeout_s must be in 0.05..60")
        if type(self.io_chunk_bytes) is not int or not 1024 <= self.io_chunk_bytes <= 1_048_576:
            raise ValueError("io_chunk_bytes must be in 1024..1048576")


class VeraRelayLiveEdge:
    """Transparent live VeraPort carrier for VeraRelay/VeraMesh Edge.

    The edge never terminates VeraPort TLS and never parses VeraPort frames.
    Controller <-> workstation confidentiality, application authentication,
    session capabilities, lane claims, fencing, and mutation idempotency remain
    end-to-end properties of VeraPort.

    This class is deliberately *not* durable relay. If the upstream cannot be
    reached, the live connection fails. Store-and-forward custody remains a
    distinct protocol/evidence path.
    """

    def __init__(
        self,
        config: LiveEdgeConfig,
        *,
        open_connection=asyncio.open_connection,
        start_server=asyncio.start_server,
    ) -> None:
        self.config = config
        self._open_connection = open_connection
        self._start_server = start_server
        self._admission_lock = asyncio.Lock()
        self._active_connections = 0
        self._server: asyncio.AbstractServer | None = None
        self._active: set[asyncio.Task[Any]] = set()

    async def start(self) -> asyncio.AbstractServer:
        if self._server is not None:
            return self._server
        self._server = await self._start_server(
            self._accept,
            self.config.listen_host,
            self.config.listen_port,
            limit=self.config.io_chunk_bytes,
        )
        return self._server

    async def close(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        active = tuple(self._active)
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    async def _accept(
        self,
        downstream_reader: asyncio.StreamReader,
        downstream_writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._active.add(task)
        admitted = False
        try:
            async with self._admission_lock:
                if self._active_connections >= self.config.max_connections:
                    downstream_writer.close()
                    try:
                        await downstream_writer.wait_closed()
                    except Exception:
                        pass
                    return
                self._active_connections += 1
                admitted = True

            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    self._open_connection(
                        self.config.upstream_host,
                        self.config.upstream_port,
                        limit=self.config.io_chunk_bytes,
                    ),
                    timeout=self.config.connect_timeout_s,
                )
            except Exception:
                downstream_writer.close()
                try:
                    await downstream_writer.wait_closed()
                except Exception:
                    pass
                return

            try:
                left = asyncio.create_task(
                    self._pump(downstream_reader, upstream_writer)
                )
                right = asyncio.create_task(
                    self._pump(upstream_reader, downstream_writer)
                )
                done, pending = await asyncio.wait(
                    {left, right},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for pending_task in pending:
                    pending_task.cancel()
                await asyncio.gather(*done, *pending, return_exceptions=True)
            finally:
                for writer in (upstream_writer, downstream_writer):
                    writer.close()
                for writer in (upstream_writer, downstream_writer):
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
        finally:
            if admitted:
                async with self._admission_lock:
                    self._active_connections -= 1
            if task is not None:
                self._active.discard(task)

    async def _pump(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        while True:
            chunk = await reader.read(self.config.io_chunk_bytes)
            if not chunk:
                try:
                    writer.write_eof()
                    await writer.drain()
                except (AttributeError, NotImplementedError, RuntimeError):
                    pass
                return
            writer.write(chunk)
            await writer.drain()


async def run(config: LiveEdgeConfig) -> None:
    edge = VeraRelayLiveEdge(config)
    server = await edge.start()
    try:
        async with server:
            await server.serve_forever()
    finally:
        await edge.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Transparent VeraRelay/VeraMesh live edge for end-to-end VeraPort TLS"
        )
    )
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=17445)
    parser.add_argument("--upstream-host", required=True)
    parser.add_argument("--upstream-port", type=int, default=17444)
    parser.add_argument("--max-connections", type=int, default=32)
    parser.add_argument("--connect-timeout-s", type=float, default=5.0)
    args = parser.parse_args()
    config = LiveEdgeConfig(
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        upstream_host=args.upstream_host,
        upstream_port=args.upstream_port,
        max_connections=args.max_connections,
        connect_timeout_s=args.connect_timeout_s,
    )
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
