"""The tab bar leads with the pending category instead of the ready dot."""

import pytest

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
    out = _label(AgentState.working, "")
    assert "●" in out


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
