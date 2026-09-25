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


class RequestLedgerCapacityExceeded(StateStoreError):
    code = "REQUEST_LEDGER_CAPACITY_EXCEEDED"


@dataclass(frozen=True)
class BeginRequest:
    disposition: str
    response: dict[str, Any] | None = None


class AgentStateStore:
    """Durable fencing and bounded mutation idempotency state.

    Read-only operations do not belong in this request ledger. Mutation IDs are
    never automatically evicted: forgetting a mutation ID could make a late
    retry execute again. Instead this store has a hard record capacity. Near
    capacity it reports DEGRADED; at capacity new mutation IDs fail closed while
    exact existing IDs remain replayable. Expansion/compaction therefore
    requires a separately reviewed migration that preserves replay safety.
    """

    DEFAULT_MAX_MUTATION_REQUESTS = 100_000
    DEFAULT_WARN_FRACTION = 0.90

    def __init__(
        self,
        path: str | Path,
        *,
        max_mutation_requests: int = DEFAULT_MAX_MUTATION_REQUESTS,
        warn_at_records: int | None = None,
    ) -> None:
        if type(max_mutation_requests) is not int or max_mutation_requests < 1:
            raise ValueError("max_mutation_requests must be a positive integer")
        if warn_at_records is None:
            warn_at_records = max(
                1,
                int(max_mutation_requests * self.DEFAULT_WARN_FRACTION),
            )
        if (
            type(warn_at_records) is not int
            or warn_at_records < 1
            or warn_at_records > max_mutation_requests
        ):
            raise ValueError(
                "warn_at_records must be in 1..max_mutation_requests"
            )

        self.path = Path(path)
        self.max_mutation_requests = max_mutation_requests
        self.warn_at_records = warn_at_records
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

    def request_record_count(self) -> int:
        with self._lock:
            row = self.connection.execute(
                "SELECT COUNT(*) AS count FROM requests"
            ).fetchone()
            return int(row["count"])

    def request_ledger_health(self) -> dict[str, Any]:
        count = self.request_record_count()
        if count >= self.max_mutation_requests:
            status = "FULL_FAIL_CLOSED"
        elif count >= self.warn_at_records:
            status = "DEGRADED_NEAR_CAPACITY"
        else:
            status = "HEALTHY"
        return {
            "schema": "VERAPORT_REQUEST_LEDGER_HEALTH_V1",
            "status": status,
            "mutation_records": count,
            "warn_at_records": self.warn_at_records,
            "max_mutation_requests": self.max_mutation_requests,
            "remaining_new_mutations": max(
                0, self.max_mutation_requests - count
            ),
            "retention": "NO_AUTOMATIC_EVICTION_FAIL_CLOSED_AT_CAPACITY",
        }

    def begin_request(
        self,
        request_id: str,
        request_sha256: str,
        *,
        now_ms: int | None = None,
    ) -> BeginRequest:
        now = int(time.time() * 1000) if now_ms is None else now_ms
        with self._lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                row = self.connection.execute(
                    "SELECT request_sha256, status, response_json "
                    "FROM requests WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    count_row = self.connection.execute(
                        "SELECT COUNT(*) AS count FROM requests"
                    ).fetchone()
                    count = int(count_row["count"])
                    if count >= self.max_mutation_requests:
                        raise RequestLedgerCapacityExceeded(
                            "mutation request ledger is full; "
                            "new mutation admission is blocked"
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
                        raise StateStoreError(
                            "completed request lacks response"
                        )
                    value = json.loads(response_json)
                    if not isinstance(value, dict):
                        raise StateStoreError(
                            "stored response is not object"
                        )
                    self.connection.commit()
                    return BeginRequest("REPLAY", value)
                raise RequestOutcomeUnknown(request_id)
            except (
                IdempotencyConflict,
                RequestOutcomeUnknown,
                RequestLedgerCapacityExceeded,
                StateStoreError,
            ):
                self.connection.rollback()
                raise
            except sqlite3.Error as exc:
                self.connection.rollback()
                raise StateStoreError(
                    "mutation request ledger unavailable"
                ) from exc
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
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                row = self.connection.execute(
                    "SELECT request_sha256, status, response_json "
                    "FROM requests WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    raise StateStoreError(
                        "cannot complete unknown request"
                    )
                if row["request_sha256"] != request_sha256:
                    raise IdempotencyConflict(request_id)
                if row["status"] == "COMPLETED":
                    if row["response_json"] != payload:
                        raise IdempotencyConflict(request_id)
                    self.connection.commit()
                    return
                self.connection.execute(
                    "UPDATE requests SET status='COMPLETED', "
                    "response_json=?, completed_at_ms=? "
                    "WHERE request_id=?",
                    (payload, now, request_id),
                )
                self.connection.commit()
            except (IdempotencyConflict, StateStoreError):
                self.connection.rollback()
                raise
            except sqlite3.Error as exc:
                self.connection.rollback()
                raise StateStoreError(
                    "mutation request ledger unavailable"
                ) from exc
            except Exception:
                self.connection.rollback()
                raise
