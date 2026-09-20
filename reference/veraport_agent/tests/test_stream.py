import asyncio

import pytest

from veraport_agent.stream import (
    FrameTooLarge,
    MultiplexClient,
    StreamProtocolError,
    serve_multiplexed,
    write_frame,
)


@pytest.mark.asyncio
async def test_one_connection_dispatches_multiple_requests_concurrently():
    active = 0
    max_active = 0
    order = []
    lock = asyncio.Lock()

    async def handler(request):
        nonlocal active, max_active
        async with lock:
            active += 1
            max_active = max(max_active, active)
        await asyncio.sleep(request["delay"])
        order.append(request["request_id"])
        async with lock:
            active -= 1
        return {
            "protocol_version": "veraport-v1",
            "request_id": request["request_id"],
            "ok": True,
            "result": {"value": request["request_id"]},
        }

    server = await asyncio.start_server(
        lambda reader, writer: serve_multiplexed(reader, writer, handler),
        "127.0.0.1",
        0,
    )
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    client = MultiplexClient(reader, writer)
    try:
        slow, fast = await asyncio.gather(
            client.request({"request_id": "slow", "delay": 0.08}),
            client.request({"request_id": "fast", "delay": 0.01}),
        )
        assert max_active >= 2
        assert order[0] == "fast"
        assert slow["request_id"] == "slow"
        assert fast["request_id"] == "fast"
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_duplicate_inflight_request_id_rejected():
    gate = asyncio.Event()

    async def handler(request):
        await gate.wait()
        return {"request_id": request["request_id"], "ok": True, "result": {}}

    server = await asyncio.start_server(
        lambda reader, writer: serve_multiplexed(reader, writer, handler),
        "127.0.0.1",
        0,
    )
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    client = MultiplexClient(reader, writer)
    try:
        first = asyncio.create_task(client.request({"request_id": "same"}))
        await asyncio.sleep(0.01)
        with pytest.raises(StreamProtocolError):
            await client.request({"request_id": "same"})
        gate.set()
        assert (await first)["request_id"] == "same"
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_frame_limit_rejects_large_write():
    class DummyWriter:
        def write(self, data):
            return None

        async def drain(self):
            return None

    with pytest.raises(FrameTooLarge):
        await write_frame(DummyWriter(), {"x": "a" * 100}, max_frame_bytes=20)
