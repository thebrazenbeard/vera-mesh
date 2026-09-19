from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class PathMode(IntEnum):
    DIRECT_STREAM = 0
    EDGE_STREAM = 1
    DURABLE_RELAY = 2


@dataclass(frozen=True)
class PathObservation:
    path_id: str
    mode: PathMode
    authenticated: bool
    healthy: bool
    observed_at_ms: int
    rtt_ms: float | None = None

    def __post_init__(self) -> None:
        if not self.path_id:
            raise ValueError("path_id is required")
        if self.observed_at_ms < 0:
            raise ValueError("observed_at_ms must be non-negative")
        if self.rtt_ms is not None and self.rtt_ms < 0:
            raise ValueError("rtt_ms must be non-negative")


@dataclass(frozen=True)
class PathDecision:
    selected: PathObservation | None
    rejected: tuple[tuple[str, str], ...]


def select_path(
    observations: tuple[PathObservation, ...],
    *,
    now_ms: int,
    max_age_ms: int = 5_000,
) -> PathDecision:
    """Choose the most synchronous current authenticated path.

    Mode priority is semantic: direct hot stream, edge-proxied hot stream,
    durable relay. RTT only breaks ties inside one mode so a marginally lower
    relay RTT can never displace a live direct stream.
    """
    if now_ms < 0 or max_age_ms < 0:
        raise ValueError("time values must be non-negative")

    eligible: list[PathObservation] = []
    rejected: list[tuple[str, str]] = []
    for item in observations:
        if item.observed_at_ms > now_ms:
            rejected.append((item.path_id, "CLOCK_FUTURE"))
            continue
        if now_ms - item.observed_at_ms > max_age_ms:
            rejected.append((item.path_id, "STALE"))
            continue
        if not item.authenticated:
            rejected.append((item.path_id, "UNAUTHENTICATED"))
            continue
        if not item.healthy:
            rejected.append((item.path_id, "UNHEALTHY"))
            continue
        eligible.append(item)

    if not eligible:
        return PathDecision(None, tuple(rejected))

    selected = min(
        eligible,
        key=lambda item: (
            int(item.mode),
            float("inf") if item.rtt_ms is None else item.rtt_ms,
            item.path_id,
        ),
    )
    return PathDecision(selected, tuple(rejected))
