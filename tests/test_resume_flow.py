"""End-to-end resume: given a workspace.json + per-tab JSONL on disk and
a stubbed driver registry, the bootstrap function should:
  - call driver.resume(session_id) for resumable tabs in order
  - skip the others
  - return a startup-banner string listing skips (or '' if none)
"""
from aegis.events import AssistantText, Result, SystemInit
from aegis.state.session_log import append_event
from aegis.state.workspace import (
    Workspace, WorkspaceTab, save, state_dir,
)
from aegis.tui.app import bootstrap_resume


class StubSession:
    def __init__(self): self.opened = True


class StubDriver:
    supports_resume = True
    def __init__(self): self.resume_calls = []
    def resume(self, agent, cwd, mcp_url, handle, session_id):
        self.resume_calls.append((handle, session_id))
        return StubSession()


class StubNoResumeDriver:
    supports_resume = False


def test_bootstrap_resume_opens_resumable_and_skips_others(tmp_path):
    sd = state_dir(tmp_path)
    save(sd, Workspace(active_handle="ok", tabs=[
        WorkspaceTab(handle="ok", profile="default", order=0,
                     provider="claude-code", session_id="sid-1",
                     created_at="2026-05-21T00:00:00Z"),
        WorkspaceTab(handle="gem", profile="default", order=1,
                     provider="gemini", session_id="sid-2",
                     created_at="2026-05-21T00:00:00Z"),
    ]))
    append_event(sd, "ok", SystemInit(session_id="sid-1"))
    append_event(sd, "ok", AssistantText(text="hello", usage=None))
    append_event(sd, "ok", Result(duration_ms=1, is_error=False))

    drv_c = StubDriver()
    drv_g = StubNoResumeDriver()
    opens = []
    banner = bootstrap_resume(
        state_dir_path=sd,
        ws=None,  # load from disk inside
        agents={"default": object()},
        drivers={"claude-code": drv_c, "gemini": drv_g},
        cwd=str(tmp_path), mcp_url="http://x",
        open_tab=lambda *, handle, replay, session: opens.append(
            (handle, len(replay.events), session.opened)),
    )
    assert opens == [("ok", 3, True)]
    assert drv_c.resume_calls == [("ok", "sid-1")]
    assert "skipped 1" in banner


def test_bootstrap_resume_zero_resumable_returns_signal(tmp_path):
    sd = state_dir(tmp_path)
    save(sd, Workspace(active_handle="gem", tabs=[
        WorkspaceTab(handle="gem", profile="default", order=0,
                     provider="gemini", session_id="sid-2",
                     created_at="2026-05-21T00:00:00Z"),
    ]))
    opens = []
    banner = bootstrap_resume(
        state_dir_path=sd, ws=None,
        agents={"default": object()},
        drivers={"gemini": StubNoResumeDriver()},
        cwd=str(tmp_path), mcp_url="http://x",
        open_tab=lambda **kw: opens.append(kw))
    assert opens == []
    assert banner.startswith("no resumable")


def test_bootstrap_resume_with_no_workspace_returns_empty(tmp_path):
    sd = state_dir(tmp_path)
    banner = bootstrap_resume(
        state_dir_path=sd, ws=None,
        agents={"default": object()},
        drivers={"claude-code": StubDriver()},
        cwd=str(tmp_path), mcp_url="http://x",
        open_tab=lambda **kw: None)
    assert banner == ""
