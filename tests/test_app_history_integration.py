"""Integration tests for history wiring through AegisApp.

The rig stubs the make_session factory so we exercise the persistence
pipeline without spawning real harness subprocesses.
"""
from pathlib import Path

import pytest

from aegis.config import Agent
from aegis.events import AssistantText, Result, SessionMeta
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
