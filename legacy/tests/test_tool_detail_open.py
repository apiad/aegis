"""The click gesture: a tool block opens its call, live or replayed, and
n/p walk the turn's calls without closing the window.
"""

import pytest

from aegis.events import ToolResult, ToolUse
from aegis.tui.pane import CopyableBlock
from aegis.tui.tool_detail import ToolDetailScreen


def _use(tid, cmd):
    return ToolUse(
        name="Bash",
        summary="",
        kind="execute",
        raw_input={"command": cmd},
        tool_call_id=tid,
    )


def _land(pane, tid, cmd, out="ok"):
    pane._on_core_event(None, _use(tid, cmd))
    pane._on_core_event(None, ToolResult(text=out, is_error=False, tool_call_id=tid))


@pytest.mark.asyncio
async def test_clicking_a_tool_block_opens_the_window(pane_app):
    async with pane_app() as (pane, pilot):
        _land(pane, "t1", "pytest -q", "3629 passed")
        pane.post_message(CopyableBlock.ToolExpandToggle("t1"))
        await pilot.pause()
        assert isinstance(pane.app.screen, ToolDetailScreen)


@pytest.mark.asyncio
async def test_escape_closes_it(pane_app):
    async with pane_app() as (pane, pilot):
        _land(pane, "t1", "pytest -q")
        pane.post_message(CopyableBlock.ToolExpandToggle("t1"))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(pane.app.screen, ToolDetailScreen)


@pytest.mark.asyncio
async def test_n_steps_to_the_next_call_without_closing(pane_app):
    async with pane_app() as (pane, pilot):
        _land(pane, "t0", "first cmd")
        _land(pane, "t1", "second cmd")
        pane.post_message(CopyableBlock.ToolExpandToggle("t0"))
        await pilot.pause()
        screen = pane.app.screen
        assert screen._use.raw_input["command"] == "first cmd"
        await pilot.press("n")
        await pilot.pause()
        assert pane.app.screen is screen
        assert screen._use.raw_input["command"] == "second cmd"


@pytest.mark.asyncio
async def test_n_at_the_last_call_stays_put(pane_app):
    # The ends are walls, not a wrap — a wrap reads as a glitch.
    async with pane_app() as (pane, pilot):
        _land(pane, "t0", "only cmd")
        pane.post_message(CopyableBlock.ToolExpandToggle("t0"))
        await pilot.pause()
        screen = pane.app.screen
        await pilot.press("n")
        await pilot.pause()
        assert pane.app.screen is screen
        assert screen._use.raw_input["command"] == "only cmd"


@pytest.mark.asyncio
async def test_a_replayed_call_opens_too(pane_app):
    # The path with no live track at all. Before this change a replayed
    # block carried no tool_call_id and the click fell through to copy.
    use = _use("t7", "from the log")
    res = ToolResult(text="ok", is_error=False, tool_call_id="t7")
    async with pane_app(events=[use, res]) as (pane, pilot):
        pane.post_message(CopyableBlock.ToolExpandToggle("t7"))
        await pilot.pause()
        assert isinstance(pane.app.screen, ToolDetailScreen)
        assert pane.app.screen._use.raw_input["command"] == "from the log"
