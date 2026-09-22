from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core import ClaimMode
from .executor import LocalExecutor, ProcessExecutionDisabled


class ProcessHandleNotFound(LookupError):
    code = "PROCESS_HANDLE_NOT_FOUND"


class ProcessHandleOwnerMismatch(PermissionError):
    code = "PROCESS_HANDLE_OWNER_MISMATCH"


class ProcessOutputOffsetUnavailable(RuntimeError):
    code = "PROCESS_OUTPUT_OFFSET_UNAVAILABLE"


class ProcessInputUnavailable(RuntimeError):
    code = "PROCESS_INPUT_UNAVAILABLE"


@dataclass
class _ManagedProcess:
    handle: str
    owner_lane_id: str
    owner_fencing_token: int
    process: subprocess.Popen[bytes]
    cwd: Path
    argv: tuple[str, ...]
    started_at_ns: int
    deadline_ns: int
    stdout: bytearray
    stderr: bytearray
    stdin_lock: threading.Lock = field(default_factory=threading.Lock)
    stdout_total_bytes: int = 0
    stderr_total_bytes: int = 0
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    watchdog_terminated: bool = False


class RdcSurface:
    """Bounded filesystem discovery/search and VeraPort-managed process lifecycle."""

    MAX_LIST_ENTRIES = 1000
    MAX_SEARCH_RESULTS = 500
    MAX_SEARCH_ENTRIES = 50_000
    MAX_SEARCH_DEPTH = 32
    MAX_PROCESS_OUTPUT_READ_BYTES = 65_536
    MAX_PROCESS_RUNTIME_S = 3_600.0

    def __init__(self, executor: LocalExecutor) -> None:
        self.executor = executor
        self._processes: dict[str, _ManagedProcess] = {}
        self._process_lock = threading.RLock()

    @staticmethod
    def _entry_json(path: Path, stat_result: os.stat_result, *, is_symlink: bool) -> dict[str, Any]:
        if is_symlink:
            kind = "symlink"
        elif os.path.isdir(path):
            kind = "directory"
        elif os.path.isfile(path):
            kind = "file"
        else:
            kind = "other"
        return {
            "name": path.name,
            "path": path.as_posix(),
            "type": kind,
            "is_symlink": is_symlink,
            "size_bytes": stat_result.st_size,
            "mtime_ns": stat_result.st_mtime_ns,
        }

    def _authorize_fs(self, lane_id: str, fencing_token: int, path: Path) -> None:
        self.executor.registry.authorize(
            lane_id,
            fencing_token,
            "fs.read",
            resource_key=self.executor._fs_resource(path),
            resource_mode=ClaimMode.READ,
        )

    async def stat(self, *, lane_id: str, fencing_token: int, path: str) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._stat_sync, lane_id, fencing_token, path
        )

    def _stat_sync(self, lane_id: str, fencing_token: int, value: str) -> dict[str, Any]:
        raw = Path(value).expanduser()
        if not raw.is_absolute():
            raw = Path.cwd() / raw
        raw = Path(os.path.abspath(raw))

        if raw.is_symlink():
            parent = self.executor._resolve_allowed(raw.parent)
            candidate = parent / raw.name
            self._authorize_fs(lane_id, fencing_token, parent)
            stat_result = os.lstat(candidate)
            return self._entry_json(candidate, stat_result, is_symlink=True)

        resolved = self.executor._resolve_allowed(raw)
        self._authorize_fs(lane_id, fencing_token, resolved)
        stat_result = os.lstat(resolved)
        return self._entry_json(
            resolved,
            stat_result,
            is_symlink=os.path.islink(resolved),
        )

    async def list_dir(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        path: str,
        offset: int = 0,
        max_entries: int = 200,
    ) -> dict[str, Any]:
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if type(max_entries) is not int or not 1 <= max_entries <= self.MAX_LIST_ENTRIES:
            raise ValueError(
                f"max_entries must be in 1..{self.MAX_LIST_ENTRIES}"
            )
        return await asyncio.to_thread(
            self._list_dir_sync,
            lane_id,
            fencing_token,
            path,
            offset,
            max_entries,
        )

    def _list_dir_sync(
        self,
        lane_id: str,
        fencing_token: int,
        value: str,
        offset: int,
        max_entries: int,
    ) -> dict[str, Any]:
        directory = self.executor._resolve_allowed(value)
        self._authorize_fs(lane_id, fencing_token, directory)
        if not directory.is_dir():
            raise NotADirectoryError(str(directory))

        with os.scandir(directory) as scan:
            entries = sorted(list(scan), key=lambda item: (item.name.casefold(), item.name))
        total = len(entries)
        selected = entries[offset : offset + max_entries]
        result = []
        for entry in selected:
            stat_result = entry.stat(follow_symlinks=False)
            child = Path(entry.path)
            result.append(
                self._entry_json(
                    child,
                    stat_result,
                    is_symlink=entry.is_symlink(),
                )
            )
        next_offset = offset + len(selected)
        return {
            "path": directory.as_posix(),
            "entries": result,
            "offset": offset,
            "next_offset": next_offset if next_offset < total else None,
            "total_entries": total,
            "truncated": next_offset < total,
        }

    async def search(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        root: str,
        query: str,
        offset: int = 0,
        max_results: int = 100,
        max_entries: int = 10_000,
        max_depth: int = 12,
        case_sensitive: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not query:
            raise ValueError("query must be a non-empty string")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if type(max_results) is not int or not 1 <= max_results <= self.MAX_SEARCH_RESULTS:
            raise ValueError(
                f"max_results must be in 1..{self.MAX_SEARCH_RESULTS}"
            )
        if type(max_entries) is not int or not 1 <= max_entries <= self.MAX_SEARCH_ENTRIES:
            raise ValueError(
                f"max_entries must be in 1..{self.MAX_SEARCH_ENTRIES}"
            )
        if type(max_depth) is not int or not 0 <= max_depth <= self.MAX_SEARCH_DEPTH:
            raise ValueError(f"max_depth must be in 0..{self.MAX_SEARCH_DEPTH}")
        if type(case_sensitive) is not bool:
            raise ValueError("case_sensitive must be bool")
        return await asyncio.to_thread(
            self._search_sync,
            lane_id,
            fencing_token,
            root,
            query,
            offset,
            max_results,
            max_entries,
            max_depth,
            case_sensitive,
        )

    def _search_sync(
        self,
        lane_id: str,
        fencing_token: int,
        root_value: str,
        query: str,
        offset: int,
        max_results: int,
        max_entries: int,
        max_depth: int,
        case_sensitive: bool,
    ) -> dict[str, Any]:
        root = self.executor._resolve_allowed(root_value)
        self._authorize_fs(lane_id, fencing_token, root)
        if not root.is_dir():
            raise NotADirectoryError(str(root))

        needle = query if case_sensitive else query.casefold()
        scanned = 0
        matched = 0
        results: list[dict[str, Any]] = []
        more_matches = False
        scan_limit_hit = False
        stack: list[tuple[Path, int]] = [(root, 0)]

        while stack:
            directory, depth = stack.pop()
            try:
                with os.scandir(directory) as scan:
                    entries = sorted(
                        list(scan),
                        key=lambda item: (item.name.casefold(), item.name),
                        reverse=True,
                    )
            except OSError:
                continue

            for entry in reversed(entries):
                if scanned >= max_entries:
                    scan_limit_hit = True
                    stack.clear()
                    break
                scanned += 1
                child = Path(entry.path)
                relative = child.relative_to(root).as_posix()
                haystack = relative if case_sensitive else relative.casefold()
                if needle in haystack:
                    if matched >= offset:
                        if len(results) < max_results:
                            stat_result = entry.stat(follow_symlinks=False)
                            results.append(
                                self._entry_json(
                                    child,
                                    stat_result,
                                    is_symlink=entry.is_symlink(),
                                )
                            )
                            results[-1]["relative_path"] = relative
                        else:
                            more_matches = True
                            stack.clear()
                            break
                    matched += 1

                if (
                    entry.is_dir(follow_symlinks=False)
                    and not entry.is_symlink()
                    and depth < max_depth
                ):
                    stack.append((child, depth + 1))

            if more_matches or scan_limit_hit:
                break

        truncated = more_matches or scan_limit_hit
        return {
            "root": root.as_posix(),
            "query": query,
            "matches": results,
            "offset": offset,
            "next_offset": (
                offset + len(results) if truncated and results else None
            ),
            "scanned_entries": scanned,
            "max_entries": max_entries,
            "max_depth": max_depth,
            "scan_limit_hit": scan_limit_hit,
            "truncated": truncated,
        }

    def _require_process_policy(self) -> None:
        if not self.executor.allow_process_exec:
            raise ProcessExecutionDisabled(
                "process operations are disabled by local workstation policy"
            )

    def _authorize_cwd(
        self,
        lane_id: str,
        fencing_token: int,
        capability: str,
        cwd: Path,
        mode: ClaimMode,
    ) -> None:
        self.executor.registry.authorize(
            lane_id,
            fencing_token,
            capability,
            resource_key=self.executor._cwd_resource(cwd),
            resource_mode=mode,
        )

    async def process_start(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        argv: list[str],
        cwd: str,
        max_runtime_s: float = 900.0,
    ) -> dict[str, Any]:
        self._require_process_policy()
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("argv must be a non-empty list of non-empty strings")
        max_runtime_s = float(max_runtime_s)
        if not 0.1 <= max_runtime_s <= self.MAX_PROCESS_RUNTIME_S:
            raise ValueError(
                f"max_runtime_s must be in 0.1..{self.MAX_PROCESS_RUNTIME_S}"
            )
        resolved_cwd = self.executor._resolve_allowed(cwd)
        self._authorize_cwd(
            lane_id,
            fencing_token,
            "process.exec",
            resolved_cwd,
            ClaimMode.WRITE,
        )
        return await asyncio.to_thread(
            self._start_process_sync,
            lane_id,
            fencing_token,
            tuple(argv),
            resolved_cwd,
            max_runtime_s,
        )

    def _start_process_sync(
        self,
        lane_id: str,
        fencing_token: int,
        argv: tuple[str, ...],
        cwd: Path,
        max_runtime_s: float,
    ) -> dict[str, Any]:
        process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            shell=False,
        )
        now_ns = time.time_ns()
        item = _ManagedProcess(
            handle="proc:" + uuid.uuid4().hex,
            owner_lane_id=lane_id,
            owner_fencing_token=fencing_token,
            process=process,
            cwd=cwd,
            argv=argv,
            started_at_ns=now_ns,
            deadline_ns=now_ns + int(max_runtime_s * 1_000_000_000),
            stdout=bytearray(),
            stderr=bytearray(),
        )
        with self._process_lock:
            self._processes[item.handle] = item
        assert process.stdout is not None and process.stderr is not None
        threading.Thread(
            target=self._drain_pipe,
            args=(item, process.stdout, True),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._drain_pipe,
            args=(item, process.stderr, False),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._watchdog,
            args=(item,),
            daemon=True,
        ).start()
        return self._status_json(item)

    def _drain_pipe(self, item: _ManagedProcess, pipe, stdout: bool) -> None:
        target = item.stdout if stdout else item.stderr
        while True:
            try:
                chunk = os.read(pipe.fileno(), 4096)
            except OSError:
                return
            if not chunk:
                return
            with self._process_lock:
                if stdout:
                    item.stdout_total_bytes += len(chunk)
                else:
                    item.stderr_total_bytes += len(chunk)
                remaining = self.executor.max_output_bytes - len(target)
                if remaining > 0:
                    target.extend(chunk[:remaining])
                if len(chunk) > max(0, remaining):
                    if stdout:
                        item.stdout_truncated = True
                    else:
                        item.stderr_truncated = True

    def _watchdog(self, item: _ManagedProcess) -> None:
        while item.process.poll() is None:
            try:
                self._authorize_cwd(
                    item.owner_lane_id,
                    item.owner_fencing_token,
                    "process.exec",
                    item.cwd,
                    ClaimMode.WRITE,
                )
            except Exception:
                with self._process_lock:
                    item.watchdog_terminated = True
                self._terminate_sync(item, 0.5)
                return
            remaining_ns = item.deadline_ns - time.time_ns()
            if remaining_ns <= 0:
                with self._process_lock:
                    item.watchdog_terminated = True
                self._terminate_sync(item, 0.5)
                return
            time.sleep(min(0.25, remaining_ns / 1_000_000_000))

    def _owned_process(
        self,
        lane_id: str,
        fencing_token: int,
        process_handle: str,
    ) -> _ManagedProcess:
        with self._process_lock:
            item = self._processes.get(process_handle)
        if item is None:
            raise ProcessHandleNotFound(process_handle)
        if (
            item.owner_lane_id != lane_id
            or item.owner_fencing_token != fencing_token
        ):
            raise ProcessHandleOwnerMismatch(process_handle)
        return item

    @staticmethod
    def _status_json(item: _ManagedProcess) -> dict[str, Any]:
        returncode = item.process.poll()
        return {
            "process_handle": item.handle,
            "pid": item.process.pid,
            "running": returncode is None,
            "returncode": returncode,
            "cwd": item.cwd.as_posix(),
            "started_at_ns": item.started_at_ns,
            "deadline_ns": item.deadline_ns,
            "watchdog_terminated": item.watchdog_terminated,
        }

    async def process_list(
        self,
        *,
        lane_id: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        self._require_process_policy()
        with self._process_lock:
            items = tuple(self._processes.values())
        visible = []
        for item in items:
            if (
                item.owner_lane_id != lane_id
                or item.owner_fencing_token != fencing_token
            ):
                continue
            self._authorize_cwd(
                lane_id,
                fencing_token,
                "process.inspect",
                item.cwd,
                ClaimMode.READ,
            )
            visible.append(self._status_json(item))
        return {"processes": visible}

    async def process_status(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        process_handle: str,
    ) -> dict[str, Any]:
        self._require_process_policy()
        item = self._owned_process(lane_id, fencing_token, process_handle)
        self._authorize_cwd(
            lane_id,
            fencing_token,
            "process.inspect",
            item.cwd,
            ClaimMode.READ,
        )
        return self._status_json(item)

    async def process_output(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        process_handle: str,
        stdout_offset: int = 0,
        stderr_offset: int = 0,
        max_bytes: int = 16_384,
    ) -> dict[str, Any]:
        self._require_process_policy()
        for name, value in {
            "stdout_offset": stdout_offset,
            "stderr_offset": stderr_offset,
        }.items():
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if (
            type(max_bytes) is not int
            or not 1 <= max_bytes <= self.MAX_PROCESS_OUTPUT_READ_BYTES
        ):
            raise ValueError(
                f"max_bytes must be in 1..{self.MAX_PROCESS_OUTPUT_READ_BYTES}"
            )

        item = self._owned_process(lane_id, fencing_token, process_handle)
        self._authorize_cwd(
            lane_id,
            fencing_token,
            "process.inspect",
            item.cwd,
            ClaimMode.READ,
        )
        with self._process_lock:
            stdout = bytes(item.stdout)
            stderr = bytes(item.stderr)
            stdout_total = item.stdout_total_bytes
            stderr_total = item.stderr_total_bytes
            stdout_truncated = item.stdout_truncated
            stderr_truncated = item.stderr_truncated

        if stdout_offset > len(stdout) and stdout_truncated:
            raise ProcessOutputOffsetUnavailable("stdout offset was not retained")
        if stderr_offset > len(stderr) and stderr_truncated:
            raise ProcessOutputOffsetUnavailable("stderr offset was not retained")

        stdout_chunk = stdout[stdout_offset : stdout_offset + max_bytes]
        stderr_chunk = stderr[stderr_offset : stderr_offset + max_bytes]
        return {
            **self._status_json(item),
            "stdout": stdout_chunk.decode("utf-8", errors="replace"),
            "stderr": stderr_chunk.decode("utf-8", errors="replace"),
            "stdout_offset": stdout_offset,
            "stderr_offset": stderr_offset,
            "stdout_next_offset": stdout_offset + len(stdout_chunk),
            "stderr_next_offset": stderr_offset + len(stderr_chunk),
            "stdout_retained_bytes": len(stdout),
            "stderr_retained_bytes": len(stderr),
            "stdout_total_bytes": stdout_total,
            "stderr_total_bytes": stderr_total,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }

    async def process_input(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        process_handle: str,
        input_text: str,
        append_newline: bool = True,
    ) -> dict[str, Any]:
        self._require_process_policy()
        if not isinstance(input_text, str):
            raise ValueError("input_text must be string")
        if type(append_newline) is not bool:
            raise ValueError("append_newline must be bool")
        payload = input_text.encode("utf-8")
        if append_newline:
            payload += b"\n"
        if len(payload) > 65_536:
            raise ValueError("process input exceeds 65536-byte limit")

        item = self._owned_process(lane_id, fencing_token, process_handle)
        self._authorize_cwd(
            lane_id,
            fencing_token,
            "process.interact",
            item.cwd,
            ClaimMode.WRITE,
        )
        await asyncio.to_thread(self._write_process_input, item, payload)
        return {
            **self._status_json(item),
            "bytes_written": len(payload),
        }

    @staticmethod
    def _write_process_input(item: _ManagedProcess, payload: bytes) -> None:
        if item.process.poll() is not None or item.process.stdin is None:
            raise ProcessInputUnavailable("process stdin is unavailable")
        with item.stdin_lock:
            try:
                item.process.stdin.write(payload)
                item.process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise ProcessInputUnavailable(
                    "process stdin is unavailable"
                ) from exc

    async def process_terminate(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        process_handle: str,
        grace_s: float = 2.0,
    ) -> dict[str, Any]:
        self._require_process_policy()
        grace_s = float(grace_s)
        if not 0 <= grace_s <= 30:
            raise ValueError("grace_s must be in 0..30")
        item = self._owned_process(lane_id, fencing_token, process_handle)
        self._authorize_cwd(
            lane_id,
            fencing_token,
            "process.control",
            item.cwd,
            ClaimMode.WRITE,
        )
        await asyncio.to_thread(self._terminate_sync, item, grace_s)
        return self._status_json(item)

    async def close_lane_processes(
        self,
        lane_id: str,
        fencing_token: int,
    ) -> list[str]:
        with self._process_lock:
            owned = [
                item
                for item in self._processes.values()
                if (
                    item.owner_lane_id == lane_id
                    and item.owner_fencing_token == fencing_token
                )
            ]
        terminated = []
        for item in owned:
            await asyncio.to_thread(self._terminate_sync, item, 0.5)
            terminated.append(item.handle)
        return terminated

    @staticmethod
    def _terminate_sync(item: _ManagedProcess, grace_s: float) -> None:
        if item.process.poll() is not None:
            return
        item.process.terminate()
        try:
            item.process.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            item.process.kill()
            item.process.wait(timeout=5)


def surface_for(executor: LocalExecutor) -> RdcSurface:
    surface = getattr(executor, "_rdc_surface", None)
    if surface is None:
        surface = RdcSurface(executor)
        setattr(executor, "_rdc_surface", surface)
    return surface
