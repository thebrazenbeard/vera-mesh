#!/usr/bin/env python3
"""Stateless OpenAI Responses API operator for a private VeraMesh MCP tunnel.

The client never persists OPENAI_API_KEY or VERAMESH_TUNNEL_ID and uses
store=false by default. It replays complete Responses output items exactly as
required for stateless continuation. MCP calls require explicit approval by
default.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

API_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-6-astra"
DEFAULT_SERVER_LABEL = "lappy_veramesh"

LANE_TOOLS = (
    "machine_info",
    "lane_list",
    "lane_open",
    "lane_renew",
    "lane_close",
)
READ_TOOLS = (
    "fs_read_text",
    "fs_read_bytes",
    "fs_stat",
    "fs_list_dir",
    "fs_search",
    "fs_search_content",
)
WRITE_TOOLS = (
    "fs_write_text",
    "fs_append_text",
    "fs_mkdir",
    "fs_move",
    "fs_replace_text",
)
PROCESS_TOOLS = (
    "process_exec",
    "process_start",
    "process_list",
    "process_status",
    "process_output",
    "process_input",
    "process_terminate",
)

PROFILES: dict[str, tuple[str, ...]] = {
    "read": LANE_TOOLS + READ_TOOLS,
    "filesystem": LANE_TOOLS + READ_TOOLS + WRITE_TOOLS,
    "build": LANE_TOOLS + READ_TOOLS + WRITE_TOOLS + PROCESS_TOOLS,
}

READONLY_AUTO_APPROVE_TOOLS = (
    "machine_info",
    "lane_list",
    "fs_read_text",
    "fs_read_bytes",
    "fs_stat",
    "fs_list_dir",
    "fs_search",
    "fs_search_content",
    "process_list",
    "process_status",
    "process_output",
)

OPERATOR_INSTRUCTIONS = """You are operating Patrick's Lappy through VeraMesh.
Treat tool discovery as capability discovery, not authority. Underlying VeraPort
capability ceilings, lane claims, fencing, roots, and local policy are controlling.
Establish current machine/session state before consequential work. Use the smallest
required lane and tool set. Never expose credentials, API keys, private keys,
tunnel identifiers, capability URLs, or secret file contents. Preserve unrelated
work. Do not delete or destructively overwrite durable state unless the user has
explicitly requested that exact effect. Do not claim an operation succeeded unless
the MCP result supplies evidence. Process tools, when present, are for bounded
build/test work and managed interactive processes; do not treat their presence as
permission to change protected system configuration."""


class OperatorError(RuntimeError):
    """Base error for operator configuration, transport, or protocol failures."""


class ApiError(OperatorError):
    """OpenAI API request failed."""


@dataclass(frozen=True)
class OperatorConfig:
    api_key: str
    tunnel_id: str
    model: str = DEFAULT_MODEL
    server_label: str = DEFAULT_SERVER_LABEL
    profile: str = "read"
    auto_approve_readonly: bool = False
    timeout_s: float = 180.0
    api_url: str = API_URL

    def validate(self) -> None:
        if not self.api_key.strip():
            raise OperatorError("OPENAI_API_KEY is required")
        if not self.tunnel_id.strip():
            raise OperatorError(
                "VERAMESH_TUNNEL_ID is required, or use --tunnel-config"
            )
        if self.profile not in PROFILES:
            raise OperatorError(f"unknown profile: {self.profile}")
        if not self.model.strip():
            raise OperatorError("model must not be empty")
        if not 5 <= self.timeout_s <= 900:
            raise OperatorError("timeout must be in 5..900 seconds")


class ResponsesTransport:
    """Small dependency-free HTTP transport for POST /v1/responses."""

    def __init__(self, config: OperatorConfig) -> None:
        self.config = config

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.config.api_url,
            data=raw,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "veramesh-responses-operator/1.0",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_s
            ) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ApiError(
                f"OpenAI Responses API returned HTTP {exc.code}: "
                f"{redact_text(body, self.config)}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"OpenAI Responses API connection failed: {exc.reason}") from exc

        try:
            value = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ApiError("OpenAI Responses API returned non-JSON output") from exc
        if not isinstance(value, dict):
            raise ApiError("OpenAI Responses API response must be a JSON object")
        return value


def load_tunnel_id_from_config(path: Path) -> str:
    """Read only tunnel_id from a VeraMesh tunnel runtime configuration file."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperatorError(f"cannot read tunnel config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise OperatorError("tunnel config must be a JSON object")
    if value.get("schema") != "VERAMESH_TUNNEL_RUNTIME_SERVICE_V1":
        raise OperatorError("unexpected VeraMesh tunnel config schema")
    tunnel_id = value.get("tunnel_id")
    if not isinstance(tunnel_id, str) or not tunnel_id.strip():
        raise OperatorError("tunnel config does not contain a valid tunnel_id")
    return tunnel_id.strip()


def resolve_tunnel_id(
    explicit: str | None,
    tunnel_config: Path | None,
    environ: Mapping[str, str] = os.environ,
) -> str:
    if explicit:
        return explicit.strip()
    env_value = environ.get("VERAMESH_TUNNEL_ID", "").strip()
    if env_value:
        return env_value
    if tunnel_config is not None:
        return load_tunnel_id_from_config(tunnel_config)
    return ""


def approval_policy(config: OperatorConfig) -> str | dict[str, Any]:
    if not config.auto_approve_readonly:
        return "always"
    available = set(PROFILES[config.profile])
    safe = [name for name in READONLY_AUTO_APPROVE_TOOLS if name in available]
    return {"never": {"tool_names": safe}}


def build_mcp_tool(config: OperatorConfig) -> dict[str, Any]:
    """Build a Responses API MCP definition using tunnel_id, never server_url."""
    return {
        "type": "mcp",
        "server_label": config.server_label,
        "server_description": (
            "Private VeraMesh MCP bridge to Patrick's Lappy. Tool availability "
            "does not expand VeraPort authority."
        ),
        "tunnel_id": config.tunnel_id,
        "allowed_tools": list(PROFILES[config.profile]),
        "require_approval": approval_policy(config),
    }


def user_input_item(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def approval_response_item(
    approval_request_id: str,
    approve: bool,
    reason: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": "mcp_approval_response",
        "approval_request_id": approval_request_id,
        "approve": approve,
    }
    if reason:
        item["reason"] = reason
    return item


def response_output(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = response.get("output", [])
    if not isinstance(value, list):
        raise OperatorError("response.output must be a list")
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            result.append(dict(item))
    return result


def approval_requests(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in response_output(response)
        if item.get("type") == "mcp_approval_request"
    ]


def output_text(response: Mapping[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct:
        return direct

    chunks: list[str] = []
    for item in response_output(response):
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"output_text", "text"}:
                text = block.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    return "\n".join(chunks)


def parse_arguments(item: Mapping[str, Any]) -> Any:
    raw = item.get("arguments")
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def redact_text(text: str, config: OperatorConfig) -> str:
    result = text
    for secret in (config.api_key, config.tunnel_id):
        if secret:
            result = result.replace(secret, "<redacted>")
    return result


def redacted_payload(payload: Mapping[str, Any], config: OperatorConfig) -> dict[str, Any]:
    copy = json.loads(json.dumps(payload))
    for tool in copy.get("tools", []):
        if isinstance(tool, dict) and "tunnel_id" in tool:
            tool["tunnel_id"] = "<redacted>"
    return copy


ApprovalDecider = Callable[[dict[str, Any]], tuple[bool, str | None]]


class OperatorSession:
    """Stateless Responses conversation that replays all returned output items."""

    def __init__(
        self,
        config: OperatorConfig,
        transport: ResponsesTransport | Any | None = None,
        approval_decider: ApprovalDecider | None = None,
        max_approval_rounds: int = 32,
    ) -> None:
        config.validate()
        self.config = config
        self.transport = transport or ResponsesTransport(config)
        self.approval_decider = approval_decider or interactive_approval_decider
        self.max_approval_rounds = max_approval_rounds
        self.transcript: list[dict[str, Any]] = []
        self.last_response: dict[str, Any] | None = None

    def _payload(self) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "store": False,
            "instructions": OPERATOR_INSTRUCTIONS,
            "tools": [build_mcp_tool(self.config)],
            "input": list(self.transcript),
        }

    def send(self, prompt: str) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise OperatorError("prompt must not be empty")
        self.transcript.append(user_input_item(prompt))
        return self._advance_until_settled()

    def _advance_until_settled(self) -> dict[str, Any]:
        for _round in range(self.max_approval_rounds + 1):
            response = self.transport.create(self._payload())
            self.last_response = response
            emitted = response_output(response)
            self.transcript.extend(emitted)

            approvals = [
                item for item in emitted
                if item.get("type") == "mcp_approval_request"
            ]
            if not approvals:
                return response

            if _round >= self.max_approval_rounds:
                raise OperatorError("too many consecutive MCP approval rounds")

            for request in approvals:
                request_id = request.get("id")
                if not isinstance(request_id, str) or not request_id:
                    raise OperatorError("MCP approval request missing id")
                approve, reason = self.approval_decider(request)
                self.transcript.append(
                    approval_response_item(request_id, approve, reason)
                )

        raise OperatorError("approval loop did not settle")


def interactive_approval_decider(
    request: dict[str, Any],
) -> tuple[bool, str | None]:
    name = request.get("name", "<unknown>")
    arguments = parse_arguments(request)
    print("\nMCP approval required")
    print(f"  tool: {name}")
    print("  arguments:")
    print(json.dumps(arguments, indent=2, ensure_ascii=False))
    answer = input("Approve this exact call? [y/N]: ").strip().lower()
    if answer in {"y", "yes"}:
        return True, None
    reason = input("Optional rejection reason (Enter to skip): ").strip()
    return False, reason or "user rejected MCP tool call"


def build_config(args: argparse.Namespace) -> OperatorConfig:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    tunnel_id = resolve_tunnel_id(
        args.tunnel_id,
        args.tunnel_config,
    )
    return OperatorConfig(
        api_key=api_key,
        tunnel_id=tunnel_id,
        model=args.model,
        server_label=args.server_label,
        profile=args.profile,
        auto_approve_readonly=args.auto_approve_readonly,
        timeout_s=args.timeout,
        api_url=args.api_url,
    )


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Operate Lappy through OpenAI Responses API + VeraMesh Secure MCP Tunnel. "
            "No ChatGPT developer-mode app is required."
        )
    )
    p.add_argument("--prompt", help="Run one prompt and exit")
    p.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="read",
        help="Tool ceiling exposed to the model (default: read)",
    )
    p.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
        help="Responses API model (default: OPENAI_MODEL or gpt-6-astra)",
    )
    p.add_argument("--server-label", default=DEFAULT_SERVER_LABEL)
    p.add_argument("--tunnel-id", help="Tunnel ID; prefer VERAMESH_TUNNEL_ID")
    p.add_argument(
        "--tunnel-config",
        type=Path,
        help=(
            "Read only tunnel_id from a VeraMesh tunnel-runtime.json file. "
            "The runtime API key is never read."
        ),
    )
    p.add_argument(
        "--auto-approve-readonly",
        action="store_true",
        help="Skip API approval only for the fixed read-only tool allowlist",
    )
    p.add_argument("--timeout", type=float, default=180.0)
    p.add_argument("--api-url", default=API_URL, help=argparse.SUPPRESS)
    p.add_argument(
        "--doctor",
        action="store_true",
        help="Validate local configuration without making an API request",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print a redacted first-request payload without making an API request",
    )
    return p


def doctor(config: OperatorConfig) -> int:
    config.validate()
    print("VeraMesh Responses operator configuration: PASS")
    print(f"model: {config.model}")
    print(f"profile: {config.profile}")
    print(f"allowed_tools: {len(PROFILES[config.profile])}")
    print("OPENAI_API_KEY: present (redacted)")
    print("VERAMESH_TUNNEL_ID: present (redacted)")
    print(f"approval_policy: {json.dumps(approval_policy(config), sort_keys=True)}")
    print("store: false")
    return 0


def run_cli(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = build_config(args)
        config.validate()

        if args.doctor:
            return doctor(config)

        session = OperatorSession(config)

        if args.dry_run:
            session.transcript.append(user_input_item(args.prompt or "Inspect Lappy."))
            print(json.dumps(redacted_payload(session._payload(), config), indent=2))
            return 0

        print(
            f"VeraMesh Responses operator: model={config.model} "
            f"profile={config.profile} store=false"
        )
        print("MCP calls require explicit approval unless --auto-approve-readonly was set.")

        if args.prompt:
            response = session.send(args.prompt)
            text = output_text(response)
            if text:
                print(text)
            return 0

        print("Interactive mode. Type /quit to exit, /reset to clear local transcript.")
        while True:
            try:
                prompt = input("\nvera> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not prompt:
                continue
            if prompt in {"/quit", "/exit"}:
                return 0
            if prompt == "/reset":
                session.transcript.clear()
                session.last_response = None
                print("Local transcript cleared.")
                continue
            response = session.send(prompt)
            text = output_text(response)
            if text:
                print(f"\n{text}")
    except OperatorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run_cli())
