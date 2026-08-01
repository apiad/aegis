"""Reordering open tabs — ctrl+shift+arrows and mouse drag.

Tab order *is* ``app._panes`` order, so every reorder is one list splice
plus a tab-bar repaint; the roster snapshot follows for free because it
writes ``order`` from the list index.
"""
import pytest
from textual.events import MouseMove

from aegis.state.workspace import load as load_workspace
from aegis.tui.widgets import TabBar

from tests.test_tui import FakeSession, _app, _factory


def _three_tab_app():
    return _app(_factory(*[FakeSession() for _ in range(3)]))


async def _open_three(pilot):
    for _ in range(2):
        await pilot.press("ctrl+t")
        await pilot.pause()


def _handles(app):
    return [p.handle for p in app._panes]


@pytest.mark.asyncio
async def test_ctrl_shift_right_moves_the_active_tab_one_slot():
    app = _three_tab_app()
    async with app.run_test() as pilot:
        await _open_three(pilot)
        before = _handles(app)
        await pilot.press("ctrl+1")          # activate the first tab
        await pilot.pause()
        await pilot.press("ctrl+shift+right")
        await pilot.pause()
        assert _handles(app) == [before[1], before[0], before[2]]
        # The moved tab travels with you — it stays active.
        assert app._active.handle == before[0]


@pytest.mark.asyncio
async def test_ctrl_shift_left_moves_the_active_tab_back():
    app = _three_tab_app()
    async with app.run_test() as pilot:
        await _open_three(pilot)
        before = _handles(app)
        await pilot.press("ctrl+3")
        await pilot.pause()
        await pilot.press("ctrl+shift+left")
        await pilot.pause()
        assert _handles(app) == [before[0], before[2], before[1]]
        assert app._active.handle == before[2]


@pytest.mark.asyncio
async def test_moving_past_an_edge_is_a_noop():
    """Clamp, don't wrap — a tab teleporting to the far end reads as a bug."""
    app = _three_tab_app()
    async with app.run_test() as pilot:
        await _open_three(pilot)
        before = _handles(app)
        await pilot.press("ctrl+1")
        await pilot.pause()
        await pilot.press("ctrl+shift+left")
        await pilot.pause()
        assert _handles(app) == before
        await pilot.press("ctrl+3")
        await pilot.pause()
        await pilot.press("ctrl+shift+right")
        await pilot.pause()
        assert _handles(app) == before


@pytest.mark.asyncio
async def test_reorder_renumbers_the_tab_bar():
    app = _three_tab_app()
    async with app.run_test(size=(160, 24)) as pilot:
        await _open_three(pilot)
        before = _handles(app)
        await pilot.press("ctrl+1")
        await pilot.pause()
        await pilot.press("ctrl+shift+right")
        await pilot.pause()
        cells = app.query_one(TabBar)._cells
        assert before[1] in str(cells[0].content)
        assert before[0] in str(cells[1].content)
        assert " 1 " in str(cells[0].content)
        assert " 2 " in str(cells[1].content)


@pytest.mark.asyncio
async def test_reorder_persists_to_the_workspace_snapshot():
    app = _three_tab_app()
    async with app.run_test() as pilot:
        await _open_three(pilot)
        before = _handles(app)
        await pilot.press("ctrl+1")
        await pilot.pause()
        await pilot.press("ctrl+shift+right")
        await pilot.pause()
        app._write_snapshot()
        ws = load_workspace(app._state_dir)
    ordered = [t.handle for t in sorted(ws.tabs, key=lambda t: t.order)]
    assert ordered == [before[1], before[0], before[2]]


# --- mouse drag ---

async def _drag(pilot, bar, src: int, dst: int) -> None:
    """Press on cell ``src``, move over cell ``dst``, release there."""
    await pilot.mouse_down(bar._cells[src])
    await pilot._post_mouse_events([MouseMove], widget=bar._cells[dst],
                                   button=1)
    await pilot.mouse_up(bar._cells[dst])
    await pilot.pause()


@pytest.mark.asyncio
async def test_dragging_a_tab_onto_a_later_one_reorders():
    app = _three_tab_app()
    async with app.run_test(size=(160, 24)) as pilot:
        await _open_three(pilot)
        before = _handles(app)
        bar = app.query_one(TabBar)
        await _drag(pilot, bar, 0, 2)
        assert _handles(app) == [before[1], before[2], before[0]]


@pytest.mark.asyncio
async def test_dragging_a_tab_backwards_reorders():
    app = _three_tab_app()
    async with app.run_test(size=(160, 24)) as pilot:
        await _open_three(pilot)
        before = _handles(app)
        bar = app.query_one(TabBar)
        await _drag(pilot, bar, 2, 0)
        assert _handles(app) == [before[2], before[0], before[1]]


@pytest.mark.asyncio
async def test_a_click_without_movement_still_activates_and_keeps_order():
    """The drag gesture rides on top of click-to-select; it must not eat it."""
    app = _three_tab_app()
    async with app.run_test(size=(160, 24)) as pilot:
        await _open_three(pilot)
        before = _handles(app)
        assert app._active.handle == before[2]
        bar = app.query_one(TabBar)
        await pilot.click(bar._cells[0])
        await pilot.pause()
        assert _handles(app) == before
        assert app._active.handle == before[0]


@pytest.mark.asyncio
async def test_hovering_after_a_release_does_not_reorder():
    """A stale drag origin must not turn a later plain hover into a move."""
    app = _three_tab_app()
    async with app.run_test(size=(160, 24)) as pilot:
        await _open_three(pilot)
        before = _handles(app)
        bar = app.query_one(TabBar)
        await pilot.mouse_down(bar._cells[0])
        await pilot.mouse_up(bar._cells[0])
        await pilot.hover(bar._cells[2])
        await pilot.pause()
        assert _handles(app) == before


def _sgr(buttons: int, x: int, y: int, state: str = "M") -> str:
    """One SGR (1006) mouse report, as a real terminal emits it."""
    return f"\x1b[<{buttons};{x + 1};{y + 1}{state}"


@pytest.mark.asyncio
async def test_real_terminal_mouse_bytes_drag_a_tab():
    """The gesture, driven from the wire rather than from synthetic events.

    A drag is reported as button 32 (motion + button-1 held), and Textual
    turns that into a ``MouseMove`` whose ``button`` is 1 — the literal the
    drag gate reads. Asserting it from the bytes keeps that from being my
    own assumption played back at me.
    """
    from textual._xterm_parser import XTermParser

    app = _three_tab_app()
    async with app.run_test(size=(160, 24)) as pilot:
        await _open_three(pilot)
        before = _handles(app)
        bar = app.query_one(TabBar)
        parser = XTermParser()

        async def wire(buttons: int, cell: int, state: str = "M") -> None:
            region = bar._cells[cell].region       # re-read: widths shift
            for event in parser.feed(
                    _sgr(buttons, region.x, region.y, state)):
                app.screen._forward_event(event)
            await pilot.pause()

        await wire(0, 0)              # press on tab 1
        await wire(32, 1)             # drag across tab 2
        await wire(32, 2)             # drag across tab 3
        await wire(0, 2, "m")         # release
        assert _handles(app) == [before[1], before[2], before[0]]

        # And a button-less motion report afterwards is just a hover.
        await wire(35, 0)
        assert _handles(app) == [before[1], before[2], before[0]]


@pytest.mark.asyncio
async def test_dragging_the_empty_tabbar_placeholder_is_a_noop():
    app = _app()
    async with app.run_test() as pilot:
        bar = app.query_one(TabBar)
        bar.set_tabs([])                 # "no tabs" placeholder
        await pilot.pause()
        await _drag(pilot, bar, 0, 0)
        assert len(app._panes) == 1
