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
async def test_quit_closes_started_session_and_exits():
    f = _factory()
    app = AegisApp({"default": _agent()}, "default", f)
    async with app.run_test() as pilot:
        app._panes[0].query_one(Input).value = "hi"
        await pilot.press("enter")     # starts the session
        await pilot.pause()
        await pilot.press("ctrl+q")
    assert app.is_running is False
    assert f.made[0].started is True
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
        bar = app.query_one(TabBar).bar_text()
        pane = app._panes[0]
        assert pane.handle in bar
        assert "default" in bar          # slug
        assert "1" in bar                # index
        assert "*" not in bar            # active pane, not unseen


@pytest.mark.asyncio
async def test_tabbar_scrolls_active_into_view():
    app = AegisApp({"default": _agent()}, "default",
                   _factory(*[FakeSession() for _ in range(8)]))
    async with app.run_test(size=(40, 10)) as pilot:
        bar = app.query_one(TabBar)
        for _ in range(7):               # 8 tabs total, overflow 40 cols
            await pilot.press("ctrl+t")
            await pilot.pause()
        assert app._active is app._panes[7]
        await pilot.pause()
        assert bar.scroll_x > 0          # scrolled right to show last tab
        await pilot.press("ctrl+1")      # back to first
        await pilot.pause()
        await pilot.pause()
        assert bar.scroll_x == 0         # scrolled back to start


# --- Task 4: multi-tab tests ---

@pytest.mark.asyncio
async def test_ctrl_t_adds_unique_tab():
    app = _app(_factory(FakeSession(), FakeSession()))
    async with app.run_test() as pilot:
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert len(app._panes) == 2
        assert app._panes[0].handle != app._panes[1].handle
        assert app._active is app._panes[1]


@pytest.mark.asyncio
async def test_background_finish_sets_unseen_and_one_bell():
    slow = FakeSession(lambda t: [AssistantText("bg done"),
                                  Result(duration_ms=1, is_error=False)])
    app = _app(_factory(slow, FakeSession()))
    bells = []
    async with app.run_test() as pilot:
        app.bell = lambda: bells.append(1)
        await pilot.press("ctrl+t")          # tab 1 active
        await pilot.pause()
        p0 = app._panes[0]
        p0.query_one(Input).value = "hi"
        await p0.query_one(Input).action_submit()
        await pilot.pause()
        await pilot.pause()
        assert p0.unseen is True
        assert bells == [1]
        await pilot.press("ctrl+1")
        await pilot.pause()
        assert p0.unseen is False


@pytest.mark.asyncio
async def test_ctrl_w_closes_last_tab_exits():
    f = _factory()
    app = AegisApp({"default": _agent()}, "default", f)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+w")
    assert app.is_running is False
    # never messaged → subprocess was never spawned, nothing to close
    assert f.made[0].started is False
    assert f.made[0].closed is False


@pytest.mark.asyncio
async def test_lazy_start_session_only_on_first_message():
    f = _factory()
    app = AegisApp({"default": _agent()}, "default", f)
    async with app.run_test() as pilot:
        sess = f.made[0]
        assert sess.started is False          # not started at mount
        from aegis.tui.widgets import StatusBar
        sb = str(app._panes[0].query_one(StatusBar).content)
        assert "0s / 0s" in sb                # session clock unanchored
        app._panes[0].query_one(Input).value = "one"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert sess.started is True           # started on first message
        app._panes[0].query_one(Input).value = "two"
        await pilot.press("enter")
        await pilot.pause()
        assert sess.sent == ["one", "two"]    # not re-started; still one session


@pytest.mark.asyncio
async def test_switch_keys_cycle():
    app = _app(_factory(FakeSession(), FakeSession(), FakeSession()))
    async with app.run_test() as pilot:
        await pilot.press("ctrl+t")
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert app._active is app._panes[2]
        await pilot.press("ctrl+1")
        await pilot.pause()
        assert app._active is app._panes[0]
        await pilot.press("ctrl+right")
        await pilot.pause()
        assert app._active is app._panes[1]


@pytest.mark.asyncio
async def test_metrics_tick_refreshes_active_pane():
    app = _app()
    async with app.run_test():
        pane = app._panes[0]
        from aegis.tui.widgets import StatusBar
        sb = pane.query_one(StatusBar)
        # blank the rendered content, then tick must repaint the metrics
        sb.update("")
        assert "↑" not in str(sb.content)
        app._tick()
        after = str(sb.content)
        assert "↑" in after and "/" in after   # SessionMetrics.render ran
        assert "·default·" in after            # identity still present


# --- Task 5: AgentPicker modal tests ---

@pytest.mark.asyncio
async def test_ctrl_n_picker_spawns_chosen_profile():
    agents = {"default": _agent(),
              "fast": Agent(harness="claude-code", model="sonnet",
                            effort="low", permission="read")}
    app = AegisApp(agents, "default", _factory(FakeSession(), FakeSession()))
    async with app.run_test() as pilot:
        await pilot.press("ctrl+n")
        await pilot.pause()
        await pilot.press("down")     # move to "fast" (sorted: default, fast)
        await pilot.press("enter")
        await pilot.pause()
        assert len(app._panes) == 2
        assert app._panes[1].agent_slug == "fast"


@pytest.mark.asyncio
async def test_ctrl_n_picker_cancel_no_new_pane():
    agents = {"default": _agent(),
              "fast": Agent(harness="claude-code", model="sonnet",
                            effort="low", permission="read")}
    app = AegisApp(agents, "default", _factory(FakeSession()))
    async with app.run_test() as pilot:
        await pilot.press("ctrl+n")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert len(app._panes) == 1


@pytest.mark.asyncio
async def test_error_then_resend_recovers_to_ready():
    calls = {"n": 0}
    def script(_t):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first turn blows up")
        return [AssistantText("recovered"),
                Result(duration_ms=1, is_error=False)]
    app = _app(_factory(FakeSession(script)))
    async with app.run_test() as pilot:
        pane = app._panes[0]
        from textual.widgets import Input
        pane.query_one(Input).value = "first"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert pane.state is AgentState.error
        pane.query_one(Input).value = "second"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert pane._transcript_has("recovered")
        assert pane.state is AgentState.ready


@pytest.mark.asyncio
async def test_app_boots_on_ink_theme():
    from aegis.tui.themes import AegisColors
    app = _app()
    async with app.run_test():
        assert app.theme == "aegis-ink"
        assert isinstance(app.palette, AegisColors)
        assert app.palette.accent == "#E0A872"


@pytest.mark.asyncio
async def test_tabbar_uses_theme_accent_for_slug():
    app = _app()
    async with app.run_test():
        bar = app.query_one(TabBar).bar_text()
        assert app.palette.accent in bar
        assert app._panes[0].handle in bar
