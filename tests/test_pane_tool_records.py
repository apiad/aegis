"""The transcript record — not the live track — is what the detail window
reads. A call that scrolled out of the mounted window, or one replayed
from disk on resume, must resolve exactly like one that just landed.
"""

import pytest

from aegis.events import ToolResult, ToolUse


def _use(tid="t1"):
    return ToolUse(
        name="Bash",
        summary="",
        kind="execute",
        raw_input={"description": "run tests", "command": "pytest -q"},
        tool_call_id=tid,
    )


def _res(tid="t1", text="3629 passed"):
    return ToolResult(text=text, is_error=False, tool_call_id=tid)


@pytest.mark.asyncio
async def test_a_live_call_resolves_to_a_record_holding_both_events(pane_app):
    async with pane_app() as (pane, pilot):
        pane._on_core_event(None, _use())
        pane._on_core_event(None, _res())
        await pilot.pause()
        rec = pane.tool_record("t1")
        assert rec is not None
        assert [type(e).__name__ for e in rec.events] == ["ToolUse", "ToolResult"]
        assert rec.events[1].text == "3629 passed"


@pytest.mark.asyncio
async def test_a_replayed_call_resolves_the_same_way(pane_app):
    # On resume the pane rebuilds _history off the log. Before this change
    # the replayed record carried no tool_call_id, so its block fell
    # through to copy-on-click and the call could not be opened at all.
    async with pane_app(events=[_use("t9"), _res("t9", "ok from disk")]) as (pane, _):
        rec = pane.tool_record("t9")
        assert rec is not None
        assert rec.tool_call_id == "t9"
        assert rec.events[1].text == "ok from disk"


@pytest.mark.asyncio
async def test_an_evicted_call_still_resolves(pane_app):
    # Eviction moves _window_start and unmounts widgets; it never drops a
    # record. A call from 400 blocks ago must still open.
    async with pane_app() as (pane, pilot):
        for i in range(400):
            pane._on_core_event(None, _use(f"t{i}"))
            pane._on_core_event(None, _res(f"t{i}"))
        await pilot.pause()
        assert pane._window_start > 0
        assert pane.tool_record("t0") is not None


@pytest.mark.asyncio
async def test_the_result_does_not_get_its_own_block(pane_app):
    async with pane_app() as (pane, pilot):
        before = len(pane._history)
        pane._on_core_event(None, _use())
        pane._on_core_event(None, _res())
        await pilot.pause()
        assert len(pane._history) == before + 1
