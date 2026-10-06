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


def test_a_tool_call_on_the_live_path_reaches_the_ring(session):
    """The seam, not the method. Delete the note_event call from
    _fire_event and this goes red; the four tests above do not."""
    session._fire_event(ToolUse(name="Edit", summary="apps/sigere/pusher.py"))
    assert [e.tool for e in session.recent_events] == ["Edit"]


def test_a_replayed_transcript_does_not_refill_the_ring(session):
    """Replay walks events through rehydrate_plan, which never calls
    _fire_event. A resumed session must start with an empty tail rather
    than one repainted from stale history."""
    session.rehydrate_plan([ToolUse(name="Edit", summary="old.py")], [1.0])
    assert session.recent_events == ()


# --- the activity tail is a label, not the command ------------------------


def test_a_bash_call_is_labelled_by_its_description_not_its_command(session):
    """Alex, 2026-09-17: the activity tail had too much detail. One line per
    call, the same label the transcript shows — for Bash that is the
    description the agent wrote, never the command."""
    session.note_event(
        ToolUse(
            name="Bash",
            summary="uv run pytest -q -n auto -m 'not slow' tests/",
            kind="execute",
            raw_input={
                "command": "uv run pytest -q -n auto -m 'not slow' tests/",
                "description": "Run the suite",
            },
        ),
        at=100.0,
    )
    assert session.recent_events == (
        EventLine(at=100.0, tool="Bash", summary="Run the suite"),
    )


def test_a_bash_call_with_no_description_keeps_a_short_command(session):
    cmd = "git log --oneline --decorate --graph --all --since=2.weeks --author=alex"
    session.note_event(ToolUse(name="Bash", summary=cmd, kind="execute",
                              raw_input={"command": cmd}), at=1.0)
    label = session.recent_events[0].summary
    assert label.startswith("git log") and len(label) <= 61


def test_a_file_call_is_labelled_the_way_the_transcript_labels_it(session):
    session.note_event(
        ToolUse(name="Read", summary="src/aegis/fleet/render.py", kind="read",
                raw_input={"file_path": "src/aegis/fleet/render.py"}),
        at=2.0,
    )
    assert session.recent_events[0].summary == "read render.py"


def test_the_activity_line_is_the_stamp_and_the_label_only():
    from aegis.fleet.render import _event

    assert _event(EventLine(at=0.0, tool="Bash", summary="Run the suite")).endswith(
        " Run the suite"
    )
    assert "Bash" not in _event(EventLine(at=0.0, tool="Bash", summary="Run the suite"))


def test_a_label_less_call_still_names_its_tool():
    from aegis.fleet.render import _event

    assert _event(EventLine(at=0.0, tool="Mystery", summary="")).endswith(" Mystery")
