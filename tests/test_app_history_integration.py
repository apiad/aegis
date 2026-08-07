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
async def test_meta_written_at_spawn_and_again_with_the_preview(
        tmp_path: Path, monkeypatch):
    """Two headers per session on purpose. The spawn one attributes the log
    from the very first record (the lazy one landed behind whatever the
    harness streamed at startup, which is what hid every session from
    Ctrl+R); the first-turn one carries the preview."""
    monkeypatch.chdir(tmp_path)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        sd = tmp_path / ".aegis" / "state"
        sessions_dir = sd / "sessions"
        # At spawn: exactly one header, and it is the first record.
        log_files = list(sessions_dir.glob("*.jsonl"))
        assert len(log_files) == 1
        events = replay_events(sd, log_files[0].stem).events
        assert isinstance(events[0], SessionMeta)
        assert events[0].preview == ""

        app._active._submit("hello world")
        await pilot.pause()

        metas = [e for e in replay_events(sd, log_files[0].stem).events
                 if isinstance(e, SessionMeta)]
        assert len(metas) == 2
        assert metas[1].preview == "hello world"
        assert metas[1].provider == "claude-code"
        assert metas[1].origin == "tui"


@pytest.mark.asyncio
async def test_preview_header_written_only_once_per_session(tmp_path: Path,
                                                            monkeypatch):
    """A second user turn does not append another preview header."""
    monkeypatch.chdir(tmp_path)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        active = app._active
        # Record twice directly — the guard must fire the hook only once.
        active._record_first_user_message("first")
        active._record_first_user_message("second")
        await pilot.pause()
        sd = tmp_path / ".aegis" / "state"
        log_files = list((sd / "sessions").glob("*.jsonl"))
        metas = [e for e in replay_events(sd, log_files[0].stem).events
                 if isinstance(e, SessionMeta)]
        previews = [m.preview for m in metas if m.preview]
        assert previews == ["first"]


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["ctrl+r", "ctrl+h"])
async def test_history_key_opens_history_modal(tmp_path: Path, monkeypatch,
                                               key: str):
    monkeypatch.chdir(tmp_path)
    from aegis.tui.history import HistoryModal
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()
        assert isinstance(app.screen, HistoryModal)


def test_history_binding_is_not_an_ambiguous_control_key():
    """Ctrl+H/I/M/J arrive as \\x08/\\t/\\r/\\n on terminals without the kitty
    keyboard protocol, so the parser reports backspace/tab/enter and a binding
    on them never fires. The advertised (show=True) history key must not be
    one of those."""
    shown = [b for b in AegisApp.BINDINGS
             if b.action == "open_history" and b.show]
    assert shown, "history needs a visible binding"
    assert not {b.key for b in shown} & {"ctrl+h", "ctrl+i", "ctrl+m",
                                         "ctrl+j"}


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
    log_id = "20260729T120000000000Z-prior"
    append_meta(sd, log_id, SessionMeta(
        handle="prior", profile="sonnet", provider="claude-code",
        cwd=str(tmp_path), created_at=now, origin="tui", preview="prior work"))
    append_event(sd, log_id, SystemInit(session_id="upstream-1"))

    fake_driver = MagicMock()
    fake_driver.supports_resume = True
    fake_driver.resume = MagicMock(return_value=FakeSession())

    app = AegisApp({"sonnet": _agent()}, "sonnet", _factory, FakeMCP(),
                   drivers={"claude-code": fake_driver})
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+r")
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
        target = app._panes[0].log_id
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
        h = app._panes[0].log_id
        app._panes[0]._submit("hello")
        await pilot.pause()
        app._record_session_closed(h, reason="user")
        app._record_session_closed(h, reason="user")
        events = replay_events(tmp_path / ".aegis" / "state", h).events
        assert sum(isinstance(e, SessionClosed) for e in events) == 1


@pytest.mark.asyncio
async def test_unused_tab_is_marked_closed_but_not_listed(tmp_path: Path,
                                                          monkeypatch):
    """A tab spawned and closed without a word now has a header, so it does
    get a close marker — but it is not a conversation, and list_history
    drops it. Otherwise every boot's default tab would fill the listing."""
    monkeypatch.chdir(tmp_path)
    from aegis.state.history import list_history
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        h = app._panes[0].log_id
        app._record_session_closed(h, reason="user")
        sd = tmp_path / ".aegis" / "state"
        events = replay_events(sd, h).events
        assert sum(isinstance(e, SessionClosed) for e in events) == 1
        assert list_history(sd, live_handles=set()) == []


@pytest.mark.asyncio
async def test_no_close_marker_for_a_worker_log(tmp_path: Path, monkeypatch):
    """Queue workers get no header, so nothing marks or lists them."""
    monkeypatch.chdir(tmp_path)
    from aegis.events import AssistantText as _AT
    from aegis.state.session_log import append_event
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        sd = tmp_path / ".aegis" / "state"
        append_event(sd, "worker-x", _AT(text="did the task", usage=None))
        app._record_session_closed("worker-x", reason="user")
        events = replay_events(sd, "worker-x").events
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


@pytest.mark.asyncio
async def test_rename_keeps_the_log_and_relabels_it(tmp_path: Path,
                                                    monkeypatch):
    """The bug that made renamed sessions unresumable: the pane, inbox and
    tabbar took the new name while the transcript kept the old one, so
    workspace.json pointed at a file that did not exist and resume opened an
    empty pane. Now the log id is fixed and the new name is recorded *inside*
    the log."""
    monkeypatch.chdir(tmp_path)
    from aegis.state.history import list_history
    from aegis.tui.app import _pane_to_tab
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app._panes[0]
        log_id, born = pane.log_id, pane.handle
        pane._submit("work on aegis")
        await pilot.pause()

        res = await app.rename_handle(born, "lucid-river")
        await pilot.pause()
        assert res["ok"] is True

        sd = tmp_path / ".aegis" / "state"
        # Identity unmoved: same id, same file, nothing orphaned.
        assert pane.log_id == log_id
        assert pane.handle == "lucid-river"
        headers = [e for e in replay_events(sd, log_id).events
                   if isinstance(e, SessionMeta)]
        assert headers[0].handle == born          # born under the old name
        assert headers[-1].handle == "lucid-river"  # renamed in place

        # Ctrl+R lists it under the new name, keyed on the unchanged id.
        rows = list_history(sd, live_handles=set())
        assert [r.handle for r in rows] == ["lucid-river"]
        assert rows[0].log_id == log_id

        # And the tab roster agrees with the transcript on disk.
        tab = _pane_to_tab(pane, order=0)
        assert (tab.handle, tab.log_id) == ("lucid-river", log_id)


@pytest.mark.asyncio
async def test_history_resume_reads_the_transcript_off_the_event_loop(
        tmp_path: Path, monkeypatch):
    """Reopening a session from Ctrl+R reads and decodes its whole transcript.
    On a 24MB / 18k-line log that is ~500ms, and on the event loop it froze
    the whole UI — the same reason list_history was moved to a thread."""
    import threading
    from datetime import datetime, timezone
    from unittest.mock import MagicMock
    import aegis.tui.app as app_mod
    from aegis.events import SessionMeta, SystemInit
    from aegis.state.session_log import append_event, append_meta

    monkeypatch.chdir(tmp_path)
    sd = tmp_path / ".aegis" / "state"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_id = "20260729T120000000000Z-prior"
    append_meta(sd, log_id, SessionMeta(
        handle="prior", profile="sonnet", provider="claude-code",
        cwd=str(tmp_path), created_at=now, origin="tui", preview="prior work"))
    append_event(sd, log_id, SystemInit(session_id="upstream-1"))

    loop_thread = threading.current_thread()
    ran_on = []
    real = app_mod._safe_replay

    def spy(*a, **kw):
        ran_on.append(threading.current_thread())
        return real(*a, **kw)

    monkeypatch.setattr(app_mod, "_safe_replay", spy)

    fake_driver = MagicMock()
    fake_driver.supports_resume = True
    fake_driver.resume = MagicMock(return_value=FakeSession())

    app = AegisApp({"sonnet": _agent()}, "sonnet", _factory, FakeMCP(),
                   drivers={"claude-code": fake_driver})
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause()
        for ch in "prior":
            await pilot.press(ch)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

    assert ran_on, "replay never ran"
    assert loop_thread not in ran_on, (
        "transcript read blocked the event loop")


# --- session titles -------------------------------------------------
#
# The manager's precedence rule is unit-tested elsewhere. What only shows
# up here is persistence: a title must reach the log, and — the trap —
# must survive a rename, whose header re-derives every other field.

@pytest.mark.asyncio
async def test_set_title_appends_a_header_carrying_it(tmp_path: Path,
                                                      monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        handle = app._active.handle
        res = await app.set_title(handle, "fix the eviction race",
                                  source="human")
        assert res["ok"] is True
        await pilot.pause()
        sd = tmp_path / ".aegis" / "state"
        log_id = list((sd / "sessions").glob("*.jsonl"))[0].stem
        metas = [e for e in replay_events(sd, log_id).events
                 if isinstance(e, SessionMeta)]
        assert metas[-1].title == "fix the eviction race"
        assert metas[-1].title_source == "human"


@pytest.mark.asyncio
async def test_a_rename_does_not_blank_the_title(tmp_path: Path,
                                                 monkeypatch):
    """_record_rename re-derives every SessionMeta field. If it omits the
    title, the header it appends says title="" and the operator's title is
    silently lost on the next /rename."""
    monkeypatch.chdir(tmp_path)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        handle = app._active.handle
        await app.set_title(handle, "eviction race", source="human")
        await app.rename_handle(handle, "fix-eviction")
        await pilot.pause()

        sd = tmp_path / ".aegis" / "state"
        log_id = list((sd / "sessions").glob("*.jsonl"))[0].stem
        metas = [e for e in replay_events(sd, log_id).events
                 if isinstance(e, SessionMeta)]
        # The rename header itself carries the title forward...
        assert metas[-1].handle == "fix-eviction"
        assert metas[-1].title == "eviction race"
        # ...and the live session still has it.
        assert app._active._core.title == "eviction race"
        assert app._active._core.title_source == "human"


@pytest.mark.asyncio
async def test_an_agent_title_is_refused_against_a_human_one(tmp_path: Path,
                                                             monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        handle = app._active.handle
        await app.set_title(handle, "operator wrote this", source="human")
        res = await app.set_title(handle, "agent wrote this", source="agent")
        assert "error" in res
        assert "human" in res["error"]
        assert app._active._core.title == "operator wrote this"


@pytest.mark.asyncio
async def test_a_resumed_pane_recovers_its_title(tmp_path: Path,
                                                 monkeypatch):
    """A restart must not quietly hand a human title back to the agents.

    The tracker is per-process, so a resumed session rebuilds its state
    from the transcript. If the title is not among what it rebuilds, the
    live session comes back with title_source="" — and the operator's
    authority, which is the entire point of the precedence rule, is gone
    the first time aegis restarts.
    """
    monkeypatch.chdir(tmp_path)
    sd = tmp_path / ".aegis" / "state"

    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        handle = app._active.handle
        log_id = app._active.log_id
        await app.set_title(handle, "eviction race", source="human")
        await pilot.pause()

    # A fresh pane over the same transcript — what resume constructs.
    from aegis.tui.pane import ConversationPane
    from aegis.tui.app import _safe_replay
    replay = _safe_replay(sd, log_id)
    pane = ConversationPane(
        FakeSession(), _agent(), "sonnet", handle, app._palette,
        state_dir_path=sd, log_id=log_id, replay=replay)

    assert pane._core.title == "eviction race"
    assert pane._core.title_source == "human"


@pytest.mark.asyncio
async def test_the_status_bar_shows_the_title_end_to_end(tmp_path: Path,
                                                         monkeypatch):
    """The bar segment is only worth having if something pushes to it.

    Width is forced wide: run_test() lays out at 80 columns, where `fit`
    correctly drops both the title and the identity segment in favour of
    state and metrics. That degradation is the bar's documented rule —
    lose what never changes, keep what does — and a title never changes.
    """
    monkeypatch.chdir(tmp_path)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app._active
        pane._bar()._width_override = 200
        assert "eviction" not in pane._bar().render_plain()
        await app.set_title(pane.handle, "fix the eviction race",
                            source="human")
        await pilot.pause()
        assert "fix the eviction race" in pane._bar().render_plain()


# --- auto-titling (slice 3) ------------------------------------------

@pytest.mark.asyncio
async def test_the_first_turn_auto_titles_the_session(tmp_path: Path,
                                                      monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = {}

    async def _fake_title_for(*, opening, agent, agents, cwd,
                              previous=None):
        seen["opening"] = opening
        return "fix the eviction race"

    monkeypatch.setattr("aegis.titlegen.title_for", _fake_title_for)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app._active
        pane._submit("the cache evicts too early, take a look")
        await pilot.pause()
        await pilot.pause()
        assert seen["opening"] == "the cache evicts too early, take a look"
        assert pane._core.title == "fix the eviction race"
        assert pane._core.title_source == "auto"


@pytest.mark.asyncio
async def test_auto_titling_fires_only_on_the_first_turn(tmp_path: Path,
                                                         monkeypatch):
    """A label that churns is worse than a handle that doesn't.

    The generator returns "" on purpose: a successful first title would
    set title_source="auto", and *that* guard would block the second call
    regardless — masking whether the fire-once flag works at all. With no
    title ever set, only the flag can keep the count at one.
    """
    monkeypatch.chdir(tmp_path)
    calls = []

    async def _fake_title_for(*, opening, agent, agents, cwd,
                              previous=None):
        calls.append(opening)
        return ""

    monkeypatch.setattr("aegis.titlegen.title_for", _fake_title_for)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app._active
        pane._submit("first message")
        await pilot.pause()
        pane._submit("second message")
        await pilot.pause()
        await pilot.pause()
        assert calls == ["first message"]


@pytest.mark.asyncio
async def test_auto_titling_skips_a_session_the_operator_already_titled(
        tmp_path: Path, monkeypatch):
    """Auto cannot outrank human, so the call would be refused anyway —
    skip it rather than pay for a refusal."""
    monkeypatch.chdir(tmp_path)
    calls = []

    async def _fake_title_for(*, opening, agent, agents, cwd,
                              previous=None):
        calls.append(opening)
        return "generated"

    monkeypatch.setattr("aegis.titlegen.title_for", _fake_title_for)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app._active
        await app.set_title(pane.handle, "mine", source="human")
        pane._submit("hello")
        await pilot.pause()
        await pilot.pause()
        assert calls == []
        assert pane._core.title == "mine"


@pytest.mark.asyncio
async def test_a_failing_generation_leaves_the_session_untouched(
        tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def _boom(*, opening, agent, agents, cwd, previous=None):
        raise RuntimeError("the endpoint is having a bad day")

    monkeypatch.setattr("aegis.titlegen.title_for", _boom)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app._active
        pane._submit("hello")
        await pilot.pause()
        await pilot.pause()
        assert pane._core.title == ""
        assert pane._core.title_source == ""


@pytest.mark.asyncio
async def test_regenerate_reads_the_tail_and_knows_the_previous_title(
        tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = {}

    async def _fake_title_for(*, opening, agent, agents, cwd,
                              previous=None):
        seen["opening"] = opening
        seen["previous"] = previous
        return "now about the indexer"

    monkeypatch.setattr("aegis.titlegen.title_for", _fake_title_for)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app._active
        pane._submit("the cache evicts too early")
        await pilot.pause()
        await app.set_title(pane.handle, "eviction race", source="human")
        await pilot.pause()

        res = await app.regenerate_title(pane.handle)
        assert res["ok"] is True
        # It summarizes the transcript, not the opening message...
        assert "ok" in seen["opening"]      # the agent's reply is in there
        # ...and it is told what the title already is.
        assert seen["previous"] == "eviction race"
        # A regenerate deliberately hands the title back to auto.
        assert pane._core.title == "now about the indexer"
        assert pane._core.title_source == "auto"


@pytest.mark.asyncio
async def test_a_failed_regeneration_keeps_the_existing_title(
        tmp_path: Path, monkeypatch):
    """Generation runs before anything is overwritten, so a bad day at the
    endpoint must not cost the operator the title they typed."""
    monkeypatch.chdir(tmp_path)

    async def _nothing(*, opening, agent, agents, cwd, previous=None):
        return ""

    monkeypatch.setattr("aegis.titlegen.title_for", _nothing)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app._active
        pane._submit("hello")
        await pilot.pause()
        await app.set_title(pane.handle, "keep me", source="human")
        await pilot.pause()

        res = await app.regenerate_title(pane.handle)
        assert "error" in res
        assert pane._core.title == "keep me"
        assert pane._core.title_source == "human"
