"""The screen's behaviour, not its pixels. The pixels are Task 7 and 8."""

import pytest

from aegis.fleet.models import CardView, FleetSnapshot
from aegis.tui.fleet_screen import FleetScreen

SNAP = FleetSnapshot(
    cards=tuple(CardView(handle=f"s{i}", tab_index=i + 1) for i in range(9))
)


def test_the_selection_starts_on_the_first_card():
    assert FleetScreen(lambda: SNAP).selected == 1


def test_arrows_move_the_selection_and_stop_at_the_ends():
    scr = FleetScreen(lambda: SNAP)
    scr.action_move(1)
    assert scr.selected == 2
    scr.action_move(-1)
    scr.action_move(-1)
    assert scr.selected == 1, "moving left off the first card must not wrap to the last"


def test_a_number_key_selects_that_card():
    scr = FleetScreen(lambda: SNAP)
    scr.action_pick(4)
    assert scr.chosen == 4


def test_a_number_beyond_the_fleet_is_inert():
    scr = FleetScreen(lambda: FleetSnapshot(cards=(CardView(handle="a", tab_index=1),)))
    scr.action_pick(7)
    assert scr.chosen is None


def test_a_ghost_card_cannot_be_opened():
    """An ephemeral session that died has no tab left to switch to."""
    snap = FleetSnapshot(
        cards=(
            CardView(handle="a", tab_index=1),
            CardView(handle="dead", tab_index=0, ghost_since=100.0),
        )
    )
    scr = FleetScreen(lambda: snap)
    scr.action_move(1)
    scr.action_open()
    assert scr.chosen is None


# --- beyond the brief: the layout a click reads, and the tab a card names ---

from aegis.fleet.render import CARD_WIDTH, GUTTER, render_fleet  # noqa: E402
from aegis.tui.fleet_screen import card_rects, in_tab_order  # noqa: E402
from aegis.tui.themes import INK, aegis_colors  # noqa: E402

PAL = aegis_colors(INK)


def test_every_rect_sits_on_its_own_cards_top_border():
    """card_at is only right if it agrees with render_fleet's real layout:
    the band on top, rows padded to their tallest card, a blank between."""
    cards = tuple(
        CardView(
            handle=f"h{i}", tab_index=i + 1, did="x" if i % 2 else "", title="t" * i
        )
        for i in range(5)
    )
    snap = FleetSnapshot(cards=cards)
    width = 2 * (CARD_WIDTH + GUTTER)
    lines = render_fleet(snap, PAL, width).plain.split("\n")
    rects = card_rects(snap, PAL, width)
    assert len(rects) == 5
    for card, (x, y, w, h) in zip(cards, rects):
        top = lines[y][x : x + w]
        assert top.startswith("┌") and top.endswith("┐"), (card.handle, top)
        assert card.handle in top
        assert lines[y + h - 1][x : x + w].startswith("└")
    # Row two starts one blank line below row one's tallest card.
    assert rects[2][1] == rects[0][1] + max(rects[0][3], rects[1][3]) + 1


def test_card_at_maps_a_cell_to_its_card_and_the_gutter_to_none():
    scr = FleetScreen(lambda: SNAP)
    scr._rects = [(0, 4, 46, 5), (48, 4, 46, 5)]
    assert scr.card_at(0, 4) == 1
    assert scr.card_at(47, 5) is None, "the gutter belongs to no card"
    assert scr.card_at(50, 8) == 2
    assert scr.card_at(50, 9) is None
    assert scr.card_at(10, 0) is None, "the band is not a card"


def test_cards_follow_the_tab_bar_not_the_brains_list():
    """The brain's session list and the view's tab bar are separate lists:
    terminal tabs sit only in the second, and a moved tab moves only there."""
    snap = FleetSnapshot(
        cards=(
            CardView(handle="a", tab_index=1),
            CardView(handle="b", tab_index=2),
            CardView(handle="mounting", tab_index=3),
            CardView(handle="dead", ghost_since=1.0),
        )
    )
    out = in_tab_order(snap, {"b": 1, "a": 3})
    assert [(c.handle, c.tab_index) for c in out.cards] == [
        ("b", 1),
        ("a", 3),
        ("mounting", 0),
        ("dead", 0),
    ]


def test_a_number_key_opens_the_tab_the_card_shows():
    """Card 2 of the grid can be tab 3 when a terminal sits at tab 2."""
    snap = FleetSnapshot(
        cards=(CardView(handle="a", tab_index=1), CardView(handle="b", tab_index=3))
    )
    scr = FleetScreen(lambda: snap)
    scr.action_pick(2)
    assert scr.chosen is None
    scr.action_pick(3)
    assert scr.chosen == 3 and scr.selected == 2


# --- mounted: F10 in both shapes the app runs in ---

from aegis.config import Agent  # noqa: E402
from aegis.config.roots import AegisRoots  # noqa: E402
from aegis.tui.app import AegisApp  # noqa: E402

from tests.brain import make_brain  # noqa: E402


class _Harness:
    session_id = None

    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


class _MCP:
    url = "http://127.0.0.1:0/mcp/"
    port = 0

    def bind(self, bridge): ...
    async def start(self): ...
    async def stop(self): ...


def _agent():
    return Agent(harness="claude-code", model="opus", effort="high", permission="auto")


def _standalone(tmp_path):
    return AegisApp(
        {"default": _agent()},
        "default",
        lambda *a, **k: _Harness(),
        _MCP(),
        clean=True,
        cwd=str(tmp_path),
    )


def _bridged(tmp_path):
    roster = {"default": _agent()}
    brain = make_brain(
        roster,
        "default",
        make_session=lambda *a, **k: _Harness(),
        mcp=None,
        roots=AegisRoots.for_project(tmp_path),
    )
    return AegisApp(
        mcp=_MCP(),
        agents=roster,
        default_agent="default",
        make_session=lambda *a, **k: _Harness(),
        queues={},
        clean=True,
        drivers={},
        cwd=str(tmp_path),
        voice=None,
        bridge=brain,
    )


async def _two_tabs(app, pilot):
    await pilot.pause()
    await app._spawn("default")
    for _ in range(20):
        await pilot.pause()
        if len(app._panes) == 2:
            return
    raise AssertionError(f"expected two tabs, have {len(app._panes)}")


@pytest.mark.parametrize(
    "shape", [_standalone, _bridged], ids=["standalone", "bridged"]
)
async def test_f10_opens_the_fleet_and_a_number_opens_that_tab(tmp_path, shape):
    app = shape(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _two_tabs(app, pilot)
        if shape is _bridged:
            assert app.manager is not None
        app._activate(0)
        await pilot.press("f10")
        await pilot.pause()
        assert isinstance(app.screen, FleetScreen)
        handles = [c.handle for c in app.screen._current.cards]
        assert handles == [p.handle for p in app._panes]
        await pilot.press("2")
        await pilot.pause()
        assert not isinstance(app.screen, FleetScreen)
        assert app._active is app._panes[1]


@pytest.mark.parametrize(
    "shape", [_standalone, _bridged], ids=["standalone", "bridged"]
)
async def test_a_moved_tab_is_still_opened_by_its_card(tmp_path, shape):
    """A reorder moves _panes and not the brain's _sessions. A card mapped
    by list index would open the other tab."""
    app = shape(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _two_tabs(app, pilot)
        first = app._panes[0]
        app._move_pane(0, 1)
        app._activate(0)
        await pilot.press("f10")
        await pilot.pause()
        card = next(c for c in app.screen._current.cards if c.handle == first.handle)
        assert card.tab_index == 2
        x, y, _w, _h = app.screen._rects[1]
        await pilot.click("#fleet-grid", offset=(x + 3, y + 1))
        await pilot.pause()
        assert not isinstance(app.screen, FleetScreen)
        assert app._active is first


@pytest.mark.parametrize("key", ["escape", "f10"])
async def test_escape_and_f10_close_the_fleet(tmp_path, key):
    app = _standalone(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f10")
        await pilot.pause()
        assert isinstance(app.screen, FleetScreen)
        await pilot.press(key)
        await pilot.pause()
        assert not isinstance(app.screen, FleetScreen)


async def test_a_burst_of_events_redraws_once_and_late(tmp_path):
    """Nine streaming sessions must not redraw the grid per event."""
    from aegis.events import ToolUse

    app = _standalone(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("f10")
        await pilot.pause()
        scr = app.screen
        calls = []
        draw = scr.refresh_fleet
        scr.refresh_fleet = lambda: (calls.append(1), draw())
        scr.refresh_fleet()
        calls.clear()
        core = app._panes[0]._core
        for i in range(10):
            core._fire_event(
                ToolUse(
                    name="Bash", summary=f"step {i}", raw_input={}, tool_call_id=f"t{i}"
                )
            )
        assert calls == [], "an event inside the window must wait for it to close"
        assert scr._pending is not None, "the session's event must reach the screen"
        await pilot.pause(0.7)
        assert calls == [1]
