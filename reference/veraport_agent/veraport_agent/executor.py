from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .core import ClaimMode, LaneRegistry


class PathOutsideRoots(PermissionError):
    pass


class ProcessExecutionDisabled(PermissionError):
    pass


class FileReadLimitExceeded(PermissionError):
    code = "FILE_READ_LIMIT_EXCEEDED"


@dataclass(frozen=True)
class FileChunk:
    offset: int
    data: bytes
    size: int


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool


class LocalExecutor:
    def __init__(
        self,
        registry: LaneRegistry,
        *,
        allowed_roots: tuple[Path, ...],
        max_output_bytes: int = 1_048_576,
        max_read_bytes: int = 1_048_576,
        allow_process_exec: bool = False,
    ) -> None:
        if not allowed_roots:
            raise ValueError("at least one allowed root is required")
        if max_output_bytes < 1:
            raise ValueError("max_output_bytes must be positive")
        if max_read_bytes < 1:
            raise ValueError("max_read_bytes must be positive")
        self.registry = registry
        self.allowed_roots = tuple(root.expanduser().resolve() for root in allowed_roots)
        self.max_output_bytes = max_output_bytes
        self.max_read_bytes = max_read_bytes
        self.allow_process_exec = allow_process_exec

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

    async def read_text(self, *, lane_id: str, fencing_token: int, path: str, encoding: str = "utf-8") -> str:
        resolved = self._resolve_allowed(path)
        self.registry.authorize(
            lane_id,
            fencing_token,
            "fs.read",
            resource_key=self._fs_resource(resolved),
            resource_mode=ClaimMode.READ,
        )
        return await asyncio.to_thread(self._read_text_bounded, resolved, encoding)

    def _read_text_bounded(self, path: Path, encoding: str) -> str:
        with path.open("rb") as handle:
            payload = handle.read(self.max_read_bytes + 1)
        if len(payload) > self.max_read_bytes:
            raise FileReadLimitExceeded(
                f"file exceeds max_read_bytes={self.max_read_bytes}"
            )
        return payload.decode(encoding)

    async def read_bytes_chunk(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        path: str,
        offset: int,
        max_bytes: int,
    ) -> FileChunk:
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        if max_bytes > self.max_read_bytes:
            raise FileReadLimitExceeded(
                f"max_bytes exceeds max_read_bytes={self.max_read_bytes}"
            )
        resolved = self._resolve_allowed(path)
        self.registry.authorize(
            lane_id,
            fencing_token,
            "fs.read",
            resource_key=self._fs_resource(resolved),
            resource_mode=ClaimMode.READ,
        )
        return await asyncio.to_thread(
            self._read_bytes_chunk,
            resolved,
            offset,
            max_bytes,
        )

    @staticmethod
    def _read_bytes_chunk(
        path: Path,
        offset: int,
        max_bytes: int,
    ) -> FileChunk:
        with path.open("rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if offset > size:
                raise ValueError(
                    f"offset {offset} exceeds file size {size}"
                )
            handle.seek(offset)
            data = handle.read(max_bytes)
        return FileChunk(offset=offset, data=data, size=size)

    async def write_text(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        path: str,
        content: str,
        encoding: str = "utf-8",
    ) -> None:
        resolved = self._resolve_allowed(path)
        self.registry.authorize(
            lane_id,
            fencing_token,
            "fs.write",
            resource_key=self._fs_resource(resolved),
            resource_mode=ClaimMode.WRITE,
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

    async def run_process(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        argv: list[str],
        cwd: str,
        timeout_s: float = 60.0,
    ) -> ProcessResult:
        if not self.allow_process_exec:
            raise ProcessExecutionDisabled("process.exec is disabled by local workstation policy")
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("argv must be a non-empty list of non-empty strings")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        resolved_cwd = self._resolve_allowed(cwd)
        self.registry.authorize(
            lane_id,
            fencing_token,
            "process.exec",
            resource_key=self._cwd_resource(resolved_cwd),
            resource_mode=ClaimMode.WRITE,
        )
        if os.name == "nt":
            return await asyncio.to_thread(
                self._run_process_windows,
                argv,
                resolved_cwd,
                timeout_s,
            )

        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=resolved_cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        return self._result(process.returncode, stdout_b, stderr_b)

    def _run_process_windows(
        self,
        argv: list[str],
        cwd: Path,
        timeout_s: float,
    ) -> ProcessResult:
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_s,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"process exceeded timeout {timeout_s}s") from exc
        return self._result(completed.returncode, completed.stdout, completed.stderr)

    def _result(self, returncode: int, stdout_b: bytes, stderr_b: bytes) -> ProcessResult:
        stdout, stdout_truncated = self._decode_limited(stdout_b)
        stderr, stderr_truncated = self._decode_limited(stderr_b)
        return ProcessResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

    def _decode_limited(self, value: bytes) -> tuple[str, bool]:
        truncated = len(value) > self.max_output_bytes
        return value[: self.max_output_bytes].decode("utf-8", errors="replace"), truncated
