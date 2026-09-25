from __future__ import annotations

import hashlib
import ntpath
import posixpath
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from .controller_runtime import ControllerRuntime


class PublicGatewayError(RuntimeError):
    code = "PUBLIC_GATEWAY_ERROR"


class PublicGatewayRemoteError(PublicGatewayError):
    code = "PUBLIC_GATEWAY_REMOTE_ERROR"

    def __init__(self, remote_code: str, message: str) -> None:
        self.remote_code = remote_code
        super().__init__(f"{remote_code}: {message}")


class PublicProcessNotFound(LookupError):
    code = "PUBLIC_PROCESS_NOT_FOUND"


@dataclass(frozen=True)
class ProcessLease:
    public_handle: str
    internal_handle: str
    lane_id: str
    fencing_token: int
    cwd: str


def canonical_remote_path(value: str) -> str:
    """Canonicalize an absolute workstation path without using host OS semantics."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("path must be a non-empty absolute string")
    raw = value.strip()
    is_windows = (
        len(raw) >= 3
        and raw[1] == ":"
        and raw[2] in {"\\", "/"}
    ) or raw.startswith("\\\\")
    if is_windows:
        normalized = ntpath.normpath(raw).replace("\\", "/")
        if not ntpath.isabs(raw):
            raise ValueError("path must be absolute")
        return normalized
    normalized = posixpath.normpath(raw)
    if not posixpath.isabs(normalized):
        raise ValueError("path must be absolute")
    return normalized


class PublicWorkstationFacade:
    """Ergonomic actor-bound facade over VeraPort's lane/fence protocol.

    Public callers never choose lane IDs, fencing tokens, or raw workstation
    process handles. Those remain internal authority state.
    """

    PROCESS_LEASE_GRACE_S = 300.0

    def __init__(
        self,
        runtime: ControllerRuntime,
        *,
        actor_id: str,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("actor_id is required")
        self.runtime = runtime
        self.actor_id = actor_id
        self._actor_tag = hashlib.sha256(
            actor_id.encode("utf-8")
        ).hexdigest()[:16]
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._processes: dict[str, ProcessLease] = {}

    def _new_id(self, prefix: str) -> str:
        value = self._id_factory()
        if not isinstance(value, str) or not value:
            raise ValueError("id_factory must return a non-empty string")
        return f"{prefix}:{self._actor_tag}:{value}"

    @staticmethod
    def _fs_claim(path: str, mode: str) -> dict[str, str]:
        return {"key": "fs:" + canonical_remote_path(path), "mode": mode}

    @staticmethod
    def _cwd_claim(path: str, mode: str) -> dict[str, str]:
        return {"key": "cwd:" + canonical_remote_path(path), "mode": mode}

    @staticmethod
    def _unwrap(response: dict[str, Any]) -> dict[str, Any]:
        if response.get("ok") is not True:
            error = response.get("error")
            if not isinstance(error, dict):
                raise PublicGatewayRemoteError(
                    "REMOTE_ERROR", "remote operation failed"
                )
            raise PublicGatewayRemoteError(
                str(error.get("code", "REMOTE_ERROR")),
                str(error.get("message", "remote operation failed")),
            )
        result = response.get("result")
        if not isinstance(result, dict):
            raise PublicGatewayError("remote result must be an object")
        return dict(result)

    async def _open_lane(
        self,
        *,
        capabilities: list[str],
        claims: list[dict[str, str]],
        ttl_s: float = 300.0,
        task: str,
    ) -> tuple[str, int]:
        lane_id = self._new_id("public-lane")
        response = await self.runtime.open_lane(
            lane_id=lane_id,
            task_id=self._new_id(task),
            capabilities=capabilities,
            claims=claims,
            ttl_s=ttl_s,
        )
        result = self._unwrap(response)
        fence = result.get("fencing_token")
        if type(fence) is not int or fence < 1:
            raise PublicGatewayError("lane.open returned no fencing token")
        return lane_id, fence

    async def _close_lane_evidence(
        self,
        lane_id: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        try:
            response = await self.runtime.close_lane(
                lane_id=lane_id,
                fencing_token=fencing_token,
            )
            if response.get("ok") is True:
                return {"closed": True}
            return {
                "closed": False,
                "error": response.get("error"),
            }
        except Exception as exc:
            return {
                "closed": False,
                "error": {
                    "code": getattr(exc, "code", exc.__class__.__name__),
                    "message": str(exc),
                },
            }

    @staticmethod
    def _attach_cleanup(
        result: dict[str, Any],
        cleanup: dict[str, Any],
    ) -> dict[str, Any]:
        if cleanup.get("closed") is True:
            return result
        enriched = dict(result)
        enriched["_veramesh_cleanup"] = cleanup
        return enriched

    async def _ephemeral(
        self,
        *,
        operation: str,
        capabilities: list[str],
        claims: list[dict[str, str]],
        body: dict[str, Any],
        task: str,
    ) -> dict[str, Any]:
        lane_id, fence = await self._open_lane(
            capabilities=capabilities,
            claims=claims,
            task=task,
        )
        succeeded = False
        result: dict[str, Any] | None = None
        try:
            if operation == "fs.read_text":
                response = await self.runtime.read_text(
                    lane_id=lane_id,
                    fencing_token=fence,
                    **body,
                )
            elif operation == "fs.read_bytes":
                response = await self.runtime.read_bytes(
                    lane_id=lane_id,
                    fencing_token=fence,
                    **body,
                )
            elif operation in {
                "fs.stat",
                "fs.list_dir",
                "fs.search",
                "fs.search_content",
            }:
                response = await self.runtime.read_operation(
                    operation,
                    lane_id=lane_id,
                    fencing_token=fence,
                    **body,
                )
            elif operation == "fs.write_text":
                response = await self.runtime.write_text(
                    lane_id=lane_id,
                    fencing_token=fence,
                    **body,
                )
            else:
                await self.runtime.ensure_started()
                response = await self.runtime.gateway.call_operation(
                    operation,
                    lane_id=lane_id,
                    fencing_token=fence,
                    **body,
                )
            result = self._unwrap(response)
            succeeded = True
            return result
        finally:
            cleanup = await self._close_lane_evidence(lane_id, fence)
            # Never transform a successful mutation into an apparent failure
            # merely because later lease cleanup had trouble. The caller gets
            # explicit cleanup evidence without being encouraged to replay.
            if succeeded and result is not None and cleanup.get("closed") is not True:
                result.update({"_veramesh_cleanup": cleanup})

    async def computer_info(self) -> dict[str, Any]:
        return await self.runtime.machine_info()

    async def read_file(
        self,
        *,
        path: str,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        return await self._ephemeral(
            operation="fs.read_text",
            capabilities=["fs.read"],
            claims=[self._fs_claim(path, "read")],
            body={"path": path, "encoding": encoding},
            task="read-file",
        )

    async def read_bytes(
        self,
        *,
        path: str,
        offset: int = 0,
        max_bytes: int | None = None,
        expected_file_version: str | None = None,
    ) -> dict[str, Any]:
        return await self._ephemeral(
            operation="fs.read_bytes",
            capabilities=["fs.read"],
            claims=[self._fs_claim(path, "read")],
            body={
                "path": path,
                "offset": offset,
                "max_bytes": max_bytes,
                "expected_file_version": expected_file_version,
            },
            task="read-bytes",
        )

    async def stat_path(self, *, path: str) -> dict[str, Any]:
        return await self._ephemeral(
            operation="fs.stat",
            capabilities=["fs.read"],
            claims=[self._fs_claim(path, "read")],
            body={"path": path},
            task="stat",
        )

    async def list_directory(
        self,
        *,
        path: str,
        offset: int = 0,
        max_entries: int = 200,
    ) -> dict[str, Any]:
        return await self._ephemeral(
            operation="fs.list_dir",
            capabilities=["fs.read"],
            claims=[self._fs_claim(path, "read")],
            body={
                "path": path,
                "offset": offset,
                "max_entries": max_entries,
            },
            task="list-directory",
        )

    async def search_files(
        self,
        *,
        root: str,
        query: str,
        offset: int = 0,
        max_results: int = 100,
        max_entries: int = 10_000,
        max_depth: int = 12,
        case_sensitive: bool = False,
    ) -> dict[str, Any]:
        return await self._ephemeral(
            operation="fs.search",
            capabilities=["fs.read"],
            claims=[self._fs_claim(root, "read")],
            body={
                "root": root,
                "query": query,
                "offset": offset,
                "max_results": max_results,
                "max_entries": max_entries,
                "max_depth": max_depth,
                "case_sensitive": case_sensitive,
            },
            task="search-files",
        )

    async def search_content(
        self,
        *,
        root: str,
        query: str,
        file_pattern: str = "*",
        offset: int = 0,
        max_results: int = 100,
        max_entries: int = 10_000,
        max_depth: int = 12,
        max_total_bytes: int = 8 * 1024 * 1024,
        case_sensitive: bool = False,
    ) -> dict[str, Any]:
        return await self._ephemeral(
            operation="fs.search_content",
            capabilities=["fs.read"],
            claims=[self._fs_claim(root, "read")],
            body={
                "root": root,
                "query": query,
                "file_pattern": file_pattern,
                "offset": offset,
                "max_results": max_results,
                "max_entries": max_entries,
                "max_depth": max_depth,
                "max_total_bytes": max_total_bytes,
                "case_sensitive": case_sensitive,
            },
            task="search-content",
        )

    async def write_file(
        self,
        *,
        path: str,
        content: str,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        return await self._ephemeral(
            operation="fs.write_text",
            capabilities=["fs.write"],
            claims=[self._fs_claim(path, "write")],
            body={"path": path, "content": content, "encoding": encoding},
            task="write-file",
        )

    async def append_file(
        self,
        *,
        path: str,
        content: str,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        return await self._ephemeral(
            operation="fs.append_text",
            capabilities=["fs.write"],
            claims=[self._fs_claim(path, "write")],
            body={"path": path, "content": content, "encoding": encoding},
            task="append-file",
        )

    async def make_directory(
        self,
        *,
        path: str,
        parents: bool = True,
    ) -> dict[str, Any]:
        return await self._ephemeral(
            operation="fs.mkdir",
            capabilities=["fs.write"],
            claims=[self._fs_claim(path, "write")],
            body={"path": path, "parents": parents},
            task="mkdir",
        )

    async def move_path(
        self,
        *,
        source: str,
        destination: str,
    ) -> dict[str, Any]:
        return await self._ephemeral(
            operation="fs.move",
            capabilities=["fs.write"],
            claims=[
                self._fs_claim(source, "write"),
                self._fs_claim(destination, "write"),
            ],
            body={"source": source, "destination": destination},
            task="move",
        )

    async def replace_text(
        self,
        *,
        path: str,
        old_string: str,
        new_string: str,
        expected_count: int = 1,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        return await self._ephemeral(
            operation="fs.replace_text",
            capabilities=["fs.read", "fs.write"],
            claims=[self._fs_claim(path, "write")],
            body={
                "path": path,
                "old_string": old_string,
                "new_string": new_string,
                "expected_count": expected_count,
                "encoding": encoding,
            },
            task="replace-text",
        )

    async def run_process(
        self,
        *,
        argv: list[str],
        cwd: str,
        timeout_s: float = 60.0,
    ) -> dict[str, Any]:
        return await self._ephemeral(
            operation="process.exec",
            capabilities=["process.exec"],
            claims=[self._cwd_claim(cwd, "write")],
            body={"argv": argv, "cwd": cwd, "timeout_s": timeout_s},
            task="run-process",
        )

    async def start_process(
        self,
        *,
        argv: list[str],
        cwd: str,
        max_runtime_s: float = 900.0,
    ) -> dict[str, Any]:
        max_runtime_s = float(max_runtime_s)
        ttl_s = min(3600.0, max_runtime_s + self.PROCESS_LEASE_GRACE_S)
        lane_id, fence = await self._open_lane(
            capabilities=[
                "process.exec",
                "process.inspect",
                "process.interact",
                "process.control",
            ],
            claims=[self._cwd_claim(cwd, "write")],
            ttl_s=ttl_s,
            task="start-process",
        )
        try:
            await self.runtime.ensure_started()
            response = await self.runtime.gateway.call_operation(
                "process.start",
                lane_id=lane_id,
                fencing_token=fence,
                argv=argv,
                cwd=cwd,
                max_runtime_s=max_runtime_s,
            )
            result = self._unwrap(response)
        except Exception:
            await self._close_lane_evidence(lane_id, fence)
            raise

        internal = result.get("process_handle")
        if not isinstance(internal, str) or not internal:
            await self._close_lane_evidence(lane_id, fence)
            raise PublicGatewayError("process.start returned no process handle")

        public_handle = self._new_id("job")
        self._processes[public_handle] = ProcessLease(
            public_handle=public_handle,
            internal_handle=internal,
            lane_id=lane_id,
            fencing_token=fence,
            cwd=canonical_remote_path(cwd),
        )
        result["process_handle"] = public_handle
        return result

    def _lease(self, process_handle: str) -> ProcessLease:
        lease = self._processes.get(process_handle)
        if lease is None:
            raise PublicProcessNotFound(process_handle)
        return lease

    @staticmethod
    def _public_process_result(
        lease: ProcessLease,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        visible = dict(result)
        visible["process_handle"] = lease.public_handle
        return visible

    async def _process_operation(
        self,
        operation: str,
        process_handle: str,
        **body: Any,
    ) -> dict[str, Any]:
        lease = self._lease(process_handle)
        await self.runtime.ensure_started()
        response = await self.runtime.gateway.call_operation(
            operation,
            lane_id=lease.lane_id,
            fencing_token=lease.fencing_token,
            process_handle=lease.internal_handle,
            **body,
        )
        return self._public_process_result(
            lease,
            self._unwrap(response),
        )

    async def process_status(self, *, process_handle: str) -> dict[str, Any]:
        return await self._process_operation(
            "process.status", process_handle
        )

    async def process_output(
        self,
        *,
        process_handle: str,
        stdout_offset: int = 0,
        stderr_offset: int = 0,
        max_bytes: int = 16_384,
    ) -> dict[str, Any]:
        return await self._process_operation(
            "process.output",
            process_handle,
            stdout_offset=stdout_offset,
            stderr_offset=stderr_offset,
            max_bytes=max_bytes,
        )

    async def process_input(
        self,
        *,
        process_handle: str,
        input_text: str,
        append_newline: bool = True,
    ) -> dict[str, Any]:
        return await self._process_operation(
            "process.input",
            process_handle,
            input_text=input_text,
            append_newline=append_newline,
        )

    async def terminate_process(
        self,
        *,
        process_handle: str,
        grace_s: float = 2.0,
    ) -> dict[str, Any]:
        return await self._process_operation(
            "process.terminate",
            process_handle,
            grace_s=grace_s,
        )

    async def release_process(
        self,
        *,
        process_handle: str,
    ) -> dict[str, Any]:
        lease = self._lease(process_handle)
        cleanup = await self._close_lane_evidence(
            lease.lane_id,
            lease.fencing_token,
        )
        if cleanup.get("closed") is True:
            self._processes.pop(process_handle, None)
        return {
            "process_handle": process_handle,
            "released": cleanup.get("closed") is True,
            "cleanup": cleanup,
        }

    async def list_processes(self) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for handle in tuple(sorted(self._processes)):
            try:
                results.append(
                    await self.process_status(process_handle=handle)
                )
            except Exception as exc:
                results.append({
                    "process_handle": handle,
                    "status_error": {
                        "code": getattr(
                            exc, "code", exc.__class__.__name__
                        ),
                        "message": str(exc),
                    },
                })
        return {"processes": results}

    async def close(self) -> dict[str, Any]:
        results = []
        for handle in tuple(sorted(self._processes)):
            results.append(
                await self.release_process(process_handle=handle)
            )
        return {
            "released_processes": results,
            "drained": all(item["released"] for item in results),
        }
