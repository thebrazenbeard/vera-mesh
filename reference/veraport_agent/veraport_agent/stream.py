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


class RequestTimeout(StreamClosed):
    code = "REQUEST_TIMEOUT"


async def read_frame(
    reader: asyncio.StreamReader,
    *,
    max_frame_bytes: int = 1_048_576,
) -> dict[str, Any]:
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


def encoded_frame_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


async def write_frame(
    writer: asyncio.StreamWriter,
    value: dict[str, Any],
    *,
    max_frame_bytes: int = 1_048_576,
) -> None:
    raw = encoded_frame_bytes(value)
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
        request_timeout_s: float = 10.0,
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
                    asyncio.shield(future),
                    timeout=self.request_timeout_s,
                )
            except TimeoutError as exc:
                timeout = RequestTimeout(
                    f"request {request_id} exceeded {self.request_timeout_s}s"
                )
                self._pending.pop(request_id, None)
                await self._abort(timeout)
                raise timeout from exc
        except Exception:
            self._pending.pop(request_id, None)
            raise

    async def close(self) -> None:
        await self._abort(StreamClosed("stream closed"))

    async def _abort(self, reason: Exception) -> None:
        if not self._closed:
            self._closed = True
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
        current = asyncio.current_task()
        if self._reader_task is not current and not self._reader_task.done():
            self._reader_task.cancel()
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(reason)
        self._pending.clear()

    async def _read_loop(self) -> None:
        try:
            while not self._closed:
                response = await read_frame(
                    self.reader,
                    max_frame_bytes=self.max_frame_bytes,
                )
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
            await self._abort(exc)


async def serve_multiplexed(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    *,
    max_frame_bytes: int = 1_048_576,
    max_inflight: int = 64,
    teardown_timeout_s: float = 10.0,
) -> None:
    """Dispatch a bounded number of correlated requests over one authenticated stream.

    Capacity is acquired before another frame is admitted into a handler task. This
    makes max_inflight a bound on admitted outstanding requests rather than merely a
    bound on simultaneously executing handler bodies.
    """
    if max_inflight < 1:
        raise ValueError("max_inflight must be positive")
    if teardown_timeout_s <= 0:
        raise ValueError("teardown_timeout_s must be positive")

    write_lock = asyncio.Lock()
    capacity = asyncio.Semaphore(max_inflight)
    tasks: set[asyncio.Task[None]] = set()
    stream_failed = asyncio.Event()

    async def send_response(
        request_id: Any,
        response: dict[str, Any],
    ) -> None:
        async with write_lock:
            try:
                await write_frame(
                    writer,
                    response,
                    max_frame_bytes=max_frame_bytes,
                )
            except FrameTooLarge:
                fallback = {
                    "protocol_version": "veraport-v1",
                    "request_id": request_id if isinstance(request_id, str) else None,
                    "ok": False,
                    "error": {
                        "code": FrameTooLarge.code,
                        "message": "response exceeds VeraPort wire-frame policy",
                    },
                }
                try:
                    await write_frame(
                        writer,
                        fallback,
                        max_frame_bytes=max_frame_bytes,
                    )
                except Exception:
                    stream_failed.set()
                    writer.close()
                    raise
            except Exception:
                stream_failed.set()
                writer.close()
                raise

    async def run_one(request: dict[str, Any]) -> None:
        try:
            try:
                response = await handler(request)
            except Exception as exc:
                response = {
                    "protocol_version": "veraport-v1",
                    "request_id": request.get("request_id"),
                    "ok": False,
                    "error": {
                        "code": getattr(
                            exc,
                            "code",
                            exc.__class__.__name__.upper(),
                        ),
                        "message": str(exc),
                    },
                }
            await send_response(request.get("request_id"), response)
        finally:
            capacity.release()

    try:
        while not stream_failed.is_set():
            await capacity.acquire()
            try:
                request = await read_frame(
                    reader,
                    max_frame_bytes=max_frame_bytes,
                )
            except Exception:
                capacity.release()
                raise
            task = asyncio.create_task(run_one(request))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    except StreamClosed:
        pass
    finally:
        if tasks:
            done, pending = await asyncio.wait(
                tuple(tasks),
                timeout=teardown_timeout_s,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if done:
                await asyncio.gather(*done, return_exceptions=True)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
