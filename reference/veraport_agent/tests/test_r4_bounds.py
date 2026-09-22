from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

import veraport_agent.stream as stream_module
from veraport_agent.core import LaneRegistry
from veraport_agent.executor import LocalExecutor
from veraport_agent.protocol import VeraPortAgent
from veraport_agent.state import (
    AgentStateStore,
    StateStoreError,
)
from veraport_agent.stream import read_frame, serve_multiplexed, write_frame


@pytest.mark.asyncio
async def test_multiplex_admission_is_bounded_before_task_creation(monkeypatch):
    gate = asyncio.Event()
    started = 0
    created = []
    real_create_task = stream_module.asyncio.create_task

    def tracked_create_task(coro, *args, **kwargs):
        task = real_create_task(coro, *args, **kwargs)
        created.append(task)
        return task

    monkeypatch.setattr(
        stream_module.asyncio,
        "create_task",
        tracked_create_task,
    )

    async def handler(request):
        nonlocal started
        started += 1
        await gate.wait()
        return {
            "protocol_version": "veraport-v1",
            "request_id": request["request_id"],
            "ok": True,
            "result": {},
        }

    server = await asyncio.start_server(
        lambda reader, writer: serve_multiplexed(
            reader,
            writer,
            handler,
            max_inflight=3,
        ),
        "127.0.0.1",
        0,
    )
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", port
    )
    try:
        for index in range(20):
            await write_frame(
                writer,
                {
                    "protocol_version": "veraport-v1",
                    "request_id": f"r-{index}",
                    "operation": "lane.list",
                },
            )
        await asyncio.sleep(0.05)

        assert started == 3
        assert len(created) == 3
        assert sum(not task.done() for task in created) <= 3

        gate.set()
        responses = [
            await asyncio.wait_for(read_frame(reader), timeout=2)
            for _ in range(20)
        ]
        assert {
            item["request_id"] for item in responses
        } == {f"r-{index}" for index in range(20)}
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_high_volume_read_probes_do_not_grow_request_ledger(
    tmp_path,
):
    store = AgentStateStore(
        tmp_path / "state.sqlite3",
        max_mutation_requests=4,
        warn_at_records=3,
    )
    registry = LaneRegistry(
        {"fs.read"},
        fence_allocator=store.next_fence,
    )
    agent = VeraPortAgent(
        registry,
        LocalExecutor(registry, allowed_roots=(tmp_path,)),
        store,
    )
    try:
        for index in range(100_001):
            response = await agent.handle({
                "protocol_version": "veraport-v1",
                "request_id": f"probe-{index}",
                "operation": "lane.list",
            })
            assert response["ok"] is True
        assert store.request_record_count() == 0
        assert (
            store.request_ledger_health()["status"]
            == "HEALTHY"
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_mutation_ledger_capacity_fails_closed_without_forgetting(
    tmp_path,
):
    store = AgentStateStore(
        tmp_path / "state.sqlite3",
        max_mutation_requests=2,
        warn_at_records=1,
    )
    registry = LaneRegistry(
        {"fs.read"},
        fence_allocator=store.next_fence,
    )
    agent = VeraPortAgent(
        registry,
        LocalExecutor(registry, allowed_roots=(tmp_path,)),
        store,
    )
    first = {
        "protocol_version": "veraport-v1",
        "request_id": "m-1",
        "operation": "lane.open",
        "lane_id": "one",
        "task_id": "one",
        "capabilities": ["fs.read"],
        "claims": [],
    }
    second = {
        "protocol_version": "veraport-v1",
        "request_id": "m-2",
        "operation": "lane.open",
        "lane_id": "two",
        "task_id": "two",
        "capabilities": ["fs.read"],
        "claims": [],
    }
    third = {
        "protocol_version": "veraport-v1",
        "request_id": "m-3",
        "operation": "lane.open",
        "lane_id": "three",
        "task_id": "three",
        "capabilities": ["fs.read"],
        "claims": [],
    }
    try:
        first_result = await agent.handle(first)
        assert first_result["ok"] is True
        assert (await agent.handle(second))["ok"] is True

        replay = await agent.handle(first)
        assert replay["ok"] is True
        assert replay["replay"]["durable_evidence"] is True

        blocked = await agent.handle(third)
        assert blocked["ok"] is False
        assert (
            blocked["error"]["code"]
            == "REQUEST_LEDGER_CAPACITY_EXCEEDED"
        )
        assert {
            lane.lane_id for lane in registry.snapshot()
        } == {"one", "two"}

        health = store.request_ledger_health()
        assert health["status"] == "FULL_FAIL_CLOSED"
        assert health["mutation_records"] == 2
        assert health["remaining_new_mutations"] == 0
    finally:
        store.close()


def test_request_ledger_reports_degraded_before_full(tmp_path):
    store = AgentStateStore(
        tmp_path / "state.sqlite3",
        max_mutation_requests=4,
        warn_at_records=3,
    )
    try:
        for index in range(3):
            request_id = f"m-{index}"
            digest = hashlib.sha256(
                request_id.encode("utf-8")
            ).hexdigest()
            assert (
                store.begin_request(
                    request_id, digest
                ).disposition
                == "NEW"
            )
            store.complete_request(
                request_id,
                digest,
                {
                    "protocol_version": "veraport-v1",
                    "request_id": request_id,
                    "ok": True,
                    "result": {},
                },
            )
        health = store.request_ledger_health()
        assert health["status"] == "DEGRADED_NEAR_CAPACITY"
        assert health["mutation_records"] == 3
        assert health["max_mutation_requests"] == 4
    finally:
        store.close()


class FailMutationLedger:
    def begin_request(self, request_id, request_sha256):
        raise StateStoreError("simulated disk-full write failure")

    def complete_request(
        self, request_id, request_sha256, response
    ):
        raise AssertionError("complete must not run")

    def request_ledger_health(self):
        return {
            "schema": "VERAPORT_REQUEST_LEDGER_HEALTH_V1",
            "status": "DEGRADED_NEAR_CAPACITY",
            "mutation_records": 10,
            "warn_at_records": 10,
            "max_mutation_requests": 11,
            "remaining_new_mutations": 1,
            "retention":
                "NO_AUTOMATIC_EVICTION_FAIL_CLOSED_AT_CAPACITY",
        }


@pytest.mark.asyncio
async def test_mutation_ledger_failure_blocks_before_mutation(tmp_path):
    registry = LaneRegistry({"fs.read"})
    agent = VeraPortAgent(
        registry,
        LocalExecutor(registry, allowed_roots=(tmp_path,)),
        FailMutationLedger(),
    )
    response = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "blocked",
        "operation": "lane.open",
        "lane_id": "must-not-exist",
        "task_id": "blocked",
        "capabilities": ["fs.read"],
        "claims": [],
    })
    assert response["ok"] is False
    assert response["error"]["code"] == "STATE_STORE_ERROR"
    assert registry.snapshot() == ()


@pytest.mark.asyncio
async def test_read_probe_ignores_mutation_ledger_write_failure(
    tmp_path,
):
    registry = LaneRegistry({"fs.read"})
    agent = VeraPortAgent(
        registry,
        LocalExecutor(registry, allowed_roots=(tmp_path,)),
        FailMutationLedger(),
    )
    response = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "read-probe",
        "operation": "lane.list",
    })
    assert response["ok"] is True
    assert response["result"]["lanes"] == []
    assert (
        response["result"]["request_ledger"]["status"]
        == "DEGRADED_NEAR_CAPACITY"
    )


def test_request_ledger_health_exposes_storage_pending_and_age(tmp_path):
    store = AgentStateStore(
        tmp_path / "state.sqlite3",
        max_mutation_requests=4,
        warn_at_records=3,
    )
    try:
        assert store.begin_request(
            "pending-health",
            hashlib.sha256(b"pending-health").hexdigest(),
            now_ms=1_000,
        ).disposition == "NEW"

        health = store.request_ledger_health(now_ms=1_250)
        assert health["status"] == "HEALTHY"
        assert health["mutation_records"] == 1
        assert health["detail_records"] == 1
        assert health["tombstone_records"] == 0
        assert health["pending_records"] == 1
        assert health["oldest_mutation_started_at_ms"] == 1_000
        assert health["oldest_mutation_age_ms"] == 250
        assert health["oldest_pending_started_at_ms"] == 1_000
        assert health["oldest_pending_age_ms"] == 250
        assert health["write_admission"] == "ALLOWED_BY_LEDGER_CAPACITY"
        assert health["degraded_reason"] is None
        assert health["state_db_bytes"] > 0
        assert health["wal_bytes"] >= 0
        assert health["shm_bytes"] >= 0
        assert health["journal_mode"] == "WAL"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_read_probe_survives_ledger_health_inspection_failure(tmp_path):
    store = AgentStateStore(tmp_path / "state.sqlite3")
    store.close()
    registry = LaneRegistry({"fs.read"})
    agent = VeraPortAgent(
        registry,
        LocalExecutor(registry, allowed_roots=(tmp_path,)),
        store,
    )

    response = await agent.handle({
        "protocol_version": "veraport-v1",
        "request_id": "health-unavailable",
        "operation": "lane.list",
    })

    assert response["ok"] is True
    health = response["result"]["request_ledger"]
    assert health["status"] == "DEGRADED_STORE_UNAVAILABLE"
    assert health["write_admission"] == "UNKNOWN_FAIL_CLOSED"
    assert health["degraded_reason"] == "STATE_STORE_UNAVAILABLE"
