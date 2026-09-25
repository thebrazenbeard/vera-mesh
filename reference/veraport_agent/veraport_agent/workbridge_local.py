from __future__ import annotations

import ipaddress
import json
import ntpath
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from .core import ClaimMode


SCHEMA = "VERAPORT_WORKBRIDGE_LOCAL_V1"


class WorkBridgeLocalError(RuntimeError):
    code = "WORKBRIDGE_LOCAL_ERROR"


class WorkBridgePathDenied(PermissionError):
    code = "WORKBRIDGE_PATH_DENIED"


def _canonical_windows_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkBridgePathDenied("path must be a non-empty Windows absolute path")
    raw = value.strip().replace("/", "\\")
    drive, tail = ntpath.splitdrive(raw)
    if len(drive) != 2 or drive[1] != ":" or not drive[0].isalpha() or not tail.startswith("\\"):
        raise WorkBridgePathDenied("WorkBridge path must be an absolute drive path")
    normalized = ntpath.normpath(drive[0].upper() + ":" + tail)
    if ":" in normalized[2:]:
        raise WorkBridgePathDenied("alternate data streams are not supported")
    return normalized


def _resource_key(path: str) -> str:
    return "fs:" + _canonical_windows_path(path).replace("\\", "/")


def _within(candidate: str, root: str) -> bool:
    candidate = _canonical_windows_path(candidate)
    root = _canonical_windows_path(root)
    try:
        common = ntpath.commonpath([candidate.casefold(), root.casefold()])
    except ValueError:
        return False
    return common == root.casefold()


@dataclass(frozen=True)
class WorkBridgeLocalConfig:
    endpoint: str
    bearer_token_file: Path
    read_roots: tuple[str, ...]
    timeout_seconds: float = 10.0

    @classmethod
    def load(cls, path: str | Path) -> "WorkBridgeLocalConfig":
        source = Path(path)
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkBridgeLocalError(f"cannot read WorkBridge local config: {exc}") from exc
        if not isinstance(value, dict):
            raise WorkBridgeLocalError("WorkBridge local config must be a JSON object")
        allowed = {"schema", "endpoint", "bearer_token_file", "read_roots", "timeout_seconds"}
        extra = set(value) - allowed
        if extra:
            raise WorkBridgeLocalError(f"unknown WorkBridge local config fields: {sorted(extra)}")
        if value.get("schema") != SCHEMA:
            raise WorkBridgeLocalError(f"schema must be {SCHEMA}")
        roots = value.get("read_roots")
        if not isinstance(roots, list) or not roots:
            raise WorkBridgeLocalError("read_roots must be a non-empty list")
        cfg = cls(
            endpoint=str(value.get("endpoint", "")),
            bearer_token_file=Path(str(value.get("bearer_token_file", ""))).expanduser(),
            read_roots=tuple(_canonical_windows_path(str(item)) for item in roots),
            timeout_seconds=float(value.get("timeout_seconds", 10.0)),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        parsed = urlsplit(self.endpoint)
        if parsed.scheme != "http" or parsed.username is not None or parsed.password is not None:
            raise WorkBridgeLocalError("endpoint must be unauthenticated URL syntax over loopback HTTP")
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
            port = parsed.port
        except ValueError as exc:
            raise WorkBridgeLocalError("endpoint must use a literal loopback IP and valid port") from exc
        if not address.is_loopback or port is None:
            raise WorkBridgeLocalError("endpoint must use a literal loopback IP and explicit port")
        if parsed.query or parsed.fragment or not parsed.path.startswith("/") or parsed.path == "/" or parsed.path.endswith("/"):
            raise WorkBridgeLocalError("endpoint must use a dedicated non-root MCP path without query or fragment")
        if not 0 < self.timeout_seconds <= 60:
            raise WorkBridgeLocalError("timeout_seconds must be in (0, 60]")
        if len({root.casefold() for root in self.read_roots}) != len(self.read_roots):
            raise WorkBridgeLocalError("read_roots contains duplicates")
        if not str(self.bearer_token_file):
            raise WorkBridgeLocalError("bearer_token_file is required")

    def validate_runtime_files(self) -> None:
        if not self.bearer_token_file.is_file():
            raise WorkBridgeLocalError(f"bearer token file missing: {self.bearer_token_file}")


Caller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class WorkBridgeLocalBackend:
    def __init__(
        self,
        config: WorkBridgeLocalConfig,
        registry: Any,
        *,
        caller: Caller | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self.registry = registry
        self._caller = caller or self._call_mcp

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        registry: Any,
    ) -> "WorkBridgeLocalBackend":
        config = WorkBridgeLocalConfig.load(path)
        config.validate_runtime_files()
        return cls(config, registry)

    def can_handle(self, path: str) -> bool:
        try:
            candidate = _canonical_windows_path(path)
        except WorkBridgePathDenied:
            return False
        return any(_within(candidate, root) for root in self.config.read_roots)

    def _authorize(self, lane_id: str, fencing_token: int, path: str) -> str:
        candidate = _canonical_windows_path(path)
        if not any(_within(candidate, root) for root in self.config.read_roots):
            raise WorkBridgePathDenied(candidate)
        self.registry.authorize(
            lane_id,
            fencing_token,
            "fs.read",
            resource_key=_resource_key(candidate),
            resource_mode=ClaimMode.READ,
        )
        return candidate

    def _token(self) -> str:
        self.config.validate_runtime_files()
        try:
            token = self.config.bearer_token_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise WorkBridgeLocalError(f"cannot read WorkBridge bearer token: {exc}") from exc
        if len(token) < 32 or any(ch.isspace() for ch in token):
            raise WorkBridgeLocalError("WorkBridge bearer token is invalid")
        return token

    async def _call_mcp(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            import httpx
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise WorkBridgeLocalError("WorkBridge local adapter requires the VeraPort mcp runtime extra") from exc

        headers = {"Authorization": "Bearer " + self._token()}
        timeout = httpx.Timeout(self.config.timeout_seconds)
        async with httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        ) as http_client:
            async with streamable_http_client(
                self.config.endpoint,
                http_client=http_client,
                terminate_on_close=True,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool, arguments)
        if result.isError:
            raise WorkBridgeLocalError(f"WorkBridge tool returned application error: {tool}")
        structured = result.structuredContent
        if not isinstance(structured, dict):
            raise WorkBridgeLocalError(f"WorkBridge tool returned no structured object: {tool}")
        return dict(structured)

    async def read_text(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        path: str,
        encoding: str = "utf-8",
    ) -> str:
        if encoding.casefold().replace("-", "") != "utf8":
            raise WorkBridgeLocalError("WorkBridge delegated text reads support UTF-8 only")
        candidate = self._authorize(lane_id, fencing_token, path)
        out = await self._caller("workspace_read_text", {"path": candidate})
        text = out.get("text")
        if not isinstance(text, str):
            raise WorkBridgeLocalError("workspace_read_text returned invalid structured content")
        return text

    @staticmethod
    def _entry(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise WorkBridgeLocalError("WorkBridge directory entry is not an object")
        path = _canonical_windows_path(str(value.get("path", "")))
        kind = str(value.get("type", ""))
        if kind not in {"file", "directory", "symlink", "other"}:
            raise WorkBridgeLocalError("WorkBridge directory entry has invalid type")
        return {
            "name": str(value.get("name", "")),
            "path": path.replace("\\", "/"),
            "type": kind,
            "is_symlink": value.get("is_symlink") is True,
            "size_bytes": int(value.get("size_bytes", 0)),
            "mtime_ns": int(value.get("mtime_ns", 0)),
        }

    async def stat(
        self,
        *,
        lane_id: str,
        fencing_token: int,
        path: str,
    ) -> dict[str, Any]:
        candidate = self._authorize(lane_id, fencing_token, path)
        out = await self._caller("workspace_stat", {"path": candidate})
        value = out.get("stat")
        entry = self._entry(value)
        entry["backend"] = "workbridge"
        return entry

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
        if type(max_entries) is not int or not 1 <= max_entries <= 1000:
            raise ValueError("max_entries must be in 1..1000")
        candidate = self._authorize(lane_id, fencing_token, path)
        out = await self._caller("workspace_list", {"path": candidate})
        raw = out.get("entries")
        if not isinstance(raw, list):
            raise WorkBridgeLocalError("workspace_list returned invalid structured content")
        entries = [self._entry(item) for item in raw]
        total = len(entries)
        selected = entries[offset : offset + max_entries]
        next_offset = offset + len(selected)
        return {
            "path": candidate.replace("\\", "/"),
            "entries": selected,
            "offset": offset,
            "next_offset": next_offset if next_offset < total else None,
            "total_entries": total,
            "truncated": next_offset < total,
            "backend": "workbridge",
        }
