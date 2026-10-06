"""The tab bar leads with the pending category instead of the ready dot."""

from types import SimpleNamespace

import pytest

from aegis.tui.pane import ConversationPane
from aegis.tui.state import AgentState
from aegis.tui.themes import INK, aegis_colors
from aegis.tui.widgets import TabBar, _TabCell

from tests.test_tui import FakeSession, _app, _factory

C = aegis_colors(INK)


def _label(state, attention_mark, active=False):
    cell = _TabCell(markup=True)
    seen = {}
    cell.update = lambda s: seen.setdefault("s", s)
    cell.render_tab(1, "h", "opus", state, False, active, None, attention_mark, C)
    return seen["s"]


def test_a_pending_category_replaces_the_ready_dot():
    out = _label(AgentState.ready, "[x]?[/]")
    assert out.lstrip().startswith("[x]?[/]")
    assert "●" not in out


def test_a_working_session_keeps_its_dot_whatever_the_category():
    out = _label(AgentState.working, "[x]?[/]")
    assert "●" in out
    assert "[x]?[/]" not in out


def test_no_category_is_todays_tab():
    out = _label(AgentState.ready, "")
    assert "●" in out


@pytest.mark.asyncio
async def test_an_inactive_tab_shows_its_mark_until_activated():
    app = _app(_factory(FakeSession(), FakeSession()))
    async with app.run_test() as pilot:
        await pilot.press("ctrl+t")
        await pilot.pause()
        idx = next(i for i, p in enumerate(app._panes) if p is not app._active)
        pane = app._panes[idx]
        pane._core.attention = "needs_input"
        pane._core.attention_seq = 1

        app._refresh_tabbar()
        assert app.query_one(TabBar)._items[idx][7] != ""

        app._activate(idx)
        assert app.query_one(TabBar)._items[idx][7] == ""
        assert pane.attention_acked == 1


@pytest.mark.asyncio
async def test_the_tick_paints_a_late_category_without_writing_the_roster():
    app = _app(_factory(FakeSession(), FakeSession()))
    async with app.run_test() as pilot:
        await pilot.press("ctrl+t")
        await pilot.pause()
        idx = next(i for i, p in enumerate(app._panes) if p is not app._active)
        pane = app._panes[idx]
        pane._core.attention = "review"
        pane._core.attention_seq = 1
        snapshots = []
        app._schedule_snapshot = lambda: snapshots.append(1)

        app._tick()
        assert app.query_one(TabBar)._items[idx][7] != ""
        assert snapshots == []


@pytest.mark.asyncio
async def test_a_tick_during_teardown_finds_no_tab_bar_and_does_nothing():
    from textual.css.query import NoMatches

    app = _app(_factory(FakeSession()))
    async with app.run_test() as pilot:
        await pilot.pause()
        real_query_one = app.query_one

        def query_one(selector, *a, **kw):
            if selector is TabBar:
                raise NoMatches("TabBar pruned")
            return real_query_one(selector, *a, **kw)

        app.query_one = query_one
        app._tick()
        app._refresh_tabbar()
        del app.query_one


def test_the_sidebar_state_names_the_category_whether_or_not_acked():
    core = SimpleNamespace(
        state=AgentState.ready, effective_attention="needs_input", attention_seq=1
    )
    pane = SimpleNamespace(attention_acked=0)
    assert ConversationPane._state_label(pane, core) == "idle · ? needs you"
    pane.attention_acked = 1  # the tab bar acks the active tab every tick
    assert ConversationPane._state_label(pane, core) == "idle · ? needs you"


@pytest.mark.parametrize(
    "category, tail", [("error", " · ✗ error"), ("review", " · ◆ review"),
                       ("waiting", " · ⧗ waiting"), ("done", ""), ("", "")]
)
def test_the_sidebar_state_per_category(category, tail):
    core = SimpleNamespace(state=AgentState.ready, effective_attention=category, attention_seq=1)
    pane = SimpleNamespace(attention_acked=1)
    assert ConversationPane._state_label(pane, core) == "idle" + tail


def test_a_working_session_state_names_no_category():
    core = SimpleNamespace(state=AgentState.working, effective_attention="error", attention_seq=1)
    pane = SimpleNamespace(attention_acked=0)
    assert ConversationPane._state_label(pane, core) == AgentState.working.label


def test_acking_in_one_view_leaves_it_pending_in_another():
    from aegis.tui.app import _attention_mark

    core = SimpleNamespace(effective_attention="needs_input", attention_seq=1)
    here = SimpleNamespace(_core=core, attention_acked=0)
    there = SimpleNamespace(_core=core, attention_acked=0)

    assert _attention_mark(here, True, C, False) == ""
    assert here.attention_acked == 1
    assert _attention_mark(there, False, C, False) != ""
    assert there.attention_acked == 0
