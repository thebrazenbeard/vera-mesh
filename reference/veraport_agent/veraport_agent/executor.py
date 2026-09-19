from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .core import ClaimMode, LaneRegistry


class PathOutsideRoots(PermissionError):
    pass


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
    ) -> None:
        if not allowed_roots:
            raise ValueError("at least one allowed root is required")
        if max_output_bytes < 1:
            raise ValueError("max_output_bytes must be positive")
        self.registry = registry
        self.allowed_roots = tuple(root.expanduser().resolve() for root in allowed_roots)
        self.max_output_bytes = max_output_bytes

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
        return await asyncio.to_thread(resolved.read_text, encoding=encoding)

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
        stdout, stdout_truncated = self._decode_limited(stdout_b)
        stderr, stderr_truncated = self._decode_limited(stderr_b)
        return ProcessResult(
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

    def _decode_limited(self, value: bytes) -> tuple[str, bool]:
        truncated = len(value) > self.max_output_bytes
        return value[: self.max_output_bytes].decode("utf-8", errors="replace"), truncated
