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
    """Captures callbacks; never touches harp. Drives updates on demand."""
    last = None

    def __init__(self, cfg, on_update, on_final, **_):
        self.cfg = cfg
        self.on_update = on_update
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
        self.on_final("done text")


@pytest.mark.asyncio
async def test_toggle_starts_and_streams_into_focused_input(monkeypatch):
    monkeypatch.setattr("aegis.tui.app.voice_available", lambda: True)
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _StubVoice
    async with app.run_test() as pilot:
        pane = app._active
        assert isinstance(pane, ConversationPane)
        pane.input_widget().value = "prefix "
        await app.action_toggle_voice()
        assert app._voice is not None and app._voice.is_running
        assert pane.has_class("recording")
        # simulate a transcript update emitted from the worker thread
        _StubVoice.last.on_update("hello world", "")
        await pilot.pause()
        assert pane.input_widget().value == "prefix hello world"


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
