from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .core import LaneRegistry
from .executor import LocalExecutor
from .protocol import VeraPortAgent


DEFAULT_CAPABILITIES = frozenset({"fs.read", "fs.write", "process.exec"})


async def serve(agent: VeraPortAgent) -> int:
    """Serve newline-delimited JSON requests on stdin with concurrent dispatch.

    This adapter is deliberately local-only.  It is a reference multiplexing
    surface, not the authenticated network transport for production VeraMesh.
    """

    write_lock = asyncio.Lock()
    pending: set[asyncio.Task[None]] = set()

    async def emit(response: dict) -> None:
        payload = json.dumps(response, sort_keys=True, separators=(",", ":"))
        async with write_lock:
            print(payload, flush=True)

    async def handle_line(raw: str) -> None:
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            response = await agent.handle(request)
        except Exception as exc:
            response = {
                "protocol_version": "veraport-v1",
                "request_id": None,
                "ok": False,
                "error": {"code": exc.__class__.__name__.upper(), "message": str(exc)},
            }
        await emit(response)

    while True:
        raw = await asyncio.to_thread(sys.stdin.readline)
        if raw == "":
            break
        if not raw.strip():
            continue
        task = asyncio.create_task(handle_line(raw))
        pending.add(task)
        task.add_done_callback(pending.discard)

    if pending:
        await asyncio.gather(*pending)
    return 0


def build_agent(roots: tuple[Path, ...], max_lanes: int) -> VeraPortAgent:
    registry = LaneRegistry(DEFAULT_CAPABILITIES, max_lanes=max_lanes)
    executor = LocalExecutor(registry, allowed_roots=roots)
    return VeraPortAgent(registry, executor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local VeraPort reference agent over JSONL stdio")
    parser.add_argument("--root", action="append", required=True, help="Allowed filesystem/process root")
    parser.add_argument("--max-lanes", type=int, default=32)
    args = parser.parse_args(argv)
    roots = tuple(Path(value) for value in args.root)
    return asyncio.run(serve(build_agent(roots, args.max_lanes)))


if __name__ == "__main__":
    raise SystemExit(main())
