import pytest

from aegis.locks.bridge import make_locks_bridge
from aegis.mcp.server import build_server
from tests.test_mcp_server import FakeBridge, _call


def _bridge_with_locks(tmp_path):
    br = FakeBridge()
    live = {"lucid-knuth", "civic-codd"}
    br.locks = make_locks_bridge(live_handles=lambda: set(live),
                                 root_fn=lambda: tmp_path)
    return br


@pytest.mark.asyncio
async def test_claim_shared_then_exclusive_conflict(tmp_path):
    br = _bridge_with_locks(tmp_path)
    srv = build_server(br)
    a = await _call(srv, "aegis_claim", paths=["src/tui/"],
                    from_handle="lucid-knuth", intent="exclusive")
    assert a["granted"] is True and a["overlaps"] == []
    b = await _call(srv, "aegis_claim", paths=["src/tui/app.py"],
                    from_handle="civic-codd", intent="shared")
    assert b["granted"] is False
    assert b["overlaps"][0]["handle"] == "lucid-knuth"
    assert b["overlaps"][0]["intent"] == "exclusive"


@pytest.mark.asyncio
async def test_release_and_board(tmp_path):
    br = _bridge_with_locks(tmp_path)
    srv = build_server(br)
    a = await _call(srv, "aegis_claim", paths=["src/x/"],
                    from_handle="lucid-knuth")
    board = await _call(srv, "aegis_claims")
    assert [c["handle"] for c in board] == ["lucid-knuth"]
    out = await _call(srv, "aegis_release",
                      claim_id=a["claim_id"], from_handle="lucid-knuth")
    assert out == {"released": True}
    assert await _call(srv, "aegis_claims") == []
