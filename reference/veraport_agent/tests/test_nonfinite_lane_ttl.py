import math

import pytest

from veraport_agent.core import LaneRegistry


@pytest.mark.parametrize("ttl", [math.nan, math.inf, -math.inf])
def test_nonfinite_open_ttl_cannot_create_immortal_lane(ttl):
    registry = LaneRegistry({"fs.read"})
    with pytest.raises(ValueError, match="ttl_s"):
        registry.open_lane(
            lane_id="lane", task_id="task",
            capabilities={"fs.read"}, ttl_s=ttl, now=100,
        )
    assert registry.snapshot(now=100) == ()


@pytest.mark.parametrize("ttl", [math.nan, math.inf, -math.inf])
def test_nonfinite_renew_ttl_cannot_replace_finite_expiry(ttl):
    registry = LaneRegistry({"fs.read"})
    lane = registry.open_lane(
        lane_id="lane", task_id="task", capabilities={"fs.read"},
        ttl_s=5, now=100,
    )
    with pytest.raises(ValueError, match="ttl_s"):
        registry.renew(lane.lane_id, lane.fencing_token, ttl_s=ttl, now=101)
    assert registry.snapshot(now=102)[0].expires_at == 105
