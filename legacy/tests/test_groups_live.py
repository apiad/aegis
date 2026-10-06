"""Live smoke: 3 real claude-code workers in one group, one broadcast,
wait_all collects all three. Exercises the full path:
SessionManager.spawn → InboxRouter.bind → broadcast → real worker turn
→ Result event → bus → wait_all → GroupResult."""
from __future__ import annotations

import asyncio
import shutil

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(shutil.which("claude") is None,
                       reason="claude CLI not on PATH; live test skipped"),
]


@pytest.mark.asyncio
async def test_three_member_broadcast_wait_all(live_session_manager):
    sm = live_session_manager
    await sm.groups.spawn(profile="default", group="rev", handle="ada")
    await sm.groups.spawn(profile="default", group="rev", handle="lucid")
    await sm.groups.spawn(profile="default", group="rev", handle="wry")

    bid = await sm.groups.broadcast(
        "rev", sender="agent:host",
        objective="Reply with exactly one word: HEARD.",
        output_format="one word",
        tool_guidance="No tools needed.",
        boundaries="One turn only.",
    )
    assert bid

    result = await asyncio.wait_for(
        sm.groups.wait_all("rev", timeout=120.0, reducer="concat"),
        timeout=130.0,
    )
    assert set(result.by_member) == {"ada", "lucid", "wry"}
    for mr in result.by_member.values():
        assert "HEARD" in mr.text.upper()
    assert result.timeouts == []
