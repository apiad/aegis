"""Plan state on the coordination plane.

A peer deciding who to hand work to should learn not just that an agent is
busy but how far along it is and what it is on. aegis_list_sessions is
dataclasses.asdict over SessionInfo, so a field added there reaches every
peer with no change to the tool body; aegis_peer_plan is the drill-down.
"""
import dataclasses

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
