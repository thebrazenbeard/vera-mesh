from __future__ import annotations

import argparse
import asyncio

from .verarelay_live_edge import LiveEdgeConfig, VeraMeshReferenceLiveEdge


class DesktopCommanderRelay(VeraMeshReferenceLiveEdge):
    """Transparent live carrier for the Desktop Commander MCP byte stream.

    This transport deliberately does not parse MCP JSON-RPC. It forwards bytes
    in both directions so Desktop Commander's own tool names, arguments,
    unrestricted command strings, responses, notifications, and errors remain
    owned by the exact upstream DesktopCommanderMCP server.
    """


async def run(config: LiveEdgeConfig) -> None:
    relay = DesktopCommanderRelay(config)
    server = await relay.start()
    try:
        async with server:
            await server.serve_forever()
    finally:
        await relay.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="VeraRelay transparent live carrier for Desktop Commander MCP"
    )
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=17447)
    parser.add_argument("--upstream-host", required=True)
    parser.add_argument("--upstream-port", type=int, required=True)
    parser.add_argument("--max-connections", type=int, default=32)
    parser.add_argument("--connect-timeout-s", type=float, default=5.0)
    parser.add_argument("--io-chunk-bytes", type=int, default=65_536)
    args = parser.parse_args()
    asyncio.run(
        run(
            LiveEdgeConfig(
                listen_host=args.listen_host,
                listen_port=args.listen_port,
                upstream_host=args.upstream_host,
                upstream_port=args.upstream_port,
                max_connections=args.max_connections,
                connect_timeout_s=args.connect_timeout_s,
                io_chunk_bytes=args.io_chunk_bytes,
            )
        )
    )


if __name__ == "__main__":
    main()
