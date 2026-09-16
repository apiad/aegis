"""The card's three middle rows. Today the only way to know what a session
did a minute ago is to read its transcript, which is exactly what a
dashboard exists to avoid."""

import pytest

from aegis.events import ToolUse
from aegis.fleet.models import EventLine


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


@pytest.fixture
def session(tmp_path):
    """A brain wired the way `cli.py::_serve` wires one, then one session
    off it. Built here rather than in `tests/conftest.py`: only this file
    needs it, and the shared conftest drives real harnesses."""
    from aegis.config import Agent
    from aegis.config.roots import AegisRoots

    from tests.brain import make_brain

    roster = {
        "opus": Agent(
            harness="claude-code", model="opus", effort="high", permission="auto"
        )
    }
    mgr = make_brain(
        roster,
        "opus",
        make_session=lambda p, u, h, **kw: _FakeHarness(),
        mcp=None,
        roots=AegisRoots.for_project(tmp_path),
    )
    return mgr._sync_spawn("opus")


def test_a_fresh_session_has_no_events(session):
    assert session.recent_events == ()


def test_a_tool_call_lands_in_the_ring(session):
    session.note_event(ToolUse(name="Edit", summary="apps/sigere/pusher.py"), at=100.0)
    assert session.recent_events == (
        EventLine(at=100.0, tool="Edit", summary="apps/sigere/pusher.py"),
    )


def test_the_ring_keeps_the_five_newest(session):
    for i in range(8):
        session.note_event(ToolUse(name="Bash", summary=f"cmd {i}"), at=float(i))
    assert len(session.recent_events) == 5
    assert [e.summary for e in session.recent_events] == [
        "cmd 3",
        "cmd 4",
        "cmd 5",
        "cmd 6",
        "cmd 7",
    ]


def test_a_subagents_tool_call_is_not_the_sessions_own(session):
    """A Task subagent's tools are not what THIS agent is doing, and the
    queue already applies this rule to assistant text for the same reason
    (queue/manager.py, `_attach_observers`)."""
    session.note_event(
        ToolUse(name="Read", summary="x.py", parent_tool_use_id="toolu_01"), at=1.0
    )
    assert session.recent_events == ()
