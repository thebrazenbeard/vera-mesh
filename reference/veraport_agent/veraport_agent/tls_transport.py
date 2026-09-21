from __future__ import annotations

import asyncio
import ssl
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Awaitable, Callable

from cryptography.hazmat.primitives.asymmetric import ec

from .hot_session import (
    ClientAuth,
    ServerAccept,
    ServerChallenge,
    SessionBinding,
    WorkstationAuthenticator,
    verify_server_accept,
)
from .stream import MultiplexClient, read_frame, serve_multiplexed, write_frame


ALPN = "veraport/1"


class TLSHotSessionError(RuntimeError):
    code = "TLS_HOT_SESSION_ERROR"


class TLSPolicyError(TLSHotSessionError):
    code = "TLS_POLICY_ERROR"


class SessionRejected(TLSHotSessionError):
    code = "SESSION_REJECTED"


def make_server_context(*, certfile: str | Path, keyfile: str | Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
    context.set_alpn_protocols([ALPN])
    return context


def make_client_context(*, cafile: str | Path) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(cafile))
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols([ALPN])
    return context


def _require_tls_policy(writer: asyncio.StreamWriter) -> None:
    ssl_object = writer.get_extra_info("ssl_object")
    if ssl_object is None:
        raise TLSPolicyError("hot session requires TLS")
    if ssl_object.version() != "TLSv1.3":
        raise TLSPolicyError("hot session requires TLS 1.3")
    if ssl_object.selected_alpn_protocol() != ALPN:
        raise TLSPolicyError("hot session requires veraport/1 ALPN")


def _challenge_frame(challenge: ServerChallenge) -> dict[str, Any]:
    return {"frame_type": "server_challenge", **asdict(challenge)}


def _client_auth_frame(auth: ClientAuth) -> dict[str, Any]:
    return {"frame_type": "client_auth", **asdict(auth)}


def _accept_frame(accept: ServerAccept) -> dict[str, Any]:
    return {"frame_type": "server_accept", **asdict(accept)}


def _parse_challenge(value: dict[str, Any]) -> ServerChallenge:
    if value.pop("frame_type", None) != "server_challenge":
        raise TLSHotSessionError("expected server_challenge")
    return ServerChallenge(**value)


def _parse_client_auth(value: dict[str, Any]) -> ClientAuth:
    if value.pop("frame_type", None) != "client_auth":
        raise TLSHotSessionError("expected client_auth")
    capabilities = value.get("requested_capabilities")
    if isinstance(capabilities, list):
        value["requested_capabilities"] = tuple(capabilities)
    return ClientAuth(**value)


def _parse_accept(value: dict[str, Any]) -> ServerAccept:
    if value.pop("frame_type", None) != "server_accept":
        raise SessionRejected(value.get("message", "session rejected"))
    capabilities = value.get("granted_capabilities")
    if isinstance(capabilities, list):
        value["granted_capabilities"] = tuple(capabilities)
    return ServerAccept(**value)


async def serve_tls_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    authenticator: WorkstationAuthenticator,
    handler_factory: Callable[[SessionBinding], Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]],
    now_ms: Callable[[], int] | None = None,
    session_ttl_ms: int = 300_000,
    max_frame_bytes: int = 1_048_576,
    max_inflight: int = 64,
) -> None:
    clock = now_ms or (lambda: int(time.time() * 1000))
    try:
        _require_tls_policy(writer)
        challenge = authenticator.challenge()
        await write_frame(writer, _challenge_frame(challenge), max_frame_bytes=max_frame_bytes)
        raw_auth = await read_frame(reader, max_frame_bytes=max_frame_bytes)
        client_auth = _parse_client_auth(dict(raw_auth))
        accept, binding = authenticator.accept(
            challenge,
            client_auth,
            now_ms=clock(),
            ttl_ms=session_ttl_ms,
        )
        await write_frame(writer, _accept_frame(accept), max_frame_bytes=max_frame_bytes)
        handler = handler_factory(binding)
        remaining_s = max(
            0.1,
            (binding.expires_at_ms - clock()) / 1000.0,
        )
        try:
            await asyncio.wait_for(
                serve_multiplexed(
                    reader,
                    writer,
                    handler,
                    max_frame_bytes=max_frame_bytes,
                    max_inflight=max_inflight,
                ),
                timeout=remaining_s,
            )
        except TimeoutError:
            pass
        finally:
            close_handler = getattr(handler, "close", None)
            if close_handler is not None:
                result = close_handler()
                if asyncio.iscoroutine(result):
                    await result
        return
    except Exception as exc:
        try:
            await write_frame(
                writer,
                {
                    "frame_type": "session_reject",
                    "code": getattr(exc, "code", exc.__class__.__name__.upper()),
                    "message": str(exc),
                },
                max_frame_bytes=max_frame_bytes,
            )
        except Exception:
            pass
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


async def open_tls_session(
    *,
    host: str,
    port: int,
    ssl_context: ssl.SSLContext,
    server_hostname: str,
    controller_private_key: ec.EllipticCurvePrivateKey,
    workstation_public_key: ec.EllipticCurvePublicKey,
    requested_capabilities: set[str] | frozenset[str],
    now_ms: Callable[[], int] | None = None,
    max_frame_bytes: int = 1_048_576,
    connect_timeout_s: float = 5.0,
    handshake_timeout_s: float = 5.0,
    request_timeout_s: float = 10.0,
) -> tuple[MultiplexClient, SessionBinding]:
    if min(connect_timeout_s, handshake_timeout_s, request_timeout_s) <= 0:
        raise ValueError("transport deadlines must be positive")
    clock = now_ms or (lambda: int(time.time() * 1000))
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            host,
            port,
            ssl=ssl_context,
            server_hostname=server_hostname,
        ),
        timeout=connect_timeout_s,
    )
    try:
        async with asyncio.timeout(handshake_timeout_s):
            _require_tls_policy(writer)
            challenge = _parse_challenge(
                dict(
                    await read_frame(
                        reader,
                        max_frame_bytes=max_frame_bytes,
                    )
                )
            )
            client_auth = ClientAuth.create(
                controller_private_key=controller_private_key,
                workstation_principal=challenge.workstation_principal,
                server_challenge=challenge.challenge,
                requested_capabilities=requested_capabilities,
            )
            await write_frame(
                writer,
                _client_auth_frame(client_auth),
                max_frame_bytes=max_frame_bytes,
            )
            raw_accept = await read_frame(
                reader,
                max_frame_bytes=max_frame_bytes,
            )
            accept = _parse_accept(dict(raw_accept))
            binding = verify_server_accept(
                challenge,
                client_auth,
                accept,
                workstation_public_key=workstation_public_key,
                now_ms=clock(),
            )
        return (
            MultiplexClient(
                reader,
                writer,
                max_frame_bytes=max_frame_bytes,
                request_timeout_s=request_timeout_s,
            ),
            binding,
        )
    except Exception:
        writer.close()
        try:
            await writer.wait_closed()
        finally:
            raise
