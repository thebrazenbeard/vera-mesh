from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class StateStoreError(RuntimeError):
    code = "STATE_STORE_ERROR"


class IdempotencyConflict(StateStoreError):
    code = "IDEMPOTENCY_CONFLICT"


class RequestOutcomeUnknown(StateStoreError):
    code = "REQUEST_OUTCOME_UNKNOWN"


class StateStoreCapacityExceeded(StateStoreError):
    code = "STATE_STORE_CAPACITY_EXCEEDED"


@dataclass(frozen=True)
class BeginRequest:
    disposition: str
    response: dict[str, Any] | None = None


class AgentStateStore:
    """Durable fencing and mutation-idempotency state.

    Read-only operations are intentionally not journaled by VeraPortAgent. Mutation
    records are bounded by max_mutation_records. Capacity exhaustion fails closed
    before admitting a new mutation rather than deleting replay evidence and risking
    re-execution of an old request ID.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_mutation_records: int = 100_000,
    ) -> None:
        if max_mutation_records < 1:
            raise ValueError("max_mutation_records must be positive")
        self.path = Path(path)
        self.max_mutation_records = max_mutation_records
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                int_value INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY,
                request_sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('PENDING','COMPLETED')),
                response_json TEXT,
                started_at_ms INTEGER NOT NULL,
                completed_at_ms INTEGER
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def next_fence(self) -> int:
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.connection.execute(
                    "SELECT int_value FROM metadata WHERE key = 'fence_counter'"
                ).fetchone()
                current = 0 if row is None else int(row["int_value"])
                value = current + 1
                self.connection.execute(
                    "INSERT INTO metadata(key, int_value) VALUES('fence_counter', ?) "
                    "ON CONFLICT(key) DO UPDATE SET int_value=excluded.int_value",
                    (value,),
                )
                self.connection.commit()
                return value
            except Exception:
                self.connection.rollback()
                raise

    def begin_request(
        self,
        request_id: str,
        request_sha256: str,
        *,
        now_ms: int | None = None,
    ) -> BeginRequest:
        now = int(time.time() * 1000) if now_ms is None else now_ms
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.connection.execute(
                    "SELECT request_sha256, status, response_json "
                    "FROM requests WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    count = int(
                        self.connection.execute(
                            "SELECT COUNT(*) AS n FROM requests"
                        ).fetchone()["n"]
                    )
                    if count >= self.max_mutation_records:
                        raise StateStoreCapacityExceeded(
                            "mutation idempotency ledger reached configured capacity; "
                            "new mutation admission is blocked until separately governed maintenance"
                        )
                    self.connection.execute(
                        "INSERT INTO requests("
                        "request_id, request_sha256, status, started_at_ms"
                        ") VALUES(?,?, 'PENDING', ?)",
                        (request_id, request_sha256, now),
                    )
                    self.connection.commit()
                    return BeginRequest("NEW")
                if row["request_sha256"] != request_sha256:
                    raise IdempotencyConflict(request_id)
                if row["status"] == "COMPLETED":
                    response_json = row["response_json"]
                    if response_json is None:
                        raise StateStoreError("completed request lacks response")
                    value = json.loads(response_json)
                    if not isinstance(value, dict):
                        raise StateStoreError("stored response is not object")
                    self.connection.commit()
                    return BeginRequest("REPLAY", value)
                raise RequestOutcomeUnknown(request_id)
            except Exception:
                self.connection.rollback()
                raise

    def complete_request(
        self,
        request_id: str,
        request_sha256: str,
        response: dict[str, Any],
        *,
        now_ms: int | None = None,
    ) -> None:
        now = int(time.time() * 1000) if now_ms is None else now_ms
        payload = json.dumps(
            response,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.connection.execute(
                    "SELECT request_sha256, status, response_json "
                    "FROM requests WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    raise StateStoreError("cannot complete unknown request")
                if row["request_sha256"] != request_sha256:
                    raise IdempotencyConflict(request_id)
                if row["status"] == "COMPLETED":
                    if row["response_json"] != payload:
                        raise IdempotencyConflict(request_id)
                    self.connection.commit()
                    return
                self.connection.execute(
                    "UPDATE requests SET status='COMPLETED', response_json=?, "
                    "completed_at_ms=? WHERE request_id=?",
                    (payload, now, request_id),
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise

    def health(self) -> dict[str, int | str]:
        with self._lock:
            row = self.connection.execute(
                "SELECT "
                "COUNT(*) AS total, "
                "SUM(CASE WHEN status='PENDING' THEN 1 ELSE 0 END) AS pending "
                "FROM requests"
            ).fetchone()
            total = int(row["total"])
            pending = int(row["pending"] or 0)
        remaining = max(0, self.max_mutation_records - total)
        return {
            "status": "DEGRADED" if remaining == 0 else "OK",
            "mutation_records": total,
            "pending_mutations": pending,
            "max_mutation_records": self.max_mutation_records,
            "remaining_mutation_capacity": remaining,
        }
