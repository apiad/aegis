"""When a pane's underlying session has latched a session_id, the next
workspace snapshot carries it."""
from aegis.state.workspace import WorkspaceTab, load, state_dir
from aegis.tui.app import write_workspace_snapshot, _pane_to_tab


def test_session_id_propagates_into_snapshot(tmp_path):
    sd = state_dir(tmp_path)
    tabs = [WorkspaceTab(
        handle="lucid-knuth", profile="default", order=0,
        provider="claude-code", session_id="abc-123",
        created_at="2026-05-21T00:00:00Z")]
    write_workspace_snapshot(sd, tabs=tabs, active_handle="lucid-knuth")
    ws = load(sd)
    assert ws.tabs[0].session_id == "abc-123"


def test_pane_to_tab_reads_session_id_from_core(tmp_path):
    """_pane_to_tab should pull session_id via the AgentSession's accessor,
    not via a private attribute."""
    class _FakeCore:
        session_id = "sid-xyz"

    class _FakeAgent:
        harness = "claude-code"

    class _FakePane:
        handle = "h"
        agent_slug = "default"
        _agent = _FakeAgent()
        _core = _FakeCore()
        _created_at = "2026-05-21T00:00:00Z"

    tab = _pane_to_tab(_FakePane(), order=0)
    assert tab.session_id == "sid-xyz"
    assert tab.provider == "claude-code"
    assert tab.handle == "h"
    assert tab.profile == "default"
    assert tab.order == 0
