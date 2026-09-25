from veraport_agent.synchrony import PathMode, PathObservation, select_path


def obs(path_id, mode, *, authenticated=True, healthy=True, observed=1000, rtt=10):
    return PathObservation(path_id, mode, authenticated, healthy, observed, rtt)


def test_direct_wins_over_lower_rtt_relay():
    decision = select_path(
        (
            obs("relay", PathMode.DURABLE_RELAY, rtt=1),
            obs("direct", PathMode.DIRECT_STREAM, rtt=30),
        ),
        now_ms=1001,
    )
    assert decision.selected.path_id == "direct"


def test_edge_fallback_when_direct_unhealthy():
    decision = select_path(
        (
            obs("direct", PathMode.DIRECT_STREAM, healthy=False),
            obs("edge", PathMode.EDGE_STREAM, rtt=20),
            obs("relay", PathMode.DURABLE_RELAY, rtt=5),
        ),
        now_ms=1001,
    )
    assert decision.selected.path_id == "edge"
    assert ("direct", "UNHEALTHY") in decision.rejected


def test_relay_fallback_when_hot_paths_absent():
    decision = select_path(
        (obs("relay", PathMode.DURABLE_RELAY, rtt=None),),
        now_ms=1001,
    )
    assert decision.selected.mode is PathMode.DURABLE_RELAY


def test_unauthenticated_direct_never_beats_authenticated_edge():
    decision = select_path(
        (
            obs("direct", PathMode.DIRECT_STREAM, authenticated=False, rtt=1),
            obs("edge", PathMode.EDGE_STREAM, rtt=40),
        ),
        now_ms=1001,
    )
    assert decision.selected.path_id == "edge"


def test_stale_path_is_not_selected():
    decision = select_path(
        (obs("direct", PathMode.DIRECT_STREAM, observed=1),),
        now_ms=10000,
        max_age_ms=100,
    )
    assert decision.selected is None
    assert decision.rejected == (("direct", "STALE"),)


def test_same_mode_prefers_lower_rtt():
    decision = select_path(
        (
            obs("a", PathMode.DIRECT_STREAM, rtt=30),
            obs("b", PathMode.DIRECT_STREAM, rtt=12),
        ),
        now_ms=1001,
    )
    assert decision.selected.path_id == "b"
