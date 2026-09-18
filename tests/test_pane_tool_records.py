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
async def test_an_evicted_call_still_resolves(pane_app, monkeypatch):
    # Eviction moves _window_start and unmounts widgets; it never drops a
    # record, so the first call still opens once it is long gone from the
    # screen. The window is shrunk rather than filled with 300 real blocks:
    # this is about the mechanism, and paying two seconds to reach a
    # threshold would make it the suite's slowest test for one assertion.
    import aegis.tui.pane as pane_mod

    monkeypatch.setattr(pane_mod, "N_MAX", 20)
    monkeypatch.setattr(pane_mod, "EVICT_BATCH", 10)
    async with pane_app() as (pane, pilot):
        for i in range(40):
            pane._on_core_event(None, _use(f"t{i}"))
            pane._on_core_event(None, _res(f"t{i}"))
        await pilot.pause()
        assert pane._window_start > 0, "nothing was evicted — the test proves nothing"
        assert pane.tool_record("t0") is not None


@pytest.mark.asyncio
async def test_the_result_does_not_get_its_own_block(pane_app):
    async with pane_app() as (pane, pilot):
        before = len(pane._history)
        pane._on_core_event(None, _use())
        pane._on_core_event(None, _res())
        await pilot.pause()
        assert len(pane._history) == before + 1


def _screen_rows(app) -> list[str]:
    """What the compositor actually paints, row by row.

    Console.print of one renderable is not this: it tolerates a Text with
    end="", where Textual gives the block no height and paints nothing, and
    it is handed a width where the real block gets less. Both of those
    shipped past the unit tests and were caught by driving the app.
    """
    return [
        "".join(seg.text for seg in strip).rstrip()
        for strip in app.screen._compositor.render_strips()
    ]


@pytest.mark.asyncio
async def test_the_row_is_painted_and_is_one_row(pane_app):
    async with pane_app() as (pane, pilot):
        pane._on_core_event(None, _use("t1"))
        pane._on_core_event(None, _res("t1", "3629 passed"))
        await pilot.pause()
        rows = _screen_rows(pane.app)
        hits = [r for r in rows if "run tests" in r]
        assert hits, f"the tool row was never painted: {rows}"
        assert len(hits) == 1, f"the row was painted more than once: {hits}"
        # Label, digest and stamp all on that one row, stamp last.
        assert "3629 passed" in hits[0]
        assert hits[0].rstrip().endswith("s"), hits[0]


@pytest.mark.asyncio
async def test_the_stamp_does_not_wrap_at_a_narrow_width(pane_app):
    # The stamp used to be padded to the TRANSCRIPT's width, which is wider
    # than the width a block gets — so it landed past the edge and wrapped
    # onto a row of its own. Narrow is where that shows first.
    async with pane_app() as (pane, pilot):
        pane.app.screen.styles.width = 56
        await pilot.pause()
        pane._on_core_event(None, _use("t1"))
        pane._on_core_event(None, _res("t1", "3629 passed"))
        await pilot.pause()
        rows = _screen_rows(pane.app)
        hits = [r for r in rows if "run tests" in r]
        assert len(hits) == 1, f"the row did not survive a narrow width: {rows}"
        assert hits[0].rstrip().endswith("s"), (
            f"the elapsed stamp left its row: {hits[0]!r}"
        )
