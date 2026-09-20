from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .core import ClaimMode, LaneRegistry


class PathOutsideRoots(PermissionError):
    pass


class ProcessExecutionDisabled(PermissionError):
    pass


class ProcessHandleNotFound(LookupError):
    code = "PROCESS_HANDLE_NOT_FOUND"


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool


@dataclass
class _ManagedProcess:
    handle: str
    process: subprocess.Popen[bytes]
    cwd: Path
    started_at_ns: int
    stdout: bytearray
    stderr: bytearray
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class LocalExecutor:
    def __init__(
        self,
        registry: LaneRegistry,
        *,
        allowed_roots: tuple[Path, ...],
        max_output_bytes: int = 1_048_576,
        allow_process_exec: bool = False,
    ) -> None:
        if not allowed_roots:
            raise ValueError("at least one allowed root is required")
        if max_output_bytes < 1:
            raise ValueError("max_output_bytes must be positive")
        self.registry = registry
        self.allowed_roots = tuple(root.expanduser().resolve() for root in allowed_roots)
        self.max_output_bytes = max_output_bytes
        self.allow_process_exec = allow_process_exec
        self._processes: dict[str, _ManagedProcess] = {}
        self._process_lock = threading.RLock()

    def _resolve_allowed(self, value: str | Path) -> Path:
        path = Path(value).expanduser().resolve()
        for root in self.allowed_roots:
            try:
                path.relative_to(root)
                return path
            except ValueError:
                continue
        raise PathOutsideRoots(str(path))

    @staticmethod
    def _fs_resource(path: Path) -> str:
        return "fs:" + path.as_posix()

    @staticmethod
    def _cwd_resource(path: Path) -> str:
        return "cwd:" + path.as_posix()

    async def read_text(self, *, lane_id: str, fencing_token: int, path: str,
                        encoding: str = "utf-8") -> str:
        resolved = self._resolve_allowed(path)
        self.registry.authorize(
            lane_id, fencing_token, "fs.read",
            resource_key=self._fs_resource(resolved), resource_mode=ClaimMode.READ,
        )
        return await asyncio.to_thread(resolved.read_text, encoding=encoding)

    async def write_text(self, *, lane_id: str, fencing_token: int, path: str,
                         content: str, encoding: str = "utf-8") -> None:
        resolved = self._resolve_allowed(path)
        self.registry.authorize(
            lane_id, fencing_token, "fs.write",
            resource_key=self._fs_resource(resolved), resource_mode=ClaimMode.WRITE,
        )
        await asyncio.to_thread(self._atomic_write, resolved, content, encoding)

    @staticmethod
    def _atomic_write(path: Path, content: str, encoding: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise

    def _require_process_policy(self) -> None:
        if not self.allow_process_exec:
            raise ProcessExecutionDisabled(
                "process operations are disabled by local workstation policy"
            )

    def _authorize_cwd(self, lane_id: str, fencing_token: int, capability: str,
                       cwd: Path, mode: ClaimMode) -> None:
        self.registry.authorize(
            lane_id, fencing_token, capability,
            resource_key=self._cwd_resource(cwd), resource_mode=mode,
        )

    async def run_process(self, *, lane_id: str, fencing_token: int, argv: list[str],
                          cwd: str, timeout_s: float = 60.0) -> ProcessResult:
        self._require_process_policy()
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("argv must be a non-empty list of non-empty strings")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        resolved_cwd = self._resolve_allowed(cwd)
        self._authorize_cwd(lane_id, fencing_token, "process.exec", resolved_cwd, ClaimMode.WRITE)
        return await asyncio.to_thread(self._run_process_sync, argv, resolved_cwd, timeout_s)

    def _run_process_sync(self, argv: list[str], cwd: Path, timeout_s: float) -> ProcessResult:
        try:
            completed = subprocess.run(
                argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout_s, shell=False, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"process exceeded timeout {timeout_s}s") from exc
        return self._result(completed.returncode, completed.stdout, completed.stderr)

    async def start_process(self, *, lane_id: str, fencing_token: int,
                            argv: list[str], cwd: str) -> dict[str, object]:
        self._require_process_policy()
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("argv must be a non-empty list of non-empty strings")
        resolved_cwd = self._resolve_allowed(cwd)
        self._authorize_cwd(lane_id, fencing_token, "process.exec", resolved_cwd, ClaimMode.WRITE)
        managed = await asyncio.to_thread(self._start_managed_sync, argv, resolved_cwd)
        return self._status_json(managed)

    def _start_managed_sync(self, argv: list[str], cwd: Path) -> _ManagedProcess:
        process = subprocess.Popen(
            argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False,
        )
        managed = _ManagedProcess(
            handle="proc:" + uuid.uuid4().hex,
            process=process,
            cwd=cwd,
            started_at_ns=time.time_ns(),
            stdout=bytearray(),
            stderr=bytearray(),
        )
        with self._process_lock:
            self._processes[managed.handle] = managed
        assert process.stdout is not None and process.stderr is not None
        threading.Thread(
            target=self._drain_pipe, args=(managed, process.stdout, True), daemon=True
        ).start()
        threading.Thread(
            target=self._drain_pipe, args=(managed, process.stderr, False), daemon=True
        ).start()
        return managed

    def _drain_pipe(self, managed: _ManagedProcess, pipe, stdout: bool) -> None:
        target = managed.stdout if stdout else managed.stderr
        while True:
            chunk = pipe.read(4096)
            if not chunk:
                return
            with self._process_lock:
                remaining = self.max_output_bytes - len(target)
                if remaining > 0:
                    target.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    if stdout:
                        managed.stdout_truncated = True
                    else:
                        managed.stderr_truncated = True

    def _get_process(self, handle: str) -> _ManagedProcess:
        with self._process_lock:
            item = self._processes.get(handle)
        if item is None:
            raise ProcessHandleNotFound(handle)
        return item

    def _status_json(self, item: _ManagedProcess) -> dict[str, object]:
        returncode = item.process.poll()
        return {
            "process_handle": item.handle,
            "pid": item.process.pid,
            "started_at_ns": item.started_at_ns,
            "running": returncode is None,
            "returncode": returncode,
            "cwd": item.cwd.as_posix(),
        }

    async def process_status(self, *, lane_id: str, fencing_token: int,
                             process_handle: str) -> dict[str, object]:
        self._require_process_policy()
        item = self._get_process(process_handle)
        self._authorize_cwd(lane_id, fencing_token, "process.inspect", item.cwd, ClaimMode.READ)
        return self._status_json(item)

    async def list_processes(self, *, lane_id: str, fencing_token: int) -> list[dict[str, object]]:
        self._require_process_policy()
        result: list[dict[str, object]] = []
        with self._process_lock:
            items = tuple(self._processes.values())
        for item in items:
            try:
                self._authorize_cwd(
                    lane_id, fencing_token, "process.inspect", item.cwd, ClaimMode.READ
                )
            except Exception:
                continue
            result.append(self._status_json(item))
        return result

    async def process_output(self, *, lane_id: str, fencing_token: int,
                             process_handle: str) -> dict[str, object]:
        self._require_process_policy()
        item = self._get_process(process_handle)
        self._authorize_cwd(lane_id, fencing_token, "process.inspect", item.cwd, ClaimMode.READ)
        with self._process_lock:
            stdout = bytes(item.stdout)
            stderr = bytes(item.stderr)
            out_trunc = item.stdout_truncated
            err_trunc = item.stderr_truncated
        return {
            **self._status_json(item),
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "stdout_truncated": out_trunc,
            "stderr_truncated": err_trunc,
        }

    async def terminate_process(self, *, lane_id: str, fencing_token: int,
                                process_handle: str, grace_s: float = 2.0) -> dict[str, object]:
        self._require_process_policy()
        if grace_s < 0 or grace_s > 30:
            raise ValueError("grace_s must be in 0..30")
        item = self._get_process(process_handle)
        self._authorize_cwd(lane_id, fencing_token, "process.control", item.cwd, ClaimMode.WRITE)
        await asyncio.to_thread(self._terminate_sync, item, grace_s)
        return self._status_json(item)

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

    def _result(self, returncode: int, stdout_b: bytes, stderr_b: bytes) -> ProcessResult:
        stdout, stdout_truncated = self._decode_limited(stdout_b)
        stderr, stderr_truncated = self._decode_limited(stderr_b)
        return ProcessResult(
            returncode=returncode, stdout=stdout, stderr=stderr,
            stdout_truncated=stdout_truncated, stderr_truncated=stderr_truncated,
        )

    def _decode_limited(self, value: bytes) -> tuple[str, bool]:
        truncated = len(value) > self.max_output_bytes
        return value[: self.max_output_bytes].decode("utf-8", errors="replace"), truncated
