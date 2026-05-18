import pytest
from aegis.config import Agent
from aegis.events import AssistantText, Result
from aegis.tui.app import AegisApp
from aegis.tui.pane import ConversationPane
from aegis.tui.state import AgentState
from aegis.tui.widgets import StatusBar, TabBar
from textual.widgets import Input, RichLog


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    def __init__(self, script=None):
        self.sent = []
        self.started = self.closed = False
        self._script = script or (
            lambda t: [AssistantText(f"echo: {t}"),
                       Result(duration_ms=10, is_error=False)])

    async def start(self): self.started = True
    async def send(self, text): self.sent.append(text)
    async def events(self):
        for ev in self._script(self.sent[-1]):
            yield ev
    async def close(self): self.closed = True


def _factory(*sessions):
    it = iter(sessions or (FakeSession(),))
    made = []
    def make(agent):
        try:
            s = next(it)
        except StopIteration:
            s = FakeSession()
        made.append(s)
        return s
    make.made = made
    return make


def _app(factory=None):
    f = factory or _factory()
    return AegisApp({"default": _agent()}, "default", f)


@pytest.mark.asyncio
async def test_starts_with_one_pane_and_widgets():
    app = _app()
    async with app.run_test():
        assert len(app._panes) == 1
        pane = app._panes[0]
        assert isinstance(pane, ConversationPane)
        assert pane.query_one(RichLog) and pane.query_one(StatusBar)
        assert pane.query_one(Input)
        assert app.query_one(TabBar)
        assert pane.state is AgentState.ready


@pytest.mark.asyncio
async def test_submit_sends_renders_and_bells():
    app = _app()
    bells = []
    async with app.run_test() as pilot:
        app.bell = lambda: bells.append(1)
        pane = app._panes[0]
        pane.query_one(Input).value = "hello"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert pane._session.sent == ["hello"]
        assert pane._transcript_has("echo: hello")
        assert pane.state is AgentState.ready
        assert bells == [1]


@pytest.mark.asyncio
async def test_quit_closes_session_and_exits():
    f = _factory()
    app = AegisApp({"default": _agent()}, "default", f)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+q")
    assert app.is_running is False
    assert f.made[0].closed is True


@pytest.mark.asyncio
async def test_interrupt_only_active_pane():
    import asyncio

    class HangSession:
        def __init__(self):
            self.sent = []
            self.started = self.closed = False
            self._gate = asyncio.Event()

        async def start(self): self.started = True
        async def send(self, text): self.sent.append(text)
        async def events(self):
            await self._gate.wait()  # blocks until cancelled
            yield AssistantText("never")  # pragma: no cover
        async def close(self): self.closed = True

    sess = HangSession()
    app = _app(_factory(sess))
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.query_one(Input).value = "go"
        await pilot.press("enter")
        await pilot.pause()
        assert pane.state is AgentState.working
        await pilot.press("escape")
        await pilot.pause()
        assert pane.state is AgentState.ready
        assert pane._transcript_has("interrupted")


@pytest.mark.asyncio
async def test_tabbar_shows_handle_slug_dot():
    app = _app()
    async with app.run_test():
        bar = str(app.query_one(TabBar).content)
        pane = app._panes[0]
        assert pane.handle in bar
        assert "default" in bar          # slug
        assert "1" in bar                # index
        assert "*" not in bar            # active pane, not unseen
