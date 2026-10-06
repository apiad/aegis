from __future__ import annotations

import pytest

from aegis.groups.models import GroupResult, MemberResult


@pytest.mark.asyncio
async def test_wait_all_returns_serializable_dict():
    from aegis.mcp.server import _aegis_group_wait_all_impl

    canned = GroupResult(
        broadcast_id="br-1",
        by_member={"a": MemberResult("a", "x", 0, 0, 0, "done")},
        combined="x",
        errors={},
        timeouts=[],
    )

    class _G:
        async def wait_all(self, group, *, timeout, reducer):
            return canned

    class _B:
        groups = _G()

    out = await _aegis_group_wait_all_impl(_B(), group="rev",
                                            timeout=1.0, reducer="concat")
    assert out["broadcast_id"] == "br-1"
    assert out["by_member"]["a"]["status"] == "done"
    assert out["combined"] == "x"
    assert out["errors"] == {}
    assert out["timeouts"] == []
