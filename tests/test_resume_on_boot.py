"""AegisApp on_mount restores tabs from workspace.json instead of spawning
a fresh default tab. Closes the long-standing TODO in app.py: the
bootstrap_resume orchestrator existed but was never wired into the live
boot path, so every relaunch lost the prior workspace.

Also covers the quit-time snapshot — action_quit must persist session_ids
latched mid-session so the next boot can resume.
"""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.events import AssistantText, Result, SystemInit
from aegis.state.session_log import append_event
from aegis.state.workspace import (
    Workspace, WorkspaceTab, load, save, state_dir,
)
from aegis.tui.app import AegisApp


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    """Same shape as test_tui.FakeSession but with a session_id attribute
    so the quit-snapshot test can latch one."""

    def __init__(self, script=None, session_id=None):
        self.sent = []
        self.started = self.closed = False
        self.session_id = session_id
        self._script = script or (
            lambda t: [AssistantText(f"echo: {t}"),
                       Result(duration_ms=10, is_error=False)])

    async def start(self): self.started = True
    async def send(self, text): self.sent.append(text)
    async def events(self):
        for ev in self._script(self.sent[-1]):
            yield ev
    async def close(self): self.closed = True


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def __init__(self):
        self.started = False
        self.stopped = False
        self.bound = None

    def bind(self, bridge): self.bound = bridge
    async def start(self): self.started = True
    async def stop(self): self.stopped = True


def _factory(*sessions):
    it = iter(sessions or (FakeSession(),))
    made = []

    def make(agent, mcp_url, handle):
        try:
            s = next(it)
        except StopIteration:
            s = FakeSession()
        made.append(s)
        return s

    make.made = made
    return make


class StubResumeDriver:
    supports_resume = True

    def __init__(self, sessions):
        self._sessions = list(sessions)
        self.calls: list[tuple[str, str]] = []

    def resume(self, agent, cwd, mcp_url, handle, session_id):
        self.calls.append((handle, session_id))
        return self._sessions.pop(0)


@pytest.mark.asyncio
async def test_resumes_tabs_from_workspace_on_boot(tmp_path, monkeypatch):
    """workspace.json with two resumable tabs → boot opens both, skips
    the default-agent spawn."""
    monkeypatch.chdir(tmp_path)
    sd = state_dir(tmp_path)
    save(sd, Workspace(active_handle="beta", tabs=[
        WorkspaceTab(handle="alpha", profile="default", order=0,
                     provider="claude-code", session_id="sid-A",
                     created_at="2026-05-27T00:00:00Z"),
        WorkspaceTab(handle="beta", profile="default", order=1,
                     provider="claude-code", session_id="sid-B",
                     created_at="2026-05-27T00:00:00Z"),
    ]))
    append_event(sd, "alpha", SystemInit(session_id="sid-A"))
    append_event(sd, "beta", SystemInit(session_id="sid-B"))

    drv = StubResumeDriver([FakeSession(session_id="sid-A"),
                            FakeSession(session_id="sid-B")])
    # Default-agent factory should NOT be consumed when resume succeeds.
    fact = _factory(FakeSession())
    app = AegisApp({"default": _agent()}, "default", fact, FakeMCP(),
                   drivers={"claude-code": drv}, cwd=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        handles = [p.handle for p in app._panes]
        assert handles == ["alpha", "beta"]
        assert drv.calls == [("alpha", "sid-A"), ("beta", "sid-B")]
        # Default factory was not used.
        assert fact.made == []
        # Active tab is the previously-active "beta".
        assert app._active is not None
        assert app._active.handle == "beta"


@pytest.mark.asyncio
async def test_no_resume_when_clean_flag_set(tmp_path, monkeypatch):
    """`aegis --clean` overrides the resume path even if workspace.json
    is intact."""
    monkeypatch.chdir(tmp_path)
    sd = state_dir(tmp_path)
    save(sd, Workspace(active_handle="alpha", tabs=[
        WorkspaceTab(handle="alpha", profile="default", order=0,
                     provider="claude-code", session_id="sid-A",
                     created_at="2026-05-27T00:00:00Z"),
    ]))
    drv = StubResumeDriver([FakeSession()])
    app = AegisApp({"default": _agent()}, "default", _factory(FakeSession()),
                   FakeMCP(), drivers={"claude-code": drv},
                   cwd=str(tmp_path), clean=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Default spawn fires, resume does not.
        assert len(app._panes) == 1
        assert app._panes[0].handle != "alpha"
        assert drv.calls == []


@pytest.mark.asyncio
async def test_fallthrough_to_default_spawn_when_nothing_resumable(
        tmp_path, monkeypatch):
    """workspace.json with only un-resumable tabs (session_id=null) →
    fall through to the default-agent spawn so the user gets a working
    blank tab instead of an empty app."""
    monkeypatch.chdir(tmp_path)
    sd = state_dir(tmp_path)
    save(sd, Workspace(active_handle="alpha", tabs=[
        WorkspaceTab(handle="alpha", profile="default", order=0,
                     provider="claude-code", session_id=None,
                     created_at="2026-05-27T00:00:00Z"),
    ]))
    drv = StubResumeDriver([])
    app = AegisApp({"default": _agent()}, "default",
                   _factory(FakeSession()), FakeMCP(),
                   drivers={"claude-code": drv}, cwd=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app._panes) == 1
        assert drv.calls == []  # no session_id → plan_resume skipped it


@pytest.mark.asyncio
async def test_files_resume_on_boot(tmp_path, monkeypatch):
    """File tabs recorded in workspace.json are re-opened on boot."""
    from aegis.state.workspace import WorkspaceFile
    from aegis.tui.file_tab import FileTab

    monkeypatch.chdir(tmp_path)
    a = tmp_path / "alpha.md"
    b = tmp_path / "beta.md"
    a.write_text("alpha\n"); b.write_text("beta\n")
    sd = state_dir(tmp_path)
    save(sd, Workspace(active_handle=None, tabs=[], files=[
        WorkspaceFile(path=str(a), order=0,
                      created_at="2026-05-27T00:00:00Z"),
        WorkspaceFile(path=str(b), order=1,
                      created_at="2026-05-27T00:00:00Z"),
    ]))
    app = AegisApp({"default": _agent()}, "default",
                   _factory(FakeSession()), FakeMCP(),
                   cwd=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        file_tabs = [p for p in app._panes if isinstance(p, FileTab)]
        assert {str(t._path) for t in file_tabs} == {str(a), str(b)}


@pytest.mark.asyncio
async def test_terminals_resume_even_with_default_spawn(tmp_path, monkeypatch):
    """Regression: terminals must restore even when no agent tabs
    resumed (so the default spawn ran). Pre-fix, the default spawn
    overwrote workspace.json with terminals=[] before
    _maybe_resume_terminals had a chance to read it."""
    from aegis.state.workspace import WorkspaceTerminal
    from aegis.tui.terminal_tab import TerminalTab

    monkeypatch.chdir(tmp_path)
    sd = state_dir(tmp_path)
    save(sd, Workspace(active_handle=None, tabs=[], terminals=[
        WorkspaceTerminal(name="t1", shell="/bin/sh", cwd=str(tmp_path),
                          created_at="2026-05-27T00:00:00Z"),
    ]))
    app = AegisApp({"default": _agent()}, "default",
                   _factory(FakeSession()), FakeMCP(),
                   cwd=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        terms = [p for p in app._panes if isinstance(p, TerminalTab)]
        assert len(terms) == 1
        assert terms[0]._info.name == "t1"
        # Tear down the spawned PTY subprocess explicitly — otherwise
        # the test hangs at exit waiting on the child.
        await pilot.press("ctrl+q")


@pytest.mark.asyncio
async def test_quit_writes_snapshot_with_session_ids(tmp_path, monkeypatch):
    """Ctrl+Q must persist the current roster — including session_ids that
    were latched after the last tab event — so the next boot can resume."""
    monkeypatch.chdir(tmp_path)
    sess = FakeSession()
    app = AegisApp({"default": _agent()}, "default", _factory(sess),
                   FakeMCP(), cwd=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Simulate: Claude SystemInit landed AFTER the last tab refresh,
        # so the in-memory session_id is set but the on-disk snapshot
        # still shows null.
        sess.session_id = "sid-LATCHED"
        await pilot.press("ctrl+q")
    ws = load(state_dir(tmp_path))
    assert ws is not None
    assert len(ws.tabs) == 1
    assert ws.tabs[0].session_id == "sid-LATCHED"
