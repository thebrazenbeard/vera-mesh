from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .controller_config import ControllerConfig
from .windows_acl import (
    harden_private_directory,
    harden_private_file,
    validate_private_directory,
    validate_private_file,
)


DEFAULT_CONFIG_PATH = Path(r"C:\ProgramData\VeraMesh\tunnel-runtime.json")
_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class TunnelRuntimeServiceError(RuntimeError):
    code = "TUNNEL_RUNTIME_SERVICE_ERROR"


@dataclass(frozen=True)
class TunnelRuntimeServiceConfig:
    tunnel_client: Path
    alias: str
    tunnel_id: str
    runtime_api_key_file: Path
    controller_config: Path
    mcp_command: str
    profile_dir: Path
    state_dir: Path
    status_interval_s: float = 5.0
    command_timeout_s: float = 60.0

    @classmethod
    def load(cls, path: str | Path) -> "TunnelRuntimeServiceConfig":
        source = Path(path)
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TunnelRuntimeServiceError(
                f"cannot read strict UTF-8 JSON config: {exc}"
            ) from exc
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TunnelRuntimeServiceConfig":
        if not isinstance(value, dict):
            raise TunnelRuntimeServiceError("config must be a JSON object")
        if value.get("schema") != "VERAMESH_TUNNEL_RUNTIME_SERVICE_V1":
            raise TunnelRuntimeServiceError("wrong tunnel runtime config schema")

        required = (
            "tunnel_client",
            "alias",
            "tunnel_id",
            "runtime_api_key_file",
            "controller_config",
            "mcp_command",
            "profile_dir",
            "state_dir",
        )
        missing = [key for key in required if key not in value]
        if missing:
            raise TunnelRuntimeServiceError(
                f"missing required fields: {missing}"
            )

        cfg = cls(
            tunnel_client=Path(str(value["tunnel_client"])).expanduser().resolve(),
            alias=str(value["alias"]).strip(),
            tunnel_id=str(value["tunnel_id"]).strip(),
            runtime_api_key_file=Path(
                str(value["runtime_api_key_file"])
            ).expanduser().resolve(),
            controller_config=Path(
                str(value["controller_config"])
            ).expanduser().resolve(),
            mcp_command=str(value["mcp_command"]).strip(),
            profile_dir=Path(str(value["profile_dir"])).expanduser().resolve(),
            state_dir=Path(str(value["state_dir"])).expanduser().resolve(),
            status_interval_s=float(value.get("status_interval_s", 5.0)),
            command_timeout_s=float(value.get("command_timeout_s", 60.0)),
        )
        cfg.validate_static()
        return cfg

    def validate_static(self) -> None:
        if not _ALIAS_RE.fullmatch(self.alias):
            raise TunnelRuntimeServiceError(
                "alias must use letters, numbers, '.', '_' or '-'"
            )
        if (
            not self.tunnel_id
            or len(self.tunnel_id) > 256
            or any(ch.isspace() or ord(ch) < 32 for ch in self.tunnel_id)
        ):
            raise TunnelRuntimeServiceError(
                "tunnel_id must be a bounded non-whitespace identifier"
            )
        if (
            not self.mcp_command
            or len(self.mcp_command) > 4096
            or "\x00" in self.mcp_command
            or "\r" in self.mcp_command
            or "\n" in self.mcp_command
        ):
            raise TunnelRuntimeServiceError("invalid mcp_command")
        if not 1.0 <= self.status_interval_s <= 300.0:
            raise TunnelRuntimeServiceError(
                "status_interval_s must be in 1..300"
            )
        if not 5.0 <= self.command_timeout_s <= 300.0:
            raise TunnelRuntimeServiceError(
                "command_timeout_s must be in 5..300"
            )

    def validate_runtime_files(self) -> None:
        for path, label in (
            (self.tunnel_client, "tunnel-client executable"),
            (self.runtime_api_key_file, "runtime API key file"),
            (self.controller_config, "VeraPort controller config"),
        ):
            if not path.is_file():
                raise TunnelRuntimeServiceError(f"{label} missing: {path}")
        for path, label in (
            (self.profile_dir, "tunnel profile directory"),
            (self.state_dir, "tunnel state directory"),
        ):
            if not path.is_dir():
                raise TunnelRuntimeServiceError(f"{label} missing: {path}")


@dataclass(frozen=True)
class RuntimeStatus:
    process_running: bool
    healthy: bool
    ready: bool
    raw: dict[str, Any]

    @property
    def usable(self) -> bool:
        return self.process_running and self.healthy


Runner = Callable[..., subprocess.CompletedProcess[str]]


def runtime_environment(
    config: TunnelRuntimeServiceConfig,
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    # The runtime key is supplied only through tunnel-client's file: secret
    # locator. Remove ambient API-key fallbacks so neither the managed runtime
    # nor its stdio MCP child receives an unnecessary bearer secret.
    env.pop("CONTROL_PLANE_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env["TUNNEL_CLIENT_PROFILE_DIR"] = str(config.profile_dir)
    env["TUNNEL_CLIENT_STATE_DIR"] = str(config.state_dir)
    env["VERAPORT_CONTROLLER_CONFIG"] = str(config.controller_config)
    env["VERAPORT_MCP_TRANSPORT"] = "stdio"
    return env


def connect_args(config: TunnelRuntimeServiceConfig) -> list[str]:
    return [
        str(config.tunnel_client),
        "runtimes",
        "connect",
        "--alias",
        config.alias,
        "--tunnel-id",
        config.tunnel_id,
        "--runtime-api-key",
        "file:" + str(config.runtime_api_key_file),
        "--profile",
        config.alias,
        "--profile-dir",
        str(config.profile_dir),
        "--mcp-command",
        config.mcp_command,
    ]


def status_args(config: TunnelRuntimeServiceConfig) -> list[str]:
    return [
        str(config.tunnel_client),
        "runtimes",
        "status",
        config.alias,
        "--json",
    ]


def stop_args(config: TunnelRuntimeServiceConfig) -> list[str]:
    return [
        str(config.tunnel_client),
        "runtimes",
        "stop",
        config.alias,
        "--json",
    ]


def _run(
    config: TunnelRuntimeServiceConfig,
    args: list[str],
    *,
    runner: Runner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    completed = runner(
        args,
        env=runtime_environment(config),
        cwd=str(config.state_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=config.command_timeout_s,
        shell=False,
        check=False,
    )
    if completed.returncode != 0:
        # Do not reflect child stdout/stderr into SCM errors. A dependency may
        # regress its own redaction; the service boundary must not turn that
        # into a secret-bearing Windows event-log message.
        raise TunnelRuntimeServiceError(
            f"tunnel-client command failed rc={completed.returncode}"
        )
    return completed


def connect_runtime(
    config: TunnelRuntimeServiceConfig,
    *,
    runner: Runner = subprocess.run,
) -> None:
    _run(config, connect_args(config), runner=runner)


def read_status(
    config: TunnelRuntimeServiceConfig,
    *,
    runner: Runner = subprocess.run,
) -> RuntimeStatus:
    completed = _run(config, status_args(config), runner=runner)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise TunnelRuntimeServiceError(
            "tunnel-client status did not return JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise TunnelRuntimeServiceError(
            "tunnel-client status JSON must be an object"
        )
    for key in ("process_running", "healthy", "ready"):
        if type(payload.get(key)) is not bool:
            raise TunnelRuntimeServiceError(
                f"tunnel-client status missing exact bool {key}"
            )
    return RuntimeStatus(
        process_running=payload["process_running"],
        healthy=payload["healthy"],
        ready=payload["ready"],
        raw=payload,
    )


def stop_runtime(
    config: TunnelRuntimeServiceConfig,
    *,
    runner: Runner = subprocess.run,
) -> None:
    _run(config, stop_args(config), runner=runner)


def harden_service_materials(
    config_path: str | Path,
    config: TunnelRuntimeServiceConfig,
) -> None:
    """Apply the qualified LocalSystem ACL profile to tunnel runtime material."""
    config_path = Path(config_path)
    controller = ControllerConfig.load(config.controller_config)
    protected_files = {
        config_path,
        config.runtime_api_key_file,
        config.controller_config,
        controller.controller_key,
        controller.tls_ca,
        controller.workstation_public_key,
    }
    protected_dirs = {
        config_path.parent,
        config.profile_dir,
        config.state_dir,
        *(path.parent for path in protected_files),
    }
    for directory in sorted(protected_dirs, key=lambda p: len(str(p))):
        harden_private_directory(directory)
    for path in protected_files:
        harden_private_file(path)


def validate_service_materials(
    config_path: str | Path,
    config: TunnelRuntimeServiceConfig,
) -> None:
    config_path = Path(config_path)
    controller = ControllerConfig.load(config.controller_config)
    protected_files = {
        config_path,
        config.runtime_api_key_file,
        config.controller_config,
        controller.controller_key,
        controller.tls_ca,
        controller.workstation_public_key,
    }
    protected_dirs = {
        config_path.parent,
        config.profile_dir,
        config.state_dir,
        *(path.parent for path in protected_files),
    }
    for directory in protected_dirs:
        validate_private_directory(directory)
    for path in protected_files:
        validate_private_file(path)


def run_until_stop(
    config_path: str | Path,
    stop_event: threading.Event,
    *,
    runner: Runner = subprocess.run,
    validate_acl: bool = True,
) -> None:
    config = TunnelRuntimeServiceConfig.load(config_path)
    config.validate_runtime_files()
    if validate_acl:
        validate_service_materials(config_path, config)

    try:
        try:
            status = read_status(config, runner=runner)
        except TunnelRuntimeServiceError:
            status = None

        if status is None or not status.usable:
            if status is not None and status.process_running:
                try:
                    stop_runtime(config, runner=runner)
                except TunnelRuntimeServiceError:
                    pass
            connect_runtime(config, runner=runner)
            status = read_status(config, runner=runner)
            if not status.usable:
                raise TunnelRuntimeServiceError(
                    "managed tunnel runtime did not become running and healthy"
                )

        while not stop_event.wait(config.status_interval_s):
            try:
                status = read_status(config, runner=runner)
            except TunnelRuntimeServiceError:
                status = None
            if status is not None and status.usable:
                continue
            if status is not None and status.process_running:
                try:
                    stop_runtime(config, runner=runner)
                except TunnelRuntimeServiceError:
                    pass
            connect_runtime(config, runner=runner)
            status = read_status(config, runner=runner)
            if not status.usable:
                raise TunnelRuntimeServiceError(
                    "managed tunnel runtime remained unhealthy after reconnect"
                )
    finally:
        try:
            stop_runtime(config, runner=runner)
        except Exception:
            # Service shutdown must continue even if the managed runtime has
            # already disappeared. SCM/event logging records the service result.
            pass


try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
except ImportError:
    servicemanager = None
    win32event = None
    win32service = None
    win32serviceutil = None


class TunnelRuntimeWindowsServiceUnavailable(RuntimeError):
    code = "TUNNEL_RUNTIME_WINDOWS_SERVICE_UNAVAILABLE"


if win32serviceutil is not None:
    class VeraMeshTunnelRuntimeService(win32serviceutil.ServiceFramework):
        _svc_name_ = "VeraMeshTunnelRuntime"
        _svc_display_name_ = "VeraMesh Secure MCP Tunnel Runtime"
        _svc_deps_ = ["VeraPortAgent"]
        _svc_description_ = (
            "Keeps the managed OpenAI tunnel-client runtime available for "
            "VeraMesh without a foreground PowerShell session."
        )

        def __init__(self, args):
            super().__init__(args)
            self._stop_event = threading.Event()
            self._service_stop = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._stop_event.set()
            win32event.SetEvent(self._service_stop)

        def SvcDoRun(self):
            servicemanager.LogInfoMsg(
                "VeraMeshTunnelRuntime starting with fixed config "
                + str(DEFAULT_CONFIG_PATH)
            )
            try:
                run_until_stop(DEFAULT_CONFIG_PATH, self._stop_event)
            except Exception as exc:
                servicemanager.LogErrorMsg(
                    "VeraMeshTunnelRuntime failed: " + str(exc)
                )
                raise
            finally:
                servicemanager.LogInfoMsg(
                    "VeraMeshTunnelRuntime stopped"
                )
else:
    class VeraMeshTunnelRuntimeService:
        def __init__(self, *args, **kwargs):
            raise TunnelRuntimeWindowsServiceUnavailable(
                "pywin32 is required for Windows Service mode"
            )


def service_cli() -> None:
    if win32serviceutil is None:
        raise TunnelRuntimeWindowsServiceUnavailable(
            "pywin32 is required for Windows Service mode"
        )
    win32serviceutil.HandleCommandLine(VeraMeshTunnelRuntimeService)
