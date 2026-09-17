"""The screen's behaviour, not its pixels. The pixels are Task 7 and 8."""

import time

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
    assert scr.chosen == "s3"


def test_a_number_beyond_the_fleet_is_inert():
    scr = FleetScreen(lambda: FleetSnapshot(cards=(CardView(handle="a", tab_index=1),)))
    scr.action_pick(7)
    assert scr.chosen is None


def test_up_and_down_move_the_detail_and_stop_auto():
    scr = FleetScreen(lambda: SNAP)
    assert scr.rotator.is_auto(0.0)
    scr.action_move(1)
    assert scr.selected == 2
    assert not scr.rotator.is_auto(time.monotonic())


def test_select_handle_moves_the_selection():
    scr = FleetScreen(lambda: SNAP)
    scr._current = SNAP
    scr.select_handle("s4")
    assert scr.selected == 5


def test_the_frame_advances_without_rebuilding_the_snapshot():
    built = []

    def snap(**_kw):
        built.append(1)
        return SNAP

    scr = FleetScreen(snap)
    scr._current = SNAP
    scr.advance_frame()
    scr.advance_frame()
    assert scr.frame == 2 and built == []


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


# --- beyond the brief: the tab a card names ---

from aegis.tui.fleet_screen import _Item, in_tab_order  # noqa: E402


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
    assert scr.chosen == "b" and scr.selected == 2


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
        # Take the selection off the rotator, so the first click selects.
        await pilot.press("up")
        await pilot.pause()
        items = list(app.screen.query_one("#fleet-list").query(_Item))
        assert [i.handle for i in items][1] == card.handle
        await pilot.click(items[1])
        await pilot.pause()
        assert isinstance(app.screen, FleetScreen), "the first click only selects"
        assert app.screen._current.cards[app.screen.selected - 1].handle == card.handle
        await pilot.click(items[1])
        await pilot.pause()
        assert not isinstance(app.screen, FleetScreen)
        assert app._active.handle == card.handle
        assert app._active is first


async def test_down_moves_the_detail_and_enter_opens_that_session(tmp_path):
    app = _standalone(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await _two_tabs(app, pilot)
        app._activate(0)
        await pilot.press("f10")
        await pilot.pause()
        second = app._panes[1]
        await pilot.press("down")
        await pilot.pause()
        body = app.screen.query_one("#fleet-detail-body")
        assert body.render().plain.startswith(second.handle)
        assert "auto" not in app.screen.query_one("#fleet-footer").render().plain
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, FleetScreen)
        assert app._active is second


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


def test_the_selection_follows_its_session_across_a_reorder():
    """A refresh that inserts a card ahead of the selection must not move
    the outline onto a different session."""
    fleet = [CardView(handle=h, tab_index=i) for i, h in enumerate("abc", start=1)]
    scr = FleetScreen(lambda **_: FleetSnapshot(cards=tuple(fleet)))
    scr.refresh_fleet()
    scr.action_move(2)
    assert scr._current.cards[scr.selected - 1].handle == "c"
    fleet.insert(1, CardView(handle="new", tab_index=2))
    scr.refresh_fleet()
    assert scr._current.cards[scr.selected - 1].handle == "c"


def test_auto_mode_starts_on_what_it_picks_not_past_the_first_item():
    """The default first item was never on screen, so the rotator must not
    treat it as shown and skip to the next one."""
    fleet = tuple(CardView(handle=h, tab_index=i) for i, h in enumerate("ab", start=1))
    scr = FleetScreen(lambda **_: FleetSnapshot(cards=fleet))
    scr.refresh_fleet()
    assert scr.selected == 1


async def _three_tabs(app, pilot):
    await _two_tabs(app, pilot)
    await app._spawn("default")
    for _ in range(20):
        await pilot.pause()
        if len(app._panes) == 3:
            return
    raise AssertionError(f"expected three tabs, have {len(app._panes)}")


@pytest.mark.parametrize(
    "shape", [_standalone, _bridged], ids=["standalone", "bridged"]
)
async def test_a_card_opens_its_session_after_an_earlier_tab_closed(tmp_path, shape):
    """The grid can be up to a second old. A tab closed in that window
    shifts every later tab down by one, so the card must name its session,
    not the number it was drawn with."""
    app = shape(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _three_tabs(app, pilot)
        app._activate(1)
        await pilot.press("f10")
        await pilot.pause()
        scr = app.screen
        last = scr._current.cards[2]
        assert last.tab_index == 3
        await app._close_pane(app._panes[0])
        # Stale on purpose: the grid still shows the closed tab.
        assert len(scr._current.cards) == 3
        await pilot.press("3")
        await pilot.pause()
        assert not isinstance(app.screen, FleetScreen)
        assert app._active is not None and app._active.handle == last.handle


async def test_a_card_whose_session_is_gone_opens_nothing(tmp_path):
    app = _standalone(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _two_tabs(app, pilot)
        app._activate(0)
        await pilot.press("f10")
        await pilot.pause()
        gone = app._panes[1]
        await app._close_pane(gone)
        await pilot.press("2")
        await pilot.pause()
        assert not isinstance(app.screen, FleetScreen)
        assert app._active is app._panes[0]


def _observers(app):
    return [len(p._core._extra_event_observers) for p in app._panes]


@pytest.mark.parametrize(
    "shape", [_standalone, _bridged], ids=["standalone", "bridged"]
)
async def test_closing_the_fleet_removes_its_event_observers(tmp_path, shape):
    """A session outlives any one screen, and in the daemon any one view:
    every observer the screen added must leave with it, however often F10
    opens."""
    app = shape(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _two_tabs(app, pilot)
        base = _observers(app)
        for _ in range(2):
            await pilot.press("f10")
            await pilot.pause()
            assert isinstance(app.screen, FleetScreen)
            assert _observers(app) == [n + 1 for n in base]
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, FleetScreen)
            assert _observers(app) == base


async def test_a_view_that_exits_with_the_fleet_open_leaves_no_observer(tmp_path):
    """The brain keeps its sessions after a view detaches. The pane removes
    its own observer on the way out, and the fleet must remove its own too."""
    app = _bridged(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _two_tabs(app, pilot)
        cores = [p._core for p in app._panes]
        await pilot.press("f10")
        await pilot.pause()
        assert isinstance(app.screen, FleetScreen)
    assert [c._extra_event_observers for c in cores] == [[], []]


def test_the_band_carries_the_system_row_it_is_handed():
    """F10 hides F3, so the band shows the tiers F3 would: the very tuples
    the app sampled and pushed to F3, handed in and not sampled again."""
    row = {"system": ("CPU 1%",), "quota": ("cc 1%",), "build": ("aegis 1",)}
    scr = FleetScreen(lambda **_: FleetSnapshot(), system_row=lambda: row)
    scr.refresh_fleet()
    band = scr._current.band
    assert (band.system, band.quota, band.build) == (
        ("CPU 1%",),
        ("cc 1%",),
        ("aegis 1",),
    )


async def test_the_build_shows_before_the_first_tick(tmp_path):
    """System and quota may be empty at boot; the build never is."""
    app = _standalone(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        row = app._fleet_system_row()
        assert set(row) == {"system", "quota", "build"}
        assert isinstance(row["system"], tuple) and isinstance(row["quota"], tuple)
        assert row["build"]


async def test_the_band_keeps_its_meters_with_a_file_tab_in_front(
    tmp_path, monkeypatch
):
    """The operator opens F10 from whatever tab is in front. A file or
    terminal tab has no SYSTEM row, so the app holds the last sample: sampled
    once per tick, pushed to F3 when an agent pane is in front, and read by
    the band either way."""
    from aegis.tui import sysmeter

    calls = []
    real = sysmeter.sample_system
    monkeypatch.setattr(
        sysmeter, "sample_system", lambda cwd: calls.append(cwd) or real(cwd)
    )
    note = tmp_path / "notes.txt"
    note.write_text("hello\n")
    app = _standalone(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        agent = app._active
        calls.clear()
        app._tick()
        assert len(calls) == 1
        assert agent._system_tiers and agent._system_tiers == app._system_last

        await app._open_file_tab(note)
        await pilot.pause()
        assert app._active is not agent and not hasattr(app._active, "set_system")
        calls.clear()
        app._tick()
        assert len(calls) == 1
        assert app._system_last

        await pilot.press("f10")
        await pilot.pause()
        assert isinstance(app.screen, FleetScreen)
        band = app.screen._current.band
        assert band.system == app._system_last
        assert band.build


async def test_the_screen_behind_the_fleet_does_not_repaint_it(tmp_path):
    """Textual's background-screen branch (``Screen._compositor_refresh``)
    sets ``_repaint_required`` on the covered screen after forwarding its
    dirty regions, so every idle of a busy covered screen became a full
    repaint of it and of the fleet on top: 57 frames/s in the bench. The
    covered screen is kept busy here the way streaming sessions keep it,
    by going idle 50 times a second."""
    from textual.screen import Screen

    app = _standalone(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        behind = app.screen
        await pilot.press("f10")
        await pilot.pause()
        fleet = app.screen
        assert isinstance(fleet, FleetScreen)
        full = []
        refresh = Screen._compositor_refresh

        def counting(scr):
            if scr is fleet and scr in scr._dirty_widgets:
                full.append(1)
            return refresh(scr)

        Screen._compositor_refresh = counting
        try:
            behind.query_one("StatusBar").refresh()
            app.set_interval(0.02, behind.check_idle)
            await pilot.pause(1.0)
        finally:
            Screen._compositor_refresh = refresh
        assert len(full) <= 3, f"{len(full)} full repaints of the fleet in 1 s"
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is behind


async def test_a_pushed_modal_is_opaque_so_the_screen_below_stays_paused(tmp_path):
    """`AegisApp._background_screens` pauses the covered screen only when the
    top screen is opaque; a translucent one falls back to Textual, whose
    covered screen repaints in a loop (57 frames/s behind F10). Today every
    pushed screen is opaque through the app's `Screen { background: ... }`
    rule, which beats `ModalScreen`'s translucent default. A bare
    `ModalScreen` subclass with no CSS of its own must inherit that."""
    from textual.screen import ModalScreen

    class _Bare(ModalScreen):
        pass

    app = _standalone(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.push_screen(_Bare())
        await pilot.pause()
        assert app.screen.styles.background.a == 1
        assert app._background_screens == []


def test_no_modal_in_aegis_makes_its_own_screen_translucent():
    """The other half of the guard: a modal whose own CSS gives the screen a
    translucent background would bring the repaint loop back under it
    without anything failing. Scans each `ModalScreen` subclass's rule for
    its own selector."""
    import re
    from pathlib import Path

    import aegis

    src = Path(aegis.__file__).parent
    offenders = []
    for path in src.rglob("*.py"):
        text = path.read_text()
        for name in re.findall(r"class (\w+)\([^)]*ModalScreen[^)]*\)", text):
            for rule in re.findall(rf"(?m)^\s*{name}\s*\{{([^}}]*)\}}", text):
                bg = re.search(r"background:\s*([^;]+);", rule)
                if bg and ("%" in bg.group(1) or "transparent" in bg.group(1)):
                    offenders.append(f"{path.relative_to(src)}: {name} {bg.group(1)}")
    assert not offenders, offenders


async def test_f10_over_a_covered_fleet_does_not_stack_a_second_one(tmp_path):
    """F4 over F10, then F10: the fleet is already in the stack, only
    covered (F2 opens a tab, not a screen, so it cannot cover F10). A
    second FleetScreen would double every session's event observers and
    fleet watchers for a view nobody can see."""
    app = _bridged(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _two_tabs(app, pilot)
        await pilot.press("f10")
        await pilot.pause()
        assert isinstance(app.screen, FleetScreen)
        await pilot.press("f4")
        await pilot.pause()
        assert not isinstance(app.screen, FleetScreen), "F4 must cover F10"
        await pilot.press("f10")
        await pilot.pause()
        fleets = [s for s in app.screen_stack if isinstance(s, FleetScreen)]
        assert len(fleets) == 1
