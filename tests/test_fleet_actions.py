"""Stop and restart on an F10 card: the glyphs, their hit boxes, and the two
ways they fire — a click on the icon and the `s` / `r` keys."""

import time

import pytest

from rich.cells import cell_len
from textual.geometry import Offset

from aegis.fleet.models import CardView, FleetSnapshot
from aegis.fleet.render import (
    ACTION_W,
    ACTIONS_W,
    RESTART,
    STOP,
    action_at,
    actions_prefix,
    render_item,
)
from aegis.tui.fleet_screen import FleetScreen, _Item
from aegis.tui.themes import INK, aegis_colors

from tests.test_fleet_screen import _standalone, _two_tabs

P = aegis_colors(INK)
SNAP = FleetSnapshot(
    cards=tuple(CardView(handle=f"s{i}", tab_index=i + 1) for i in range(3))
)

# Where the content box starts inside an _Item's region: one cell of
# `border: round` plus one of `padding: 0 1` across, one cell of border down
# (the padding is 0 vertically). Written out because `pilot.click` takes a
# region offset, and the test that clicks the icons has to convert.
CONTENT = Offset(2, 1)


# --- the drawing and its hit boxes ---


def test_the_pair_opens_a_live_cards_first_line():
    line = render_item(CardView(handle="a"), P, 0, actions=True).plain.split("\n")[0]
    assert line.startswith(f"{STOP} {RESTART} ")


def test_a_card_drawn_without_actions_is_what_it_always_was():
    """Default off, so every reader of a card is untouched by the pair."""
    plain = render_item(CardView(handle="a"), P, 0).plain
    assert STOP not in plain and RESTART not in plain
    assert render_item(CardView(handle="a"), P, 0, actions=True).plain == (
        f"{STOP} {RESTART} " + plain
    )


def test_the_hit_boxes_cover_exactly_the_cells_drawn():
    """The width the clicks are mapped against is the width of the ink. If
    these drift, a click lands one target over and stops the wrong agent."""
    assert cell_len(actions_prefix(P, live=True).plain) == ACTIONS_W


@pytest.mark.parametrize(
    "x,want",
    [
        (0, "stop"),  # the glyph
        (ACTION_W - 1, "stop"),  # the gap after it is still the target
        (ACTION_W, "restart"),
        (ACTIONS_W - 1, "restart"),
        (ACTIONS_W, None),  # the card's own first line begins here
        (-1, None),
        (40, None),
    ],
)
def test_every_cell_maps_to_one_target_or_none(x, want):
    assert action_at(x) == want


def test_a_ghost_draws_the_pair_dimmed_rather_than_dropping_it():
    """Dropping it would shift the ghost's first line two cells left of every
    other card's, which reads as a different kind of row, not a dead one."""
    ghost = CardView(handle="a", ghost_since=1.0)
    text = render_item(ghost, P, 0, actions=True)
    assert text.plain.startswith(f"{STOP} {RESTART} ")
    assert text.plain == render_item(ghost, P, 0, actions=True).plain


# --- the keys ---


def _screen_recording():
    scr = FleetScreen(lambda **_kw: SNAP)
    scr._current = SNAP
    acted: list[tuple[str, str]] = []
    scr.act_on = lambda handle, action: acted.append((handle, action))
    return scr, acted


def test_s_and_r_act_on_the_selected_card():
    scr, acted = _screen_recording()
    scr.selected = 2
    scr.action_stop()
    scr.action_restart()
    assert acted == [("s1", "stop"), ("s1", "restart")]


def test_a_ghost_card_cannot_be_stopped_or_restarted():
    """Its session is gone; there is nothing to interrupt or send to."""
    snap = FleetSnapshot(cards=(CardView(handle="dead", ghost_since=1.0),))
    scr = FleetScreen(lambda **_kw: snap)
    scr._current = snap
    acted = []
    scr.act_on = lambda handle, action: acted.append((handle, action))
    scr.action_stop()
    scr.action_restart()
    assert acted == []


def test_an_empty_fleet_ignores_both_keys():
    empty = FleetSnapshot(cards=())
    scr = FleetScreen(lambda **_kw: empty)
    scr._current = empty
    acted = []
    scr.act_on = lambda handle, action: acted.append((handle, action))
    scr.action_stop()
    scr.action_restart()
    assert acted == []


def test_the_footer_still_names_the_key_that_closes_the_screen():
    """`aegis bench`'s fleet scenario waits on this substring to know F10
    drew at all, and the row was compacted to fit stop and restart."""
    from aegis.tui.fleet_screen import _KEYS

    assert "esc/F10 close" in _KEYS
    assert "s stop" in _KEYS and "r restart" in _KEYS


# --- mounted: the icons under a real mouse ---


def _recorder(app):
    calls: list[tuple[str, str]] = []

    async def stop(handle, **_kw):
        calls.append((handle, "stop"))

    async def restart(handle):
        calls.append((handle, "restart"))

    app.interrupt = stop
    app.restart = restart
    return calls


async def _fleet_with_items(app, pilot):
    await _two_tabs(app, pilot)
    app._activate(0)
    await pilot.press("f10")
    await pilot.pause()
    scr = app.screen
    items = {i.handle: i for i in scr.query_one("#fleet-list").query(_Item)}
    return scr, items


@pytest.mark.parametrize(
    "dx,action", [(0, "stop"), (ACTION_W, "restart")], ids=["stop", "restart"]
)
async def test_clicking_an_icon_acts_on_that_card(tmp_path, dx, action):
    app = _standalone(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        scr, items = await _fleet_with_items(app, pilot)
        calls = _recorder(app)
        target = app._panes[1].handle
        await pilot.click(items[target], offset=CONTENT + Offset(dx, 0))
        await pilot.pause()
        assert calls == [(target, action)]


async def test_clicking_an_icon_neither_selects_nor_opens_the_card(tmp_path):
    """Four agents stopped in four clicks is the point: a click that moved
    the selection would scroll the detail under the next one, and a second
    click on an already-selected card would open its tab and close F10."""
    app = _standalone(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        scr, items = await _fleet_with_items(app, pilot)
        _recorder(app)
        before = scr.selected
        other = app._panes[1].handle
        for _ in range(2):
            await pilot.click(items[other], offset=CONTENT)
            await pilot.pause()
        assert scr.selected == before
        assert scr.chosen is None
        assert app.screen is scr


async def test_a_click_past_the_pair_selects_the_card_as_it_always_did(tmp_path):
    app = _standalone(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        scr, items = await _fleet_with_items(app, pilot)
        calls = _recorder(app)
        other = app._panes[1].handle
        await pilot.click(items[other], offset=CONTENT + Offset(ACTIONS_W + 2, 0))
        await pilot.pause()
        assert calls == []
        assert scr._current.cards[scr.selected - 1].handle == other


async def test_a_click_below_the_first_line_is_not_an_icon(tmp_path):
    """The pair is on row 0 only; the `where` line underneath is card body."""
    app = _standalone(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        scr, items = await _fleet_with_items(app, pilot)
        calls = _recorder(app)
        other = app._panes[1].handle
        await pilot.click(items[other], offset=CONTENT + Offset(0, 1))
        await pilot.pause()
        assert calls == []


async def test_the_selected_card_is_still_opened_by_enter(tmp_path):
    """The pair must not have eaten the screen's existing gesture."""
    app = _standalone(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        scr, _items = await _fleet_with_items(app, pilot)
        await pilot.press("down")
        await pilot.pause()
        assert not scr.rotator.is_auto(time.monotonic())
        await pilot.press("enter")
        await pilot.pause()
        assert app._active is app._panes[1]
