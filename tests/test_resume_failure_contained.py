"""When driver.resume raises for one tab, other tabs still open and the
failure is contained in its own pane."""
import aegis.tui.app as app_mod
from aegis.state.session_log import EventReplay, append_event
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


class OkDriver:
    supports_resume = True

    def resume(self, agent, cwd, mcp_url, handle, session_id):
        class S:
            pass
        return S()


def _two_tab_workspace(sd):
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


def test_unreadable_transcript_does_not_abort_the_boot(tmp_path, monkeypatch):
    """Replay ran outside the per-tab guard, so anything it raised escaped
    on_mount and took every tab down with it — one damaged transcript cost
    the whole workspace. A tab may lose its scrollback; it may not lose the
    other tabs."""
    sd = state_dir(tmp_path)
    _two_tab_workspace(sd)

    def boom(state_dir_path, handle):
        if handle == "a":
            raise OSError("permission denied")
        return EventReplay(events=[], interrupted=False)

    monkeypatch.setattr(app_mod, "replay_events", boom, raising=False)
    monkeypatch.setattr("aegis.state.session_log.replay_events", boom)

    opened = []
    bootstrap_resume(
        state_dir_path=sd, ws=None,
        agents={"default": object()},
        drivers={"claude-code": OkDriver()},
        cwd=str(tmp_path), mcp_url="http://x",
        open_tab=lambda **kw: opened.append((kw["handle"], kw["replay"])))

    assert [h for h, _ in opened] == ["a", "b"]
    # The tab whose transcript could not be read opens empty, not missing.
    by_handle = dict(opened)
    assert by_handle["a"].events == []
