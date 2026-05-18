import pytest
from aegis.config import Agent
from aegis.events import AssistantText, Result
from aegis.tui.app import AegisApp
from aegis.tui.state import AgentState
from aegis.tui.widgets import TabStrip, StatusBar
from rich.text import Text
from textual.widgets import RichLog, Input


class FakeSession:
    def __init__(self):
        self.sent = []
        self.started = self.closed = False

    async def start(self):
        self.started = True

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        yield AssistantText(f"echo: {self.sent[-1]}")
        yield Result(duration_ms=10, is_error=False)

    async def close(self):
        self.closed = True


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


@pytest.mark.asyncio
async def test_app_mounts_with_all_widgets():
    app = AegisApp(FakeSession(), _agent(), "default")
    async with app.run_test() as pilot:
        assert app.query_one(TabStrip)
        assert app.query_one(StatusBar)
        assert app.query_one(RichLog)
        assert app.query_one(Input)
        assert app.state is AgentState.ready


@pytest.mark.asyncio
async def test_submitting_input_sends_and_renders_and_pings():
    sess = FakeSession()
    app = AegisApp(sess, _agent(), "default")
    bells = []
    async with app.run_test() as pilot:
        app.bell = lambda: bells.append(1)
        inp = app.query_one(Input)
        inp.value = "hello"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert sess.sent == ["hello"]
        assert app._transcript_has("echo: hello")
        assert app.state is AgentState.ready
        assert bells == [1]


@pytest.mark.asyncio
async def test_quit_binding_exits():
    app = AegisApp(FakeSession(), _agent(), "default")
    async with app.run_test() as pilot:
        await pilot.press("ctrl+q")
    assert app.is_running is False
