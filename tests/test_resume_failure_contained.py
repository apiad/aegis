"""When driver.resume raises for one tab, other tabs still open and the
failure is contained in its own pane."""
from aegis.state.session_log import append_event
from aegis.state.workspace import (
    Workspace, WorkspaceTab, save, state_dir,
)
from aegis.events import SystemInit
from aegis.tui.app import bootstrap_resume


class FlakyDriver:
    supports_resume = True
    def __init__(self, fail_handle): self.fail_handle = fail_handle
    def resume(self, agent, cwd, mcp_url, handle, session_id):
        if handle == self.fail_handle:
            raise RuntimeError("session expired")
        class S: pass
        return S()


def test_one_tab_fails_others_open(tmp_path):
    sd = state_dir(tmp_path)
    save(sd, Workspace(active_handle="a", tabs=[
        WorkspaceTab(handle="a", profile="default", order=0,
                     provider="claude-code", session_id="sid-a",
                     created_at="2026-05-21T00:00:00Z"),
        WorkspaceTab(handle="b", profile="default", order=1,
                     provider="claude-code", session_id="sid-b",
                     created_at="2026-05-21T00:00:00Z"),
    ]))
    append_event(sd, "a", SystemInit(session_id="sid-a"))
    append_event(sd, "b", SystemInit(session_id="sid-b"))

    events = []
    bootstrap_resume(
        state_dir_path=sd, ws=None,
        agents={"default": object()},
        drivers={"claude-code": FlakyDriver(fail_handle="a")},
        cwd=str(tmp_path), mcp_url="http://x",
        open_tab=lambda **kw: events.append(("ok", kw["handle"])),
        open_failed_tab=lambda **kw: events.append(("fail", kw["handle"], kw["reason"])))
    # Both handles produce an event; one is success, one is failure.
    kinds = {e[0] for e in events}
    assert kinds == {"ok", "fail"}
    fail = next(e for e in events if e[0] == "fail")
    assert fail[1] == "a"
    assert "session expired" in fail[2]
