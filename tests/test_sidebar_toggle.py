"""F3 toggles a mode, not a widget.

These assertions are on real widget visibility, never on the presence of
the CSS class: a class-name assertion passes against a rule that no longer
hides anything, which is exactly the failure the rule exists to prevent.
The plan's Task 4 step 8 mutation-checks that.
"""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.events import AgentPlan, AssistantText, PlanEntry, Result
from aegis.tui.app import AegisApp
from aegis.tui.monitor_strip import MonitorStrip
from aegis.tui.plan_strip import PlanStrip
from aegis.tui.sidebar import SIDEBAR_MIN, Sidebar
from aegis.tui.strip import QueueStrip
from aegis.tui.widgets import StatusBar


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    session_id = "sid-1"

    def __init__(self):
        self.sent = []

    async def start(self): pass
    async def send(self, text): self.sent.append(text)

    async def events(self):
        yield AssistantText("ok")
        yield Result(duration_ms=1, is_error=False)

    async def close(self): pass


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): self.bound = bridge
    async def start(self): pass
    async def stop(self): pass


def _app():
    def make(agent, mcp_url, handle, **kw):
        return FakeSession()
    return AegisApp({"default": _agent()}, "default", make, FakeMCP())


COLLAPSED = (QueueStrip, MonitorStrip, PlanStrip, StatusBar)


def _one_task_plan():
    from aegis.plan import PlanState, PlanTask
    return PlanState(tasks=(
        PlanTask(key="1", subject="a", status="in_progress"),))


@pytest.mark.asyncio
async def test_closed_is_todays_pane():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await pilot.pause()
        assert not pane.query_one(Sidebar).display
        assert pane.query_one(StatusBar).display


@pytest.mark.asyncio
async def test_opening_shows_the_sidebar_and_hides_every_collapsed_surface():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()

        assert pane.query_one(Sidebar).display
        still_visible = [w.__class__.__name__
                         for cls in COLLAPSED
                         for w in pane.query(cls) if w.display]
        assert still_visible == []


@pytest.mark.asyncio
async def test_closing_restores_every_collapsed_surface():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        pane.toggle_task_dock()
        await pilot.pause()

        assert not pane.query_one(Sidebar).display
        assert pane.query_one(StatusBar).display


@pytest.mark.asyncio
async def test_a_plan_update_cannot_reopen_a_hidden_plan_strip():
    """PlanStrip used to set .display imperatively, and an inline style
    beats CSS — so a plan arriving while the sidebar was open would put the
    strip back on screen underneath it."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        pane.query_one(PlanStrip).refresh_plan(_one_task_plan(), True)
        await pilot.pause()
        assert not pane.query_one(PlanStrip).display


# --- the open mode carries what the collapsed one did -------------------


def _sidebar_text(pane) -> str:
    return pane.query_one(Sidebar).plain()


@pytest.mark.asyncio
async def test_the_open_sidebar_carries_the_status_bar_segments():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.set_system(("cpu 34% ram 61% disk 82%",))
        pane.toggle_task_dock()
        await pilot.pause()
        text = _sidebar_text(pane)
        assert "SESSION" in text
        assert "SYSTEM" in text
        assert "cpu 34%" in text


@pytest.mark.asyncio
async def test_the_open_sidebar_shows_a_monitor_armed_for_this_pane():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        app.monitor_manager.start_monitor(
            from_handle=pane.handle, description="full suite",
            done="false", autorun=False)
        await pilot.pause()
        assert "full suite" in _sidebar_text(pane)


@pytest.mark.asyncio
async def test_a_disconnect_leads_the_session_section():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.set_connection_state(False)
        pane.toggle_task_dock()
        await pilot.pause()
        lines = [ln for ln in _sidebar_text(pane).split("\n") if ln]
        assert lines[0] == "SESSION"
        assert lines[1].startswith("⚠ disconnected")


@pytest.mark.asyncio
async def test_a_closed_sidebar_stores_but_never_renders():
    """The closed mode costs one branch per event, not a second render
    tree. The model still updates — dropping it would make the first frame
    after a toggle stale — but nothing paints."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        sidebar = pane.query_one(Sidebar)
        sidebar._paints = 0
        pane.set_system(("cpu 99%",))
        pane.refresh_metrics()
        await pilot.pause()
        assert sidebar._paints == 0
        assert sidebar._model.system == ("cpu 99%",)


@pytest.mark.asyncio
async def test_a_closed_pane_releases_its_sidebar_subscriptions():
    """The sidebar subscribes to the monitor manager and the queue digest,
    both of which outlive any one pane. Without a matching release the
    callbacks pile up and fire into a torn-down pane — which surfaced as
    `RuntimeError: Event loop is closed` at interpreter shutdown, and is
    the same shape as the watchdog-observer leak that once ate the user's
    inotify instances."""
    app = _app()
    async with app.run_test() as pilot:
        before = len(app.monitor_manager._subs)
        handle = await app.spawn("default", handle="peer-one")
        await pilot.pause()
        assert len(app.monitor_manager._subs) > before

        await app.close(handle)
        await pilot.pause()
        assert len(app.monitor_manager._subs) == before


@pytest.mark.asyncio
async def test_the_first_frame_after_opening_is_not_stale():
    """The corollary: an update that arrived while closed must be on
    screen the moment it opens."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.set_system(("cpu 99%",))
        pane.toggle_task_dock()
        await pilot.pause()
        assert "cpu 99%" in _sidebar_text(pane)


# --- the column reads as a surface, not as bare transcript --------------


@pytest.mark.asyncio
async def test_the_open_sidebar_is_tinted_off_the_transcript():
    """The four strips it absorbs each sat on `$panel`. Sharing that token
    is what makes the column read as one surface instead of text floating
    beside the transcript, which is on `$background`.

    Asserted on `background_colors[1]` — the colour the widget actually
    composites to — not on `styles.background`, which is the *declared*
    value and reads as transparent on any widget that never set one. That
    version of this test passed against the untinted sidebar.
    """
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        sidebar = pane.query_one(Sidebar).background_colors[1]
        transcript = pane.query_one("#transcript").background_colors[1]
        bar = pane.query_one(StatusBar).background_colors[1]
        assert sidebar != transcript
        assert sidebar == bar        # the token the absorbed strips used


@pytest.mark.asyncio
async def test_the_open_sidebar_is_padded_on_all_four_sides():
    """A tinted column with no vertical padding puts its first heading
    hard against the tab bar and the tint hard against the pane edge."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        pad = pane.query_one(Sidebar).styles.padding
        assert pad.top >= 1 and pad.bottom >= 1
        assert pad.left >= 2 and pad.right >= 2


@pytest.mark.asyncio
async def test_the_render_width_excludes_the_padding_it_grew():
    """`size` is already the content box, so `_width` must not subtract
    the padding a second time — and the fallback taken before the first
    layout has to track the real padding rather than the `0 1` it was
    written against, or a never-shown tab truncates to the wrong budget."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        sidebar = pane.query_one(Sidebar)
        pad = sidebar.styles.padding

        # Laid out: the content box, taken whole.
        assert sidebar._width() == sidebar.size.width
        assert sidebar.size.width \
            == sidebar.outer_size.width - pad.left - pad.right

        # Never laid out (a fresh widget's size is 0x0): the fallback is
        # the narrowest content box the CSS can produce, not the frame.
        assert Sidebar(pane._palette)._width() \
            == SIDEBAR_MIN - pad.left - pad.right


# --- the PLAN section is a section, not a dock in a box -----------------


def _with_plan(pane, *entries):
    """Drive the pane's real tracker, not a hand-built PlanState — the
    dock reads working time off it and a fabricated state renders rows
    the live one never would."""
    now = pane._core._now()
    pane._core._trackers_working(True, ts=now - 154.0)
    pane._core._apply_plan(AgentPlan(entries=tuple(
        PlanEntry(content=c, status=s, id=str(i))
        for i, (c, s) in enumerate(entries))), ts=now - 154.0)
    pane._refresh_plan_surfaces()


@pytest.mark.asyncio
async def test_the_plan_section_leaves_one_blank_row_like_every_other():
    """`render_plan_dock` ends every row with a newline, so the block it
    returns carries a trailing one. Pasted between the sidebar's own
    "\\n\\n" separators that renders as two blank rows after PLAN and one
    after each of its five siblings — a gap that reads as a missing
    section rather than as spacing."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        # SYSTEM after it: a trailing blank at the very end of the column
        # is invisible, so PLAN has to be followed by something for the
        # doubled gap to exist at all.
        pane.set_system(("cpu 34%",))
        _with_plan(pane, ("alpha", "completed"), ("beta", "in_progress"))
        await pilot.pause()
        lines = _sidebar_text(pane).split("\n")
        assert "SYSTEM" in lines
        assert not any(a == "" and b == ""
                       for a, b in zip(lines, lines[1:])), lines


@pytest.mark.asyncio
async def test_the_plan_counter_is_not_printed_twice():
    """The dock opened with its own `tasks d/t` header because it was a
    free-standing surface. As a section it is not: `heading` already puts
    the same counter at the right edge of the PLAN row."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        _with_plan(pane, ("alpha", "completed"), ("beta", "in_progress"))
        await pilot.pause()
        text = _sidebar_text(pane)
        assert text.count("1/2") == 1, text
        assert "tasks 1/2" not in text
        assert "alpha" in text and "beta" in text     # rows still render


# --- the two paths into the mode ---------------------------------------


@pytest.mark.asyncio
async def test_slash_tasks_toggles_the_same_mode_as_f3():
    """`/tasks` reaches the sidebar through the command seam's `effect`
    dict, and `_apply_command_effect` ignores kinds it does not know so a
    new client can send new ones. That tolerance is also what would let a
    renamed effect silently do nothing, with the command still reporting
    ok — so the wiring is asserted end to end, from the typed line to the
    widget, rather than at either end alone.
    """
    from aegis.commands import CommandContext, dispatch

    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        assert not pane.query_one(Sidebar).display

        res = await dispatch("/tasks", CommandContext(
            bridge=app, handle=pane.handle))
        assert res.ok and res.effect is not None
        pane._apply_command_effect(res.effect)
        await pilot.pause()
        assert pane.query_one(Sidebar).display
        assert not pane.query_one(StatusBar).display

        pane._apply_command_effect(res.effect)
        await pilot.pause()
        assert not pane.query_one(Sidebar).display
        assert pane.query_one(StatusBar).display


# --- the mode is app-wide, not per-tab ----------------------------------


@pytest.mark.asyncio
async def test_f3_puts_every_tab_in_the_mode(tmp_path, monkeypatch):
    """F3 is a reading mode for aegis, not a widget on one pane: it moves
    every tab at once, so switching tabs cannot change the layout."""
    monkeypatch.chdir(tmp_path)
    app = _app()
    async with app.run_test() as pilot:
        await app._spawn(app._default_agent)
        await pilot.pause()
        first, second = app._panes[0], app._panes[1]

        app.action_toggle_tasks()
        await pilot.pause()
        assert first.query_one(Sidebar).display
        assert second.query_one(Sidebar).display
        assert not second.query_one(StatusBar).display

        app.action_toggle_tasks()
        await pilot.pause()
        assert not first.query_one(Sidebar).display
        assert not second.query_one(Sidebar).display
        assert second.query_one(StatusBar).display


@pytest.mark.asyncio
async def test_the_pane_entry_point_moves_the_whole_app(tmp_path,
                                                        monkeypatch):
    """`/tasks` and the pane's own toggle reach the mode through the pane
    that received them — they must still flip it everywhere, or the two
    entry points would disagree with F3."""
    monkeypatch.chdir(tmp_path)
    app = _app()
    async with app.run_test() as pilot:
        await app._spawn(app._default_agent)
        await pilot.pause()
        first, second = app._panes[0], app._panes[1]

        second.toggle_task_dock()
        await pilot.pause()
        assert first.query_one(Sidebar).display
        assert second.query_one(Sidebar).display


@pytest.mark.asyncio
async def test_a_tab_opened_while_the_mode_is_on_comes_up_in_it(
        tmp_path, monkeypatch):
    """A pane mounted later adopts the mode. Without this a fresh tab
    lands collapsed beside its siblings and F3 has to be pressed twice."""
    monkeypatch.chdir(tmp_path)
    app = _app()
    async with app.run_test() as pilot:
        app.action_toggle_tasks()
        await pilot.pause()

        await app._spawn(app._default_agent)
        await pilot.pause()
        fresh = app._panes[-1]
        assert fresh.query_one(Sidebar).display
        assert not fresh.query_one(StatusBar).display
