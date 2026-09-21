import pytest

from veraport_agent.mcp_server import require_loopback_mcp_host


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_initial_mcp_bind_accepts_loopback_only(host):
    assert require_loopback_mcp_host(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5", "example.com"])
def test_initial_mcp_bind_rejects_non_loopback(host):
    with pytest.raises(ValueError, match="loopback"):
        require_loopback_mcp_host(host)
