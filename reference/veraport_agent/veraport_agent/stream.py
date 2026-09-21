from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import Awaitable, Callable
from typing import Any


class StreamProtocolError(RuntimeError):
    code = "STREAM_PROTOCOL_ERROR"


class FrameTooLarge(StreamProtocolError):
    code = "FRAME_TOO_LARGE"


class StreamClosed(StreamProtocolError):
    code = "STREAM_CLOSED"


async def read_frame(reader: asyncio.StreamReader, *, max_frame_bytes: int = 1_048_576) -> dict[str, Any]:
    try:
        header = await reader.readexactly(4)
    except asyncio.IncompleteReadError as exc:
        raise StreamClosed("stream closed while reading frame header") from exc
    (size,) = struct.unpack("!I", header)
    if size < 2 or size > max_frame_bytes:
        raise FrameTooLarge(f"frame size {size} exceeds policy")
    try:
        raw = await reader.readexactly(size)
    except asyncio.IncompleteReadError as exc:
        raise StreamClosed("stream closed while reading frame body") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StreamProtocolError("frame is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise StreamProtocolError("frame must be a JSON object")
    return value


async def write_frame(
    writer: asyncio.StreamWriter,
    value: dict[str, Any],
    *,
    max_frame_bytes: int = 1_048_576,
) -> None:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if len(raw) > max_frame_bytes:
        raise FrameTooLarge(f"encoded frame size {len(raw)} exceeds policy")
    writer.write(struct.pack("!I", len(raw)) + raw)
    await writer.drain()


class MultiplexClient:
    """Many correlated requests over one already-authenticated byte stream."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        max_frame_bytes: int = 1_048_576,
        request_timeout_s: float = 5.0,
    ) -> None:
        if request_timeout_s <= 0:
            raise ValueError("request_timeout_s must be positive")
        self.reader = reader
        self.writer = writer
        self.max_frame_bytes = max_frame_bytes
        self.request_timeout_s = request_timeout_s
        self._write_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._closed = False
        self._reader_task = asyncio.create_task(self._read_loop())

    async def request(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id is required")
        if self._closed:
            raise StreamClosed("multiplex client is closed")
        if request_id in self._pending:
            raise StreamProtocolError(f"duplicate in-flight request_id: {request_id}")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        try:
            async with self._write_lock:
                await write_frame(
                    self.writer,
                    request,
                    max_frame_bytes=self.max_frame_bytes,
                )
            try:
                return await asyncio.wait_for(
                    future,
                    timeout=self.request_timeout_s,
                )
            except TimeoutError as exc:
                self._pending.pop(request_id, None)
                await self.close()
                raise StreamClosed(
                    f"request timed out: {request_id}"
                ) from exc
        except Exception:
            self._pending.pop(request_id, None)
            raise

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.writer.close()
        try:
            await self.writer.wait_closed()
        finally:
            self._reader_task.cancel()
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(StreamClosed("stream closed"))
            self._pending.clear()

    async def _read_loop(self) -> None:
        try:
            while not self._closed:
                response = await read_frame(self.reader, max_frame_bytes=self.max_frame_bytes)
                request_id = response.get("request_id")
                if not isinstance(request_id, str):
                    raise StreamProtocolError("response request_id is required")
                future = self._pending.pop(request_id, None)
                if future is None:
                    raise StreamProtocolError(f"unsolicited response: {request_id}")
                if not future.done():
                    future.set_result(response)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._closed = True
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(exc)
            self._pending.clear()


async def serve_multiplexed(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    *,
    max_frame_bytes: int = 1_048_576,
    max_inflight: int = 64,
) -> None:
    """Dispatch independent frames concurrently over one stream connection.

    Authentication/session admission is intentionally outside this function:
    callers must only invoke it after the connection is bound to an accepted
    VeraMesh hot session.
    """
    if max_inflight < 1:
        raise ValueError("max_inflight must be positive")
    write_lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(max_inflight)
    tasks: set[asyncio.Task[None]] = set()

    async def run_one(request: dict[str, Any]) -> None:
        async with semaphore:
            try:
                response = await handler(request)
            except Exception as exc:
                response = {
                    "protocol_version": "veraport-v1",
                    "request_id": request.get("request_id"),
                    "ok": False,
                    "error": {"code": exc.__class__.__name__.upper(), "message": str(exc)},
                }
            async with write_lock:
                await write_frame(writer, response, max_frame_bytes=max_frame_bytes)

    try:
        while True:
            request = await read_frame(reader, max_frame_bytes=max_frame_bytes)
            task = asyncio.create_task(run_one(request))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    except StreamClosed:
        pass
    finally:
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
