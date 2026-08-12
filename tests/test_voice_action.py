import pytest

from aegis.config import Agent, VoiceConfig
from aegis.events import AssistantText, Result
from aegis.tui.app import AegisApp
from aegis.tui.pane import ConversationPane


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    def __init__(self):
        self.sent = []
        self.started = self.closed = False

    async def start(self): self.started = True
    async def send(self, text): self.sent.append(text)
    async def events(self):
        yield AssistantText("ok")
        yield Result(duration_ms=1, is_error=False)
    async def close(self): self.closed = True


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): self.bound = bridge
    async def start(self): pass
    async def stop(self): pass


def _factory(*sessions):
    it = iter(sessions or (FakeSession(),))
    def make(agent, mcp_url, handle):
        try:
            return next(it)
        except StopIteration:
            return FakeSession()
    return make


def _app(*, voice):
    return AegisApp({"default": _agent()}, "default", _factory(), FakeMCP(),
                    voice=voice)


class _StubVoice:
    """Full-mode stub: never touches harp. Delivers the final text on stop."""
    last = None

    def __init__(self, cfg, on_final, **_):
        self.cfg = cfg
        self.on_final = on_final
        self._running = False
        _StubVoice.last = self

    @property
    def is_running(self):
        return self._running

    def start(self):
        self._running = True

    def stop(self):
        self._running = False
        self.on_final("hello world")   # deliver synchronously for the test


@pytest.mark.asyncio
async def test_stop_inserts_final_text_once(monkeypatch):
    monkeypatch.setattr("aegis.tui.app.voice_available", lambda: True)
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _StubVoice
    async with app.run_test() as pilot:
        pane = app._active
        assert isinstance(pane, ConversationPane)
        pane.input_widget().value = "prefix "
        await app.action_toggle_voice()      # start
        assert app._voice is not None and app._voice.is_running
        assert pane.has_class("recording")
        await app.action_toggle_voice()      # stop -> on_final("hello world")
        await pilot.pause()
        assert pane.input_widget().value == "prefix hello world"
        assert not pane.has_class("recording")


@pytest.mark.asyncio
async def test_second_toggle_stops_and_clears(monkeypatch):
    monkeypatch.setattr("aegis.tui.app.voice_available", lambda: True)
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _StubVoice
    async with app.run_test() as pilot:
        pane = app._active
        await app.action_toggle_voice()   # start
        await app.action_toggle_voice()   # stop
        await pilot.pause()
        assert app._voice is None
        assert not pane.has_class("recording")


@pytest.mark.asyncio
async def test_unavailable_deps_shows_hint_no_session(monkeypatch):
    import aegis.tui.app as appmod
    monkeypatch.setattr(appmod, "voice_available", lambda: False)
    monkeypatch.setattr(appmod, "unavailable_reason", lambda: "install hint")
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _StubVoice
    async with app.run_test():
        await app.action_toggle_voice()
        assert app._voice is None


class _DeferredStubVoice:
    """Like _StubVoice, but the transcript lands only when the test says so
    — which is the only way the transcribing window is observable."""
    last = None

    def __init__(self, cfg, on_final, **_):
        self.cfg = cfg
        self.on_final = on_final
        self._running = False
        _DeferredStubVoice.last = self

    @property
    def is_running(self):
        return self._running

    def start(self):
        self._running = True

    def stop(self):
        self._running = False      # decode "in flight"; no on_final yet

    def deliver(self, text):
        self.on_final(text)


async def _start_recording(app):
    await app.action_toggle_voice()
    return app._active


@pytest.mark.asyncio
async def test_input_locked_while_recording(monkeypatch):
    monkeypatch.setattr("aegis.tui.app.voice_available", lambda: True)
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _DeferredStubVoice
    async with app.run_test():
        pane = await _start_recording(app)
        assert pane.input_widget().locked is True


@pytest.mark.asyncio
async def test_input_stays_locked_while_transcribing(monkeypatch):
    """The overwrite window is record AND decode, so the lock spans both."""
    monkeypatch.setattr("aegis.tui.app.voice_available", lambda: True)
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _DeferredStubVoice
    async with app.run_test() as pilot:
        pane = await _start_recording(app)
        await app.action_toggle_voice()          # stop -> transcribing
        await pilot.pause()
        assert pane.input_widget().locked is True
        assert pane.has_class("transcribing")
        _DeferredStubVoice.last.deliver("spoken")
        await pilot.pause()
        assert pane.input_widget().locked is False
        assert not pane.has_class("transcribing")


@pytest.mark.asyncio
async def test_text_typed_before_recording_survives(monkeypatch):
    """The regression: the transcript appends to what is there now, not to
    a value captured when recording started."""
    monkeypatch.setattr("aegis.tui.app.voice_available", lambda: True)
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _DeferredStubVoice
    async with app.run_test() as pilot:
        pane = app._active
        pane.input_widget().value = "prefix "
        await app.action_toggle_voice()
        await app.action_toggle_voice()
        _DeferredStubVoice.last.deliver("hello world")
        await pilot.pause()
        assert pane.input_widget().value == "prefix hello world"


@pytest.mark.asyncio
async def test_empty_transcript_still_unlocks(monkeypatch):
    """A recording with no speech returns early — and would strand the
    input locked and the strip spinning forever."""
    monkeypatch.setattr("aegis.tui.app.voice_available", lambda: True)
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _DeferredStubVoice
    async with app.run_test() as pilot:
        pane = await _start_recording(app)
        await app.action_toggle_voice()
        _DeferredStubVoice.last.deliver("")
        await pilot.pause()
        assert pane.input_widget().locked is False
        assert not pane.has_class("recording")
        assert not pane.has_class("transcribing")


@pytest.mark.asyncio
async def test_second_press_while_transcribing_is_ignored(monkeypatch):
    """_stop_voice clears _voice before the decode runs, so without a
    guard a second press starts a NEW recording whose state the in-flight
    decode then clears."""
    monkeypatch.setattr("aegis.tui.app.voice_available", lambda: True)
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _DeferredStubVoice
    async with app.run_test() as pilot:
        pane = await _start_recording(app)
        first = _DeferredStubVoice.last
        await app.action_toggle_voice()          # -> transcribing
        await app.action_toggle_voice()          # must be ignored
        await pilot.pause()
        assert _DeferredStubVoice.last is first, "no new recording started"
        assert app._voice is None
        assert pane.has_class("transcribing")
        first.deliver("done")
        await pilot.pause()
        assert pane.input_widget().locked is False


@pytest.mark.asyncio
async def test_strip_shows_the_configured_key(monkeypatch):
    monkeypatch.setattr("aegis.tui.app.voice_available", lambda: True)
    app = _app(voice=VoiceConfig(enabled=True, key="ctrl+shift+v"))
    app._voice_session_factory = _DeferredStubVoice
    async with app.run_test() as pilot:
        from aegis.tui.voice_strip import VoiceStrip
        pane = await _start_recording(app)
        await pilot.pause()
        assert "ctrl+shift+v" in str(pane.query_one(VoiceStrip).render())


@pytest.mark.asyncio
async def test_transcript_appends_to_the_value_at_delivery_time(monkeypatch):
    """The actual defect, pinned at the seam rather than through the UI.

    The old code captured the input's contents when recording STARTED and
    reassigned base + transcript on delivery, so anything that changed in
    between was discarded. The lock now makes that unreachable by typing,
    which is exactly why this test writes the value directly: it proves
    _apply_voice_text reads the CURRENT value, so a future hole in the lock
    degrades to 'both survive' rather than 'your text vanishes'.
    """
    monkeypatch.setattr("aegis.tui.app.voice_available", lambda: True)
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _DeferredStubVoice
    async with app.run_test() as pilot:
        pane = app._active
        pane.input_widget().value = "at start "
        await app.action_toggle_voice()
        # Something changes the buffer after the capture point would have
        # been taken. Bypasses the lock on purpose — the lock is the UX,
        # this is the correctness underneath it.
        pane.input_widget().text = "changed mid-flight "
        await app.action_toggle_voice()
        _DeferredStubVoice.last.deliver("spoken words")
        await pilot.pause()
        value = pane.input_widget().value
        assert value == "changed mid-flight spoken words", value
        assert "at start" not in value, "a stale capture was used"
