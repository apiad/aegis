import pytest
from aegis.config import Agent
from aegis.events import AssistantText, Result, ToolResult, ToolUse
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


class ResultThenErrorSession:
    def __init__(self):
        self.sent = []
        self.started = self.closed = False

    async def start(self):
        self.started = True

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        from aegis.events import Result
        yield Result(duration_ms=5, is_error=False)
        raise RuntimeError("harness blew up after result")

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_bell_fires_once_when_result_then_error():
    app = AegisApp(ResultThenErrorSession(), _agent(), "default")
    bells = []
    async with app.run_test() as pilot:
        app.bell = lambda: bells.append(1)
        inp = app.query_one(Input)
        inp.value = "hello"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert bells == [1]  # exactly one bell despite the post-Result exception


class ToolTurnSession:
    def __init__(self):
        self.sent = []
        self.started = self.closed = False

    async def start(self):
        self.started = True

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        yield ToolUse(name="Bash", summary="echo hi")
        yield ToolResult(text="boom", is_error=True)
        yield Result(duration_ms=10, is_error=False,
                     input_tokens=1200, output_tokens=340)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_status_metrics_render_and_tick():
    from aegis.tui.metrics import SessionMetrics
    app = AegisApp(ToolTurnSession(), _agent(), "default")
    async with app.run_test() as pilot:
        clock = [100.0]
        app._now = lambda: clock[0]
        app._metrics = SessionMetrics(clock[0])
        inp = app.query_one(Input)
        inp.value = "go"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        sb_text = str(app.query_one(StatusBar).content)
        assert "↑1.2k" in sb_text
        assert "↓340" in sb_text
        assert "⚒ 1 (1 err)" in sb_text
        clock[0] = 225.0
        app._tick()
        sb_text2 = str(app.query_one(StatusBar).content)
        assert "2m05s" in sb_text2


class ErrorThenOkSession:
    """First turn errors (no Result), second turn succeeds."""

    def __init__(self):
        self.sent = []
        self.started = self.closed = False

    async def start(self):
        self.started = True

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        if len(self.sent) == 1:
            raise RuntimeError("first turn blows up")
        yield AssistantText("recovered")
        yield Result(duration_ms=1, is_error=False)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_error_then_resend_recovers_to_ready():
    app = AegisApp(ErrorThenOkSession(), _agent(), "default")
    async with app.run_test() as pilot:
        inp = app.query_one(Input)
        inp.value = "first"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert app.state is AgentState.error
        inp = app.query_one(Input)
        inp.value = "second"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert app._transcript_has("recovered")
        assert app.state is AgentState.ready
