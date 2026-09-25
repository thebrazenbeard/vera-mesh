from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DesktopCommanderStdioHostConfig:
    listen_host: str
    listen_port: int
    executable: Path
    entrypoint: Path
    arguments: tuple[str, ...] = ("--no-onboarding",)
    cwd: Path | None = None
    max_connections: int = 8
    io_chunk_bytes: int = 65_536

    def __post_init__(self) -> None:
        if not self.listen_host:
            raise ValueError("listen_host is required")
        if type(self.listen_port) is not int or not 1 <= self.listen_port <= 65_535:
            raise ValueError("listen_port must be in 1..65535")
        if type(self.max_connections) is not int or not 1 <= self.max_connections <= 64:
            raise ValueError("max_connections must be in 1..64")
        if type(self.io_chunk_bytes) is not int or not 1024 <= self.io_chunk_bytes <= 1_048_576:
            raise ValueError("io_chunk_bytes must be in 1024..1048576")


class DesktopCommanderStdioHost:
    """Expose an exact Desktop Commander stdio MCP process as an opaque stream.

    This wrapper owns transport only. It never parses MCP and never interprets,
    filters, or rewrites Desktop Commander tool calls or command strings.
    """

    def __init__(self, config: DesktopCommanderStdioHostConfig) -> None:
        self.config = config
        self._server: asyncio.AbstractServer | None = None
        self._admission_lock = asyncio.Lock()
        self._active_connections = 0
        self._active: set[asyncio.Task[Any]] = set()

    async def start(self) -> asyncio.AbstractServer:
        if self._server is not None:
            return self._server
        if not self.config.executable.is_file():
            raise FileNotFoundError(self.config.executable)
        if not self.config.entrypoint.is_file():
            raise FileNotFoundError(self.config.entrypoint)
        self._server = await asyncio.start_server(
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
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._active.add(task)
        admitted = False
        process: asyncio.subprocess.Process | None = None
        try:
            async with self._admission_lock:
                if self._active_connections >= self.config.max_connections:
                    writer.close()
                    await writer.wait_closed()
                    return
                self._active_connections += 1
                admitted = True

            env = dict(os.environ)
            env["DC_REMOTE_DEVICE"] = "true"
            command = [
                str(self.config.executable),
                str(self.config.entrypoint),
                *self.config.arguments,
            ]
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self.config.cwd or self.config.entrypoint.parent.parent),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("Desktop Commander stdio pipes unavailable")

            upstream = asyncio.create_task(
                self._pump_reader_to_process(reader, process.stdin)
            )
            downstream = asyncio.create_task(
                self._pump_process_to_writer(process.stdout, writer)
            )
            done, pending = await asyncio.wait(
                {upstream, downstream},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for pending_task in pending:
                pending_task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        finally:
            if process is not None and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            if admitted:
                async with self._admission_lock:
                    self._active_connections -= 1
            if task is not None:
                self._active.discard(task)

    async def _pump_reader_to_process(
        self,
        reader: asyncio.StreamReader,
        stdin: asyncio.StreamWriter,
    ) -> None:
        while True:
            chunk = await reader.read(self.config.io_chunk_bytes)
            if not chunk:
                try:
                    stdin.write_eof()
                except (AttributeError, NotImplementedError, RuntimeError):
                    stdin.close()
                return
            stdin.write(chunk)
            await stdin.drain()

    async def _pump_process_to_writer(
        self,
        stdout: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        while True:
            chunk = await stdout.read(self.config.io_chunk_bytes)
            if not chunk:
                return
            writer.write(chunk)
            await writer.drain()


async def run(config: DesktopCommanderStdioHostConfig) -> None:
    host = DesktopCommanderStdioHost(config)
    server = await host.start()
    try:
        async with server:
            await server.serve_forever()
    finally:
        await host.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Expose exact DesktopCommanderMCP stdio as an opaque local stream"
    )
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=17446)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--entrypoint", required=True)
    parser.add_argument("--cwd")
    parser.add_argument("--argument", action="append", default=["--no-onboarding"])
    args = parser.parse_args()
    asyncio.run(
        run(
            DesktopCommanderStdioHostConfig(
                listen_host=args.listen_host,
                listen_port=args.listen_port,
                executable=Path(args.executable).resolve(),
                entrypoint=Path(args.entrypoint).resolve(),
                arguments=tuple(args.argument),
                cwd=Path(args.cwd).resolve() if args.cwd else None,
            )
        )
    )


if __name__ == "__main__":
    main()
