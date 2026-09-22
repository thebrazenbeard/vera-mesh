from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest

from veraport_agent.core import ClaimMode, LaneRegistry, ResourceClaim
from veraport_agent.executor import LocalExecutor
from veraport_agent.protocol import VeraPortAgent
from veraport_agent.stream import MultiplexClient, StreamClosed, serve_multiplexed


def read_claim(path: Path) -> ResourceClaim:
    return ResourceClaim(
        "fs:" + path.resolve().as_posix(),
        ClaimMode.READ,
    )


@pytest.mark.asyncio
async def test_ranged_byte_reads_reconstruct_exact_file(tmp_path: Path) -> None:
    payload = b"alpha\x00\xff" + "snowman-☃".encode("utf-8") + b"-omega"
    target = tmp_path / "payload.bin"
    target.write_bytes(payload)

    registry = LaneRegistry({"fs.read"})
    lane = registry.open_lane(
        lane_id="range",
        task_id="range",
        capabilities={"fs.read"},
        claims=(read_claim(tmp_path),),
    )
    agent = VeraPortAgent(
        registry,
        LocalExecutor(
            registry,
            allowed_roots=(tmp_path,),
            max_read_bytes=1024,
            max_read_chunk_bytes=5,
        ),
    )

    rebuilt = bytearray()
    offset = 0
    version = None
    while True:
        response = await agent.handle({
            "protocol_version": "veraport-v1",
            "request_id": f"chunk-{offset}",
            "operation": "fs.read_bytes",
            "lane_id": lane.lane_id,
            "fencing_token": lane.fencing_token,
            "path": str(target),
            "offset": offset,
            "max_bytes": 5,
            "expected_file_version": version,
        })
        assert response["ok"] is True
        result = response["result"]
        if version is None:
            version = result["file_version"]
        assert result["file_version"] == version
        assert result["offset"] == offset
        chunk = base64.b64decode(result["content_base64"])
        assert len(chunk) == result["bytes_read"]
        rebuilt.extend(chunk)
        offset = result["next_offset"]
        if result["eof"]:
            assert result["size_bytes"] == len(payload)
            break

    assert bytes(rebuilt) == payload


@pytest.mark.asyncio
async def test_default_ranged_chunk_fits_default_frame_budget(
    tmp_path: Path,
) -> None:
    target = tmp_path / "default-chunk.bin"
    target.write_bytes(b"z" * 262_144)

    registry = LaneRegistry({"fs.read"})
    lane = registry.open_lane(
        lane_id="default-range",
        task_id="default-range",
        capabilities={"fs.read"},
        claims=(read_claim(tmp_path),),
    )
    agent = VeraPortAgent(
        registry,
        LocalExecutor(registry, allowed_roots=(tmp_path,)),
    )

    response = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "default-range",
        "operation": "fs.read_bytes",
        "lane_id": lane.lane_id,
        "fencing_token": lane.fencing_token,
        "path": str(target),
        "offset": 0,
    })
    assert response["ok"] is True
    assert response["result"]["bytes_read"] == 262_144

    encoded = json.dumps(
        response,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    assert len(encoded) < 1_048_576


@pytest.mark.asyncio
async def test_ranged_read_detects_file_change_between_chunks(
    tmp_path: Path,
) -> None:
    target = tmp_path / "changing.bin"
    target.write_bytes(b"abcdefghij")

    registry = LaneRegistry({"fs.read"})
    lane = registry.open_lane(
        lane_id="range",
        task_id="range",
        capabilities={"fs.read"},
        claims=(read_claim(tmp_path),),
    )
    agent = VeraPortAgent(
        registry,
        LocalExecutor(
            registry,
            allowed_roots=(tmp_path,),
            max_read_bytes=1024,
            max_read_chunk_bytes=4,
        ),
    )

    first = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "first",
        "operation": "fs.read_bytes",
        "lane_id": lane.lane_id,
        "fencing_token": lane.fencing_token,
        "path": str(target),
        "offset": 0,
        "max_bytes": 4,
    })
    assert first["ok"] is True

    target.write_bytes(b"ABCDEFGHIJK")
    second = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "second",
        "operation": "fs.read_bytes",
        "lane_id": lane.lane_id,
        "fencing_token": lane.fencing_token,
        "path": str(target),
        "offset": first["result"]["next_offset"],
        "max_bytes": 4,
        "expected_file_version": first["result"]["file_version"],
    })

    assert second["ok"] is False
    assert second["error"]["code"] == "FILE_VERSION_CHANGED"


@pytest.mark.asyncio
async def test_ranged_read_reauthorizes_after_lane_close(tmp_path: Path) -> None:
    target = tmp_path / "closed.bin"
    target.write_bytes(b"abcdef")

    registry = LaneRegistry({"fs.read"})
    lane = registry.open_lane(
        lane_id="range",
        task_id="range",
        capabilities={"fs.read"},
        claims=(read_claim(tmp_path),),
    )
    agent = VeraPortAgent(
        registry,
        LocalExecutor(
            registry,
            allowed_roots=(tmp_path,),
            max_read_bytes=1024,
            max_read_chunk_bytes=3,
        ),
    )

    first = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "first",
        "operation": "fs.read_bytes",
        "lane_id": lane.lane_id,
        "fencing_token": lane.fencing_token,
        "path": str(target),
        "offset": 0,
        "max_bytes": 3,
    })
    assert first["ok"] is True

    registry.close(lane.lane_id, lane.fencing_token)
    after = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "after-close",
        "operation": "fs.read_bytes",
        "lane_id": lane.lane_id,
        "fencing_token": lane.fencing_token,
        "path": str(target),
        "offset": 3,
        "max_bytes": 3,
        "expected_file_version": first["result"]["file_version"],
    })

    assert after["ok"] is False
    assert after["error"]["code"] == "LANE_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "x" * 600,
        "\x00" * 200,
        "é" * 200,
    ],
)
async def test_legacy_text_read_overflow_returns_correlated_error(
    tmp_path: Path,
    text: str,
) -> None:
    target = tmp_path / "wire.txt"
    target.write_text(text, encoding="utf-8")

    registry = LaneRegistry({"fs.read"})
    agent = VeraPortAgent(
        registry,
        LocalExecutor(
            registry,
            allowed_roots=(tmp_path,),
            max_read_bytes=4096,
        ),
    )

    server = await asyncio.start_server(
        lambda reader, writer: serve_multiplexed(
            reader,
            writer,
            agent.handle,
            max_frame_bytes=512,
        ),
        "127.0.0.1",
        0,
    )
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection(
        "127.0.0.1",
        port,
    )
    client = MultiplexClient(
        reader,
        writer,
        max_frame_bytes=512,
        request_timeout_s=1.0,
    )
    try:
        opened = await client.request({
            "protocol_version": "veraport-v1",
            "request_id": "open",
            "operation": "lane.open",
            "lane_id": "wire",
            "task_id": "wire",
            "capabilities": ["fs.read"],
            "claims": [{
                "key": "fs:" + tmp_path.resolve().as_posix(),
                "mode": "read",
            }],
        })
        response = await client.request({
            "protocol_version": "veraport-v1",
            "request_id": "read",
            "operation": "fs.read_text",
            "lane_id": "wire",
            "fencing_token": opened["result"]["fencing_token"],
            "path": str(target),
        })
        assert response["request_id"] == "read"
        assert response["ok"] is False
        assert response["error"]["code"] == "FRAME_TOO_LARGE"
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_if_overflow_error_cannot_fit_stream_closes_deterministically() -> None:
    async def handler(request):
        return {
            "protocol_version": "veraport-v1",
            "request_id": request["request_id"],
            "ok": True,
            "result": {"content": "x" * 1000},
        }

    server = await asyncio.start_server(
        lambda reader, writer: serve_multiplexed(
            reader,
            writer,
            handler,
            max_frame_bytes=48,
        ),
        "127.0.0.1",
        0,
    )
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    client = MultiplexClient(
        reader,
        writer,
        max_frame_bytes=48,
        request_timeout_s=1.0,
    )
    try:
        with pytest.raises(StreamClosed):
            await client.request({"request_id": "x"})
    finally:
        await client.close()
        server.close()
        await server.wait_closed()
