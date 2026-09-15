"""Every tab a daemon view opens is a brain session.

The MCP plane serves the brain, so a session a view builds for itself is
invisible to every agent: its harness gets no token, it is absent from
aegis_list_sessions, and read_peer, handoff, rename and /loop all answer
"unknown session". On 2026-09-14 that was every tab opened with /spawn,
while Ctrl+N worked, because only `_spawn` had been moved onto the brain.

Each test opens a tab one of the other ways and asks the brain, not the
view, whether it exists and whether its harness was given a token.
"""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.drivers import DRIVERS
from aegis.state.history import SessionHistoryRow
from aegis.state.workspace import Workspace, WorkspaceTab, save
from aegis.views.registry import ViewRegistry

from tests.brain import make_brain
from tests.views.conftest import FakeMCP


class _FakeHarness:
    session_id = "sid-1"

    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


def _reg(tmp_path):
    """A registry over one brain, sharing one MCP plane, with every
    factory call recorded as (handle, kwargs)."""
    calls: list[tuple[str, dict]] = []

    def factory(profile, url, handle, **kw):
        calls.append((handle, kw))
        return _FakeHarness()

    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": _agent()}
    mcp = FakeMCP()
    mgr = make_brain(roster, "default", make_session=factory, mcp=mcp,
                     roots=roots)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=mcp,
                       agents=roster, default_agent="default",
                       make_session=factory,
                       drivers={s: c() for s, c in DRIVERS.items()})
    return reg, mgr, calls


def _brain_handles(mgr):
    return {s.handle for s in mgr.list_sessions()}


def _call_for(calls, handle):
    return next(kw for h, kw in reversed(calls) if h == handle)


async def test_slash_spawn_in_a_view_opens_a_brain_session(tmp_path):
    """/spawn reaches the app through ctx.bridge.spawn."""
    reg, mgr, calls = _reg(tmp_path)
    v = await reg.open("tty-1", (100, 30))
    async with v.app.run_test(headless=False, size=(100, 30)) as pilot:
        await pilot.pause()
        h = await v.app.spawn("default", spawned_by="someone")
        await pilot.pause()
        assert h in _brain_handles(mgr), "the brain never heard of it"
        assert _call_for(calls, h).get("token") == "test-token"
        assert v.app.pane_for(h) is not None, "the view shows no tab"
        assert mgr.get(h).spawned_by == "someone"
    await reg.close_all()


async def test_slash_fork_in_a_view_opens_a_brain_session(tmp_path):
    reg, mgr, calls = _reg(tmp_path)
    v = await reg.open("tty-1", (100, 30))
    async with v.app.run_test(headless=False, size=(100, 30)) as pilot:
        await pilot.pause()
        parent = await mgr.spawn("default")
        await pilot.pause()
        child = await v.app.fork(parent, forked_by=parent)
        await pilot.pause()
        assert child in _brain_handles(mgr), "the brain never heard of it"
        kw = _call_for(calls, child)
        assert kw.get("fork_from") == "sid-1"
        assert kw.get("token") == "test-token"
        assert v.app.pane_for(child) is not None, "the view shows no tab"
    await reg.close_all()


async def test_reopening_from_history_in_a_view_opens_a_brain_session(
        tmp_path):
    """Ctrl+R, resume."""
    reg, mgr, calls = _reg(tmp_path)
    v = await reg.open("tty-1", (100, 30))
    row = SessionHistoryRow(
        log_id="20260914T000000000000Z-old-tab", handle="old-tab",
        profile="default", provider="claude-code", cwd=str(tmp_path),
        created_at="2026-09-14T00:00:00Z", closed_at="2026-09-14T01:00:00Z",
        last_activity_at="2026-09-14T01:00:00Z", preview="",
        session_id="sid-old", is_open=False, crash_inferred=False)
    async with v.app.run_test(headless=False, size=(100, 30)) as pilot:
        await pilot.pause()
        await v.app._resume_from_history(row)
        await pilot.pause()
        assert "old-tab" in _brain_handles(mgr), "the brain never heard of it"
        kw = _call_for(calls, "old-tab")
        assert kw.get("resume_from") == "sid-old"
        assert kw.get("token") == "test-token"
        # The reopened tab keeps writing to the log it had.
        assert mgr.get("old-tab").log_id == row.log_id
        assert v.app.pane_for("old-tab") is not None, "the view shows no tab"
    await reg.close_all()


async def test_tabs_restored_at_boot_are_brain_sessions(tmp_path):
    reg, mgr, calls = _reg(tmp_path)
    roots = AegisRoots.for_project(tmp_path)
    save(roots.state_dir, Workspace(tabs=[WorkspaceTab(
        handle="kept-tab", profile="default", order=0,
        provider="claude-code", session_id="sid-kept",
        created_at="2026-09-14T00:00:00Z",
        log_id="20260914T000000000000Z-kept-tab")]))
    v = await reg.open("tty-1", (100, 30))
    async with v.app.run_test(headless=False, size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause()
        assert "kept-tab" in _brain_handles(mgr), "restored only in the view"
        kw = _call_for(calls, "kept-tab")
        assert kw.get("resume_from") == "sid-kept"
        assert kw.get("token") == "test-token"
        assert mgr.get("kept-tab").log_id == "20260914T000000000000Z-kept-tab"
        assert v.app.pane_for("kept-tab") is not None
    await reg.close_all()


async def test_reconnect_in_a_view_goes_to_the_brain(tmp_path, monkeypatch):
    reg, mgr, _calls = _reg(tmp_path)
    seen: list[str] = []

    async def fake_reconnect(handle):
        seen.append(handle)
        return f"reconnected {handle}"

    monkeypatch.setattr(mgr, "reconnect", fake_reconnect)
    v = await reg.open("tty-1", (100, 30))
    async with v.app.run_test(headless=False, size=(100, 30)) as pilot:
        await pilot.pause()
        h = await mgr.spawn("default")
        await pilot.pause()
        assert await v.app.reconnect(h) == f"reconnected {h}"
        assert seen == [h]
    await reg.close_all()


async def test_a_view_refuses_to_build_a_session_of_its_own(tmp_path):
    """The rule, enforced rather than remembered: the next path someone
    adds to a view fails here instead of in production."""
    from aegis.tui.app import _SessionManagerAdapter

    reg, _mgr, _calls = _reg(tmp_path)
    v = await reg.open("tty-1", (100, 30))
    async with v.app.run_test(headless=False, size=(100, 30)) as pilot:
        await pilot.pause()
        with pytest.raises(RuntimeError, match="brain"):
            _SessionManagerAdapter(v.app).spawn("default")
        with pytest.raises(RuntimeError, match="brain"):
            _SessionManagerAdapter(v.app).fork("anything")
    await reg.close_all()
