"""AgentSession feeds the RepoTracker from its own event stream."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from aegis.core.session import AgentSession
from aegis.events import ToolUse
from aegis.repos.models import RepoState
from aegis.repos.tracker import RepoTracker


class _Recorder:
    """Stands in for RepoTracker at the seam AgentSession actually uses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def record(self, handle, path, *, host="local") -> None:
        self.calls.append((handle, str(path), host))


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "aegis"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    return root


def _session(tmp_path, tracker=None, host="local"):
    from aegis.hosts.models import Place
    agent = SimpleNamespace(harness="claude", model="opus", effort="high")
    return AgentSession(SimpleNamespace(), agent, "main", "alice",
                        project_root=tmp_path,
                        place=Place(host, str(tmp_path)),
                        repo_tracker=tracker)


def test_a_write_is_recorded_against_the_sessions_handle(tmp_path, repo):
    rec = _Recorder()
    sess = _session(tmp_path, rec)
    sess._fire_event(ToolUse(name="Write", summary="",
                             raw_input={"file_path": str(repo / "x.py")}))
    assert rec.calls == [("alice", str(repo / "x.py"), "local")]


def test_a_read_records_nothing(tmp_path, repo):
    rec = _Recorder()
    sess = _session(tmp_path, rec)
    sess._fire_event(ToolUse(name="Read", summary="",
                             raw_input={"file_path": str(repo / "x.py")}))
    assert rec.calls == []


def test_a_bash_call_records_nothing(tmp_path, repo):
    rec = _Recorder()
    sess = _session(tmp_path, rec)
    sess._fire_event(ToolUse(
        name="Bash", summary="",
        raw_input={"command": f"sed -i s/a/b/ {repo / 'x.py'}"}))
    assert rec.calls == []


def test_a_subagents_write_lands_under_the_parent_handle(tmp_path, repo):
    """A subagent has no handle of its own, and the repo is this session's
    responsibility either way."""
    rec = _Recorder()
    sess = _session(tmp_path, rec)
    sess._fire_event(ToolUse(name="Edit", summary="",
                             raw_input={"file_path": str(repo / "x.py")},
                             parent_tool_use_id="toolu_01"))
    assert rec.calls == [("alice", str(repo / "x.py"), "local")]


def test_the_sessions_host_travels_with_the_write(tmp_path, repo):
    rec = _Recorder()
    sess = _session(tmp_path, rec, host="vps")
    sess._fire_event(ToolUse(name="Write", summary="",
                             raw_input={"file_path": "/srv/warden/x.py"}))
    assert rec.calls == [("alice", "/srv/warden/x.py", "vps")]


def test_no_tracker_attached_is_a_no_op(tmp_path, repo):
    sess = _session(tmp_path, None)
    sess._fire_event(ToolUse(name="Write", summary="",
                             raw_input={"file_path": str(repo / "x.py")}))
    # Reaching here without raising is the assertion.


def test_a_raising_tracker_never_takes_the_turn_down(tmp_path, repo):
    class Boom:
        def record(self, *a, **k):
            raise RuntimeError("board on fire")

    sess = _session(tmp_path, Boom())
    sess._fire_event(ToolUse(name="Write", summary="",
                             raw_input={"file_path": str(repo / "x.py")}))


def test_end_to_end_through_a_real_tracker(tmp_path, repo):
    tracker = RepoTracker(probe=lambda root: RepoState(root=root))
    sess = _session(tmp_path, tracker)
    sess._fire_event(ToolUse(name="Write", summary="",
                             raw_input={"file_path": str(repo / "x.py")}))
    views = tracker.snapshot(for_handle="alice")
    assert [v.state.name for v in views] == ["aegis"]
    assert views[0].mine is True
    assert views[0].state.branch == "main"


def test_replayed_history_does_not_repopulate_the_board(tmp_path, repo):
    """A restart clears the board. `_fire_event` is the live path only —
    the replay walks events without firing them, and a resumed session that
    resurrected every repo its transcript ever mentioned would report
    agents standing in repos nobody is in."""
    from aegis.events import Result

    tracker = RepoTracker(probe=lambda root: RepoState(root=root))
    sess = _session(tmp_path, tracker)
    events = [ToolUse(name="Write", summary="",
                      raw_input={"file_path": str(repo / "x.py")}),
              Result(duration_ms=1, is_error=False)]
    sess.rehydrate_plan(events, [1.0, 2.0])
    assert tracker.snapshot() == []
