"""Integration tests for history wiring through AegisApp.

The rig stubs the make_session factory so we exercise the persistence
pipeline without spawning real harness subprocesses.
"""
from pathlib import Path

import pytest

from aegis.config import Agent
from aegis.events import AssistantText, Result, SessionClosed, SessionMeta
from aegis.state.session_log import replay_events


def _agent():
    return Agent(harness="claude-code", model="claude-sonnet-4-5",
                 effort="medium", permission="auto")


class FakeSession:
    def __init__(self):
        self.sent = []
        self.started = self.closed = False
        self.session_id = None

    async def start(self): self.started = True
    async def send(self, text): self.sent.append(text)
    async def events(self):
        yield AssistantText("ok", usage=None)
        yield Result(duration_ms=1, is_error=False)
    async def close(self): self.closed = True


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): self.bound = bridge
    async def start(self): pass
    async def stop(self): pass


def _factory(agent, mcp_url, handle):
    return FakeSession()


def _app():
    return AegisApp({"sonnet": _agent()}, "sonnet", _factory, FakeMCP())


from aegis.tui.app import AegisApp  # noqa: E402  (after Fakes for readability)


@pytest.mark.asyncio
async def test_first_message_writes_session_meta(tmp_path: Path, monkeypatch):
    """Meta is written on the first user turn (not at spawn), with the
    message as preview."""
    monkeypatch.chdir(tmp_path)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        sessions_dir = tmp_path / ".aegis" / "state" / "sessions"
        # Pre-first-message: no meta header yet.
        if sessions_dir.is_dir():
            for f in sessions_dir.glob("*.jsonl"):
                replay = replay_events(tmp_path / ".aegis" / "state", f.stem)
                assert not any(isinstance(e, SessionMeta)
                               for e in replay.events)
        # Send a message through the active pane.
        active = app._active
        active._submit("hello world")
        await pilot.pause()
        # Now meta is present, preview populated.
        log_files = list(sessions_dir.glob("*.jsonl"))
        assert len(log_files) == 1
        replay = replay_events(tmp_path / ".aegis" / "state",
                               log_files[0].stem)
        metas = [e for e in replay.events if isinstance(e, SessionMeta)]
        assert len(metas) == 1
        assert metas[0].preview == "hello world"
        assert metas[0].provider == "claude-code"
        assert metas[0].origin == "tui"


@pytest.mark.asyncio
async def test_meta_written_only_once_per_session(tmp_path: Path,
                                                  monkeypatch):
    """A second user turn does not append a duplicate meta header."""
    monkeypatch.chdir(tmp_path)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        active = app._active
        # Record twice directly — the guard must fire the hook only once.
        active._record_first_user_message("first")
        active._record_first_user_message("second")
        await pilot.pause()
        sessions_dir = tmp_path / ".aegis" / "state" / "sessions"
        log_files = list(sessions_dir.glob("*.jsonl"))
        replay = replay_events(tmp_path / ".aegis" / "state",
                               log_files[0].stem)
        metas = [e for e in replay.events if isinstance(e, SessionMeta)]
        assert len(metas) == 1
        assert metas[0].preview == "first"


@pytest.mark.asyncio
async def test_ctrl_h_opens_history_modal(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from aegis.tui.history import HistoryModal
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+h")
        await pilot.pause()
        assert isinstance(app.screen, HistoryModal)


@pytest.mark.asyncio
async def test_history_resume_calls_driver_resume(tmp_path: Path, monkeypatch):
    """A closed row with a session_id and a resume-capable provider routes
    Enter through drv.resume() with the recorded handle + session_id."""
    monkeypatch.chdir(tmp_path)
    from datetime import datetime, timezone
    from unittest.mock import MagicMock
    from aegis.events import SessionMeta, SystemInit
    from aegis.state.session_log import append_event, append_meta

    sd = tmp_path / ".aegis" / "state"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    append_meta(sd, SessionMeta(
        handle="prior", profile="sonnet", provider="claude-code",
        cwd=str(tmp_path), created_at=now, origin="tui", preview="prior work"))
    append_event(sd, "prior", SystemInit(session_id="upstream-1"))

    fake_driver = MagicMock()
    fake_driver.supports_resume = True
    fake_driver.resume = MagicMock(return_value=FakeSession())

    app = AegisApp({"sonnet": _agent()}, "sonnet", _factory, FakeMCP(),
                   drivers={"claude-code": fake_driver})
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+h")
        await pilot.pause()
        # Only "prior" is in history (the live default tab has no meta yet).
        for ch in "prior":
            await pilot.press(ch)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        fake_driver.resume.assert_called_once()
        args = fake_driver.resume.call_args.args
        # resume(agent, cwd, mcp_url, handle, session_id)
        assert args[3] == "prior"
        assert args[4] == "upstream-1"
        # A pane for the resumed handle now exists.
        assert any(p.handle == "prior" for p in app._panes)


@pytest.mark.asyncio
async def test_close_pane_writes_session_closed(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        target = app._panes[0].handle
        # Give tab 0 a meta header (first user message), then a 2nd tab so
        # closing the first doesn't exit the app.
        app._panes[0]._submit("hello")
        await pilot.pause()
        await app._spawn("sonnet")
        await pilot.pause()
        app._activate(0)
        await pilot.pause()
        await pilot.press("ctrl+w")
        await pilot.pause()
        events = replay_events(tmp_path / ".aegis" / "state", target).events
        closed = [e for e in events if isinstance(e, SessionClosed)]
        assert len(closed) == 1
        assert closed[0].reason == "user"


@pytest.mark.asyncio
async def test_record_session_closed_is_idempotent(tmp_path: Path,
                                                   monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        h = app._panes[0].handle
        app._panes[0]._submit("hello")
        await pilot.pause()
        app._record_session_closed(h, reason="user")
        app._record_session_closed(h, reason="user")
        events = replay_events(tmp_path / ".aegis" / "state", h).events
        assert sum(isinstance(e, SessionClosed) for e in events) == 1


@pytest.mark.asyncio
async def test_no_close_marker_without_meta(tmp_path: Path, monkeypatch):
    """A tab that never got a meta header (no user message) writes no
    SessionClosed — nothing to show in Ctrl+H, nothing to mark."""
    monkeypatch.chdir(tmp_path)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        h = app._panes[0].handle
        app._record_session_closed(h, reason="user")
        events = replay_events(tmp_path / ".aegis" / "state", h).events
        assert not any(isinstance(e, SessionClosed) for e in events)


@pytest.mark.asyncio
async def test_queue_worker_spawn_writes_no_meta(tmp_path: Path, monkeypatch):
    """Queue workers spawn through _SessionManagerAdapter, which builds a
    pane with no first-message hook — so their logs carry no SessionMeta
    and stay out of Ctrl+H."""
    monkeypatch.chdir(tmp_path)
    from aegis.tui.app import _SessionManagerAdapter
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        adapter = _SessionManagerAdapter(app)
        worker = adapter.spawn("sonnet", handle="worker-test",
                               opening_prompt="do the task")
        await pilot.pause()
        replay = replay_events(tmp_path / ".aegis" / "state", worker.handle)
        assert not any(isinstance(e, SessionMeta) for e in replay.events)
