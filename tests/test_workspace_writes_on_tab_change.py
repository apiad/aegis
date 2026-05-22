"""Workspace.json reflects the live tab roster.

Uses a stub AegisApp surface: we don't run Textual, we just exercise the
state-mutation hooks (open, close, activate, reorder) that the real app
also calls, and assert the on-disk workspace.json matches after each.
"""
from pathlib import Path

from aegis.state.workspace import Workspace, WorkspaceTab, load, state_dir
from aegis.tui.app import write_workspace_snapshot  # to be added


def _tab(handle, profile, order, provider, sid="sid-" + "x"):
    return WorkspaceTab(handle=handle, profile=profile, order=order,
                        provider=provider, session_id=sid,
                        created_at="2026-05-21T00:00:00Z")


def test_snapshot_reflects_single_tab(tmp_path):
    sd = state_dir(tmp_path)
    tabs = [_tab("lucid-knuth", "default", 0, "claude-code")]
    write_workspace_snapshot(sd, tabs=tabs, active_handle="lucid-knuth")
    ws = load(sd)
    assert ws == Workspace(active_handle="lucid-knuth", tabs=tabs)


def test_snapshot_after_close_drops_tab(tmp_path):
    sd = state_dir(tmp_path)
    tabs = [_tab("a", "p", 0, "claude-code"),
            _tab("b", "p", 1, "claude-code")]
    write_workspace_snapshot(sd, tabs=tabs, active_handle="b")
    write_workspace_snapshot(sd, tabs=[tabs[1]], active_handle="b")
    assert load(sd).tabs == [tabs[1]]


def test_snapshot_after_reorder(tmp_path):
    sd = state_dir(tmp_path)
    tabs = [_tab("a", "p", 0, "claude-code"),
            _tab("b", "p", 1, "claude-code")]
    write_workspace_snapshot(sd, tabs=tabs, active_handle="a")
    reordered = [_tab("b", "p", 0, "claude-code"),
                 _tab("a", "p", 1, "claude-code")]
    write_workspace_snapshot(sd, tabs=reordered, active_handle="a")
    assert load(sd).tabs == reordered


def test_session_log_observer_writes_events_for_handle(tmp_path):
    """Each pane subscribes an observer that appends incoming events."""
    from aegis.events import AssistantText, Result, SystemInit
    from aegis.state.session_log import replay_events
    from aegis.tui.pane import make_session_log_observer  # to be added

    sd = state_dir(tmp_path)
    obs = make_session_log_observer(sd, handle="lucid-knuth")

    class _FakeSession:
        handle = "lucid-knuth"

    sess = _FakeSession()
    obs(sess, SystemInit(session_id="xyz"))
    obs(sess, AssistantText(text="hi", usage=None))
    obs(sess, Result(duration_ms=1, is_error=False))

    rep = replay_events(sd, "lucid-knuth")
    assert [type(e).__name__ for e in rep.events] == [
        "SystemInit", "AssistantText", "Result"]
    assert rep.interrupted is False
