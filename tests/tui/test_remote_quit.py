"""Tests for B1: action_quit crashes in remote mode.

AegisApp in remote mode must:
- Call _remote_manager.close() (not inbox_router / queue_digest / _mcp)
- Then call self.exit()
- Not raise any exception on Ctrl+Q
"""
from __future__ import annotations

import pytest
import asyncio


# ---------------------------------------------------------------------------
# Fake manager
# ---------------------------------------------------------------------------

class _FakeRemoteManager:
    """Minimal fake RemoteSessionManager for action_quit tests."""

    def __init__(self) -> None:
        self.close_called = False
        self._ws = _FakeWs()
        self._tunnel = None
        # Aux plane stubs (needed by AegisApp.__init__)
        from aegis.tui.remote_manager import _DisabledPlane
        self.queue_manager = _DisabledPlane("queue_manager")
        self.inbox_router = _DisabledPlane("inbox_router")
        self.canvas_manager = _DisabledPlane("canvas_manager")
        self.terminal_manager = _DisabledPlane("terminal_manager")
        self.groups = _DisabledPlane("groups")
        self.locks = _DisabledPlane("locks")
        self.workflow_registry = _DisabledPlane("workflow_registry")
        self.remotes: dict = {}
        self.scheduler = None
        import pathlib
        self.state_root = pathlib.Path.cwd()

    async def shutdown(self) -> None:
        self.close_called = True

    def list_sessions(self):
        return []

    def list_agents(self):
        return ["main"]

    def inline_schedule_names(self):
        return set()


class _FakeWs:
    def on(self, kind, fn):
        pass

    def on_connection(self, fn):
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_action_quit_remote_calls_manager_close(monkeypatch):
    """action_quit in remote mode must call _remote_manager.close() without
    touching inbox_router / queue_digest / _mcp, which are all stubs."""
    from aegis.tui.app import AegisApp

    mgr = _FakeRemoteManager()
    app = AegisApp(
        agents={"main": None},
        default_agent="main",
        make_session=None,
        mcp=None,
        manager=mgr,
    )

    exit_called = []

    def fake_exit(*args, **kwargs):
        exit_called.append(True)

    monkeypatch.setattr(app, "exit", fake_exit)

    # action_quit must not raise and must call manager.close()
    await app.action_quit()

    assert mgr.close_called, "manager.shutdown() was not called"
    assert exit_called, "app.exit() was not called"


@pytest.mark.asyncio
async def test_action_quit_remote_does_not_touch_inbox_router(monkeypatch):
    """action_quit must NOT call inbox_router.unbind_session in remote mode.
    That would raise RemoteUnsupportedError on the _DisabledPlaneStub."""
    from aegis.tui.app import AegisApp
    from aegis.tui.remote_manager import RemoteUnsupportedError

    mgr = _FakeRemoteManager()
    app = AegisApp(
        agents={"main": None},
        default_agent="main",
        make_session=None,
        mcp=None,
        manager=mgr,
    )

    monkeypatch.setattr(app, "exit", lambda *a, **k: None)

    # Must not raise RemoteUnsupportedError
    try:
        await app.action_quit()
    except RemoteUnsupportedError as exc:
        pytest.fail(f"action_quit raised RemoteUnsupportedError: {exc}")


@pytest.mark.asyncio
async def test_action_quit_remote_does_not_touch_mcp(monkeypatch):
    """action_quit must NOT call self._mcp.stop() in remote mode
    (_mcp is None → AttributeError)."""
    from aegis.tui.app import AegisApp

    mgr = _FakeRemoteManager()
    app = AegisApp(
        agents={"main": None},
        default_agent="main",
        make_session=None,
        mcp=None,
        manager=mgr,
    )

    monkeypatch.setattr(app, "exit", lambda *a, **k: None)

    # Must not raise AttributeError ('NoneType' has no attribute 'stop')
    try:
        await app.action_quit()
    except AttributeError as exc:
        pytest.fail(f"action_quit raised AttributeError: {exc}")
