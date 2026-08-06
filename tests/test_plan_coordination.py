"""Plan state on the coordination plane.

A peer deciding who to hand work to should learn not just that an agent is
busy but how far along it is and what it is on. aegis_list_sessions is
dataclasses.asdict over SessionInfo, so a field added there reaches every
peer with no change to the tool body; aegis_peer_plan is the drill-down.
"""
import dataclasses

import pytest

from aegis.mcp.bridge import SessionInfo
from aegis.plan import PlanSnapshot, PlanState, PlanTask


def test_session_info_defaults_plan_to_none():
    """Every existing construction site stays valid."""
    s = SessionInfo(handle="h", agent_slug="a", state="ready",
                    active=True, unseen=False)
    assert s.plan is None


def test_plan_reaches_list_sessions_through_asdict():
    s = SessionInfo(handle="h", agent_slug="a", state="working",
                    active=True, unseen=False,
                    plan=PlanSnapshot(done=3, total=8, current="Wire it up",
                                      current_working_s=63.0))
    d = dataclasses.asdict(s)
    assert d["plan"]["done"] == 3
    assert d["plan"]["total"] == 8
    assert d["plan"]["current"] == "Wire it up"
    assert d["plan"]["current_working_s"] == 63.0


def test_manager_populates_the_roll_up_from_the_live_tracker():
    """The bridge must read the session's real tracker, not a stub."""
    from aegis.events import AgentPlan, PlanEntry

    from tests.test_plan_tracker import _FakeSession
    from aegis.core.session import AgentSession
    from aegis.core.manager import SessionManager

    sess = AgentSession(_FakeSession(), agent=None, agent_slug="default",
                        handle="worker")
    sess._fire_event(AgentPlan(entries=(
        PlanEntry(content="read", status="completed"),
        PlanEntry(content="write", status="in_progress"),
        PlanEntry(content="ship", status="pending"))))

    mgr = SessionManager.__new__(SessionManager)
    mgr._sessions = [sess]
    mgr._mru = []
    info = mgr.list_sessions()[0]
    assert info.plan is not None
    assert (info.plan.done, info.plan.total) == (1, 3)
    assert info.plan.current == "write"


def test_a_session_with_no_plan_reports_none_not_zero_of_zero():
    """None means "no plan"; 0/0 would read as "a plan with no tasks"."""
    from tests.test_plan_tracker import _FakeSession
    from aegis.core.session import AgentSession
    from aegis.core.manager import SessionManager

    sess = AgentSession(_FakeSession(), agent=None, agent_slug="default",
                        handle="idle")
    mgr = SessionManager.__new__(SessionManager)
    mgr._sessions = [sess]
    mgr._mru = []
    assert mgr.list_sessions()[0].plan is None


def test_plan_state_reaches_the_bridge_for_the_drill_down():
    from aegis.events import AgentPlan, PlanEntry

    from tests.test_plan_tracker import _FakeSession
    from aegis.core.session import AgentSession
    from aegis.core.manager import SessionManager

    sess = AgentSession(_FakeSession(), agent=None, agent_slug="default",
                        handle="worker")
    sess._fire_event(AgentPlan(entries=(
        PlanEntry(content="read", status="completed"),)))
    mgr = SessionManager.__new__(SessionManager)
    mgr._sessions = [sess]
    mgr._mru = []
    st = mgr.plan_state("worker")
    assert isinstance(st, PlanState)
    assert st.tasks[0].subject == "read"
    assert mgr.plan_state("ghost") is None


# -- the MCP surface -------------------------------------------------

import pytest


def _server_with(plan_by_handle, handles=("worker",)):
    from aegis.mcp.bridge import SessionInfo
    from aegis.mcp.server import build_server
    from tests.test_mcp_server import FakeBridge

    br = FakeBridge()
    br.list_sessions = lambda: [
        SessionInfo(handle=h, agent_slug="main", state="working",
                    active=False, unseen=False)
        for h in handles]
    br.plan_state = lambda h: plan_by_handle.get(h)
    return build_server(br)


@pytest.mark.asyncio
async def test_peer_plan_returns_the_full_annotated_list():
    """list_sessions says 1/2; this says which two, and for how long."""
    from tests.test_mcp_server import _call

    plan = PlanState(tasks=(
        PlanTask(key="1", subject="Read the spec", status="completed",
                 working_s=252.0),
        PlanTask(key="2", subject="Write the code", status="in_progress",
                 working_s=63.0)))
    out = await _call(_server_with({"worker": plan}), "aegis_peer_plan",
                      handle="worker")
    assert (out["done"], out["total"]) == (1, 2)
    assert out["tasks"][1] == {"subject": "Write the code",
                               "status": "in_progress", "working_s": 63.0}


@pytest.mark.asyncio
async def test_peer_plan_refuses_an_unknown_handle():
    from tests.test_mcp_server import _call

    out = await _call(_server_with({}), "aegis_peer_plan", handle="ghost")
    assert "no session" in out["error"]


@pytest.mark.asyncio
async def test_peer_plan_on_a_session_with_no_plan_is_empty_not_an_error():
    from tests.test_mcp_server import _call

    out = await _call(_server_with({}), "aegis_peer_plan", handle="worker")
    assert out["tasks"] == [] and out["total"] == 0
    assert "error" not in out


def test_the_briefing_tells_agents_plans_are_visible():
    """An agent that is never told cannot use it."""
    from aegis.mcp.server import BRIEFING

    assert "aegis_peer_plan" in BRIEFING
    assert "plan" in BRIEFING


# -- the tab bar -----------------------------------------------------

def test_tab_suffix_carries_plan_progress():
    """The tab bar answers "how far along is that other agent" without
    switching to it."""
    from aegis.events import AgentPlan, PlanEntry

    from tests.test_plan_tracker import _FakeSession
    from aegis.core.session import AgentSession
    from aegis.tui.app import _tab_suffix

    core = AgentSession(_FakeSession(), agent=None, agent_slug="default",
                        handle="w")
    core._fire_event(AgentPlan(entries=(
        PlanEntry(content="a", status="completed"),
        PlanEntry(content="b", status="in_progress"),
        PlanEntry(content="c", status="pending"))))

    class _Pane:
        handle = "w"
        _core = core

    assert _tab_suffix(_Pane(), None) == "1/3"


def test_tab_suffix_is_unchanged_for_a_session_without_a_plan():
    from tests.test_plan_tracker import _FakeSession
    from aegis.core.session import AgentSession
    from aegis.tui.app import _tab_suffix

    class _Pane:
        handle = "w"
        _core = AgentSession(_FakeSession(), agent=None,
                             agent_slug="default", handle="w")

    assert _tab_suffix(_Pane(), None) is None


# -- surviving a restart ---------------------------------------------
#
# The plan vanished from the strip the moment Alex restarted aegis, and
# only reappeared when the agent happened to call TaskList. The tracker was
# built to be replayable — every method takes an explicit ts precisely so a
# replayed transcript reproduces the live numbers — but nothing ever
# replayed it, so the property went unused and the surface went blank.

def test_replay_carries_the_timestamp_of_each_event(tmp_path):
    """Rehydration is only exact if the persisted ts comes back with the
    event; without it the clocks restart at zero."""
    from aegis.events import AgentPlan, PlanEntry
    from aegis.state.session_log import (
        make_session_log_observer, replay_events,
    )

    obs = make_session_log_observer(tmp_path, "log-1")
    obs(None, AgentPlan(entries=(PlanEntry(content="a", status="pending"),)))
    rep = replay_events(tmp_path, "log-1")
    assert len(rep.stamps) == len(rep.events)
    assert all(isinstance(s, float) for s in rep.stamps)


def test_rehydrate_restores_the_plan_after_a_restart():
    from aegis.core.session import AgentSession
    from aegis.events import AgentPlan, PlanEntry
    from tests.test_plan_tracker import _FakeSession

    plan = AgentPlan(entries=(
        PlanEntry(content="read", status="completed"),
        PlanEntry(content="write", status="in_progress"),
        PlanEntry(content="ship", status="pending")))

    fresh = AgentSession(_FakeSession(), agent=None, agent_slug="default",
                         handle="reborn")
    assert fresh.plan_state().total == 0, "precondition: a new session is blank"
    fresh.rehydrate_plan([plan], [1000.0])
    st = fresh.plan_state()
    assert (st.done, st.total) == (1, 3)
    assert st.current is not None and st.current.subject == "write"


def test_rehydrate_replays_working_time_from_the_persisted_stamps():
    """Replay equivalence: the banked clock is reproduced, not reset. The
    session worked from t=100 to t=160 with "write" in progress, so it must
    come back reading a minute — not the em dash a fresh task shows."""
    from aegis.core.session import AgentSession
    from aegis.events import AgentPlan, PlanEntry, Result
    from tests.test_plan_tracker import _FakeSession

    plan = AgentPlan(entries=(
        PlanEntry(content="write", status="in_progress"),))
    sess = AgentSession(_FakeSession(), agent=None, agent_slug="default",
                        handle="reborn")
    # plan lands at t=100 mid-turn; the turn ends (Result) at t=160.
    sess.rehydrate_plan([plan, Result(duration_ms=1, is_error=False)],
                        [100.0, 160.0])
    task = sess.plan_state().tasks[0]
    assert task.working_s == pytest.approx(60.0, abs=1.0), task.working_s


def test_rehydrate_leaves_a_transcript_with_no_plan_alone():
    from aegis.core.session import AgentSession
    from aegis.events import AssistantText
    from tests.test_plan_tracker import _FakeSession

    sess = AgentSession(_FakeSession(), agent=None, agent_slug="default",
                        handle="reborn")
    sess.rehydrate_plan([AssistantText(text="hello")], [1.0])
    assert sess.plan_roll_up() is None or sess.plan_state().total == 0


def test_rehydrate_routes_a_subagent_plan_to_its_own_tracker():
    from aegis.core.session import AgentSession
    from aegis.events import AgentPlan, PlanEntry
    from tests.test_plan_tracker import _FakeSession

    top = AgentPlan(entries=(PlanEntry(content="dispatch",
                                       status="in_progress"),))
    sub = AgentPlan(entries=(PlanEntry(content="grind", status="pending"),),
                    parent_tool_use_id="tool_1")
    sess = AgentSession(_FakeSession(), agent=None, agent_slug="default",
                        handle="reborn")
    sess.rehydrate_plan([top, sub], [1.0, 2.0])
    assert sess.plan_state().total == 1
    assert "tool_1" in sess.subplans
    assert sess.subplans["tool_1"].snapshot(ts=3.0).total == 1


def test_a_resumed_pane_paints_the_restored_plan_on_its_strip(tmp_path):
    """End to end through the real resume path: events on disk → replay →
    rehydrate → a visible strip. The unit tests above all passed while the
    strip stayed blank, because nothing repainted it at mount."""
    import asyncio

    from textual.app import App, ComposeResult

    from aegis.events import AgentPlan, PlanEntry
    from aegis.state.session_log import (
        make_session_log_observer, replay_events,
    )
    from aegis.tui.pane import ConversationPane
    from aegis.tui.plan_strip import PlanStrip
    from aegis.tui.themes import INK, aegis_colors
    from tests.test_plan_tracker import _FakeSession
    from aegis.core.session import AgentSession

    obs = make_session_log_observer(tmp_path, "log-resume")
    obs(None, AgentPlan(entries=(
        PlanEntry(content="read the spec", status="completed"),
        PlanEntry(content="write the code", status="in_progress"))))
    replay = replay_events(tmp_path, "log-resume")

    session = AgentSession(_FakeSession(), agent=None, agent_slug="default",
                           handle="reborn")

    class _A(App):
        def compose(self) -> ComposeResult:
            yield ConversationPane(
                session, None, "default", "reborn", aegis_colors(INK),
                replay=replay, log_id="log-resume")

    async def _run():
        async with _A().run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            strip = pilot.app.query_one("#plan-strip", PlanStrip)
            assert strip.display is True, "the resumed plan did not paint"
            assert "1/2" in strip.render().plain

    asyncio.run(_run())
