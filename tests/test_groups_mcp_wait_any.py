from __future__ import annotations

import pytest

from aegis.groups.models import GroupResult, MemberResult


@pytest.mark.asyncio
async def test_mcp_wait_any_serializes_result():
    from aegis.mcp.server import _aegis_group_wait_any_impl

    canned = GroupResult(
        broadcast_id="br-1",
        by_member={"a": MemberResult("a", "winner", 1, 2, 3, "done")},
        combined="winner", errors={}, timeouts=[],
    )

    class _G:
        async def wait_any(self, group, *, timeout, cancel_losers):
            assert cancel_losers is True
            return canned

    class _B:
        groups = _G()

    out = await _aegis_group_wait_any_impl(
        _B(), group="g", timeout=1.0, cancel_losers=True)
    assert out["by_member"]["a"]["text"] == "winner"
