from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .core import LaneRegistry
from .executor import LocalExecutor
from .protocol import VeraPortAgent
from .state import AgentStateStore


BASE_CAPABILITIES = frozenset({"fs.read", "fs.write"})


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


def build_agent(
    roots: tuple[Path, ...],
    max_lanes: int,
    *,
    allow_process_exec: bool = False,
    state_db: Path | None = None,
) -> VeraPortAgent:
    capabilities = set(BASE_CAPABILITIES)
    if allow_process_exec:
        capabilities.add("process.exec")
    state_store = AgentStateStore(state_db) if state_db is not None else None
    registry = LaneRegistry(
        capabilities,
        max_lanes=max_lanes,
        fence_allocator=state_store.next_fence if state_store is not None else None,
    )
    executor = LocalExecutor(registry, allowed_roots=roots, allow_process_exec=allow_process_exec)
    return VeraPortAgent(registry, executor, state_store)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local VeraPort reference agent over JSONL stdio")
    parser.add_argument("--root", action="append", required=True, help="Allowed filesystem/process root")
    parser.add_argument("--max-lanes", type=int, default=32)
    parser.add_argument("--state-db", help="SQLite path for durable fencing/idempotency state")
    parser.add_argument(
        "--allow-process-exec",
        action="store_true",
        help="Explicitly enable broad host process execution; disabled by default",
    )
    args = parser.parse_args(argv)
    roots = tuple(Path(value) for value in args.root)
    state_db = Path(args.state_db) if args.state_db else None
    return asyncio.run(
        serve(
            build_agent(
                roots,
                args.max_lanes,
                allow_process_exec=args.allow_process_exec,
                state_db=state_db,
            )
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
