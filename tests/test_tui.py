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


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def __init__(self):
        self.started = False
        self.stopped = False
        self.bound = None

    def bind(self, bridge):
        self.bound = bridge

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


def _factory(*sessions):
    it = iter(sessions or (FakeSession(),))
    made = []
    def make(agent, mcp_url):
        try:
            s = next(it)
        except StopIteration:
            s = FakeSession()
        made.append(s)
        return s
    make.made = made
    return make


def _app(factory=None, mcp=None):
    f = factory or _factory()
    return AegisApp({"default": _agent()}, "default", f, mcp or FakeMCP())


@pytest.mark.asyncio
async def test_app_starts_and_stops_mcp():
    m = FakeMCP()
    app = AegisApp({"default": _agent()}, "default", _factory(), m)
    async with app.run_test() as pilot:
        assert m.started is True
        await pilot.press("ctrl+q")
    assert m.stopped is True


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
    app = AegisApp({"default": _agent()}, "default", f, FakeMCP())
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
                   _factory(*[FakeSession() for _ in range(8)]),
                   FakeMCP())
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
    app = AegisApp({"default": _agent()}, "default", f, FakeMCP())
    async with app.run_test() as pilot:
        await pilot.press("ctrl+w")
    assert app.is_running is False
    # never messaged → subprocess was never spawned, nothing to close
    assert f.made[0].started is False
    assert f.made[0].closed is False


@pytest.mark.asyncio
async def test_lazy_start_session_only_on_first_message():
    f = _factory()
    app = AegisApp({"default": _agent()}, "default", f, FakeMCP())
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
    app = AegisApp(agents, "default", _factory(FakeSession(), FakeSession()),
                   FakeMCP())
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
    app = AegisApp(agents, "default", _factory(FakeSession()), FakeMCP())
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


@pytest.mark.asyncio
async def test_pane_holds_palette_and_renders():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        assert pane._palette is app.palette
        pane.query_one(Input).value = "hi"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert pane._transcript_has("echo: hi")


@pytest.mark.asyncio
async def test_blank_line_between_turns():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.query_one(Input).value = "first"
        await pilot.press("enter")
        await pilot.pause(); await pilot.pause()
        pane.query_one(Input).value = "second"
        await pilot.press("enter")
        await pilot.pause(); await pilot.pause()
        lines = [l.text if hasattr(l, "text") else str(l)
                 for l in pane.query_one(RichLog).lines]
        joined = "\n".join(lines)
        assert "\n\n› second" in joined
        assert not joined.startswith("\n")


@pytest.mark.asyncio
async def test_ink_layout_has_breathing_padding():
    # Regression: the Ink look requires real padding (spec §Wiring). It
    # shipped cramped because ConversationPane.DEFAULT_CSS had none.
    app = _app()
    async with app.run_test():
        pane = app._panes[0]
        log_pad = pane.query_one(RichLog).styles.padding
        inp_pad = pane.query_one(Input).styles.padding
        assert log_pad.top >= 1 and log_pad.right >= 2, log_pad
        assert inp_pad.right >= 1, inp_pad


@pytest.mark.asyncio
async def test_input_border_stable_across_focus_blur():
    # Regression: the input "lifted" because its border differed between
    # focus and blur (Textual Input:focus re-adds a `tall` border at higher
    # specificity). The invariant that prevents the lift is SYMMETRY — the
    # border must be identical focused vs blurred (it may be present; it
    # must not change). It now carries a top+bottom rule (Claude-Code-like).
    app = _app()
    async with app.run_test() as pilot:
        inp = app._panes[0].query_one(Input)
        app.set_focus(inp)
        await pilot.pause()
        f_top, f_bot = inp.styles.border.top, inp.styles.border.bottom
        app.set_focus(None)
        await pilot.pause()
        b_top, b_bot = inp.styles.border.top, inp.styles.border.bottom
        assert (f_top, f_bot) == (b_top, b_bot), (
            f"focus={(f_top, f_bot)} blur={(b_top, b_bot)}")
        # the requested rule above/below the input is present
        assert f_top[0] and f_top[0] not in ("", "none"), f_top
        assert f_bot[0] and f_bot[0] not in ("", "none"), f_bot
        # and there is air between the status line and the input
        assert inp.styles.margin.top >= 1, inp.styles.margin


@pytest.mark.asyncio
async def test_blank_row_between_user_and_agent():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.query_one(Input).value = "ping"
        await pilot.press("enter")
        await pilot.pause(); await pilot.pause()
        lines = [l.text if hasattr(l, "text") else str(l)
                 for l in pane.query_one(RichLog).lines]
        # find the user line and the agent echo; a blank row must separate
        ui = next(i for i, t in enumerate(lines) if t.startswith("› ping"))
        ai = next(i for i, t in enumerate(lines) if "echo: ping" in t)
        assert ai > ui
        assert any(lines[j].strip() == "" for j in range(ui + 1, ai))


@pytest.mark.asyncio
async def test_blank_rows_between_agent_steps():
    from aegis.events import AssistantThinking, ToolUse, ToolResult
    script = lambda t: [
        AssistantThinking("mm"), ToolUse(name="Read", summary="f.py"),
        ToolResult(text="ok", is_error=False), AssistantText("done"),
        Result(duration_ms=1, is_error=False),
    ]
    app = _app(_factory(FakeSession(script)))
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.query_one(Input).value = "go"
        await pilot.press("enter")
        await pilot.pause(); await pilot.pause()
        lines = [l.text if hasattr(l, "text") else str(l)
                 for l in pane.query_one(RichLog).lines]
        think_i = next(i for i, t in enumerate(lines) if "Thinking" in t)
        read_i = next(i for i, t in enumerate(lines) if "Read" in t)
        # at least one blank row separates the thinking step from the tool
        assert any(lines[j].strip() == "" for j in range(think_i + 1, read_i))


@pytest.mark.asyncio
async def test_appbridge_list_and_handoff():
    app = _app(_factory(FakeSession(), FakeSession()))
    async with app.run_test() as pilot:
        await pilot.press("ctrl+t")          # 2 panes
        await pilot.pause()
        sessions = app.list_sessions()
        assert {s.handle for s in sessions} == {
            p.handle for p in app._panes}
        assert any(s.active for s in sessions)
        a, b = app._panes[0].handle, app._panes[1].handle
        # self / unknown rejects
        assert "yourself" in await app.handoff(a, a, "x")
        assert "no session" in await app.handoff(a, "ghost", "x")
        # deliver into b
        msg = await app.handoff(a, b, "please continue the spec")
        assert msg == f"delivered to {b}"
        await pilot.pause(); await pilot.pause()
        assert app._panes[1]._transcript_has(f"[handoff from {a}]")
        assert app._panes[1]._transcript_has("please continue the spec")


@pytest.mark.asyncio
async def test_handoff_rejects_busy_target():
    import asyncio

    class Hang:
        def __init__(self):
            self.sent = []
            self.started = self.closed = False
        async def start(self): self.started = True
        async def send(self, t): self.sent.append(t)
        async def events(self):
            await asyncio.Event().wait()
            yield None  # pragma: no cover
        async def close(self): self.closed = True

    app = _app(_factory(FakeSession(), Hang()))
    async with app.run_test() as pilot:
        await pilot.press("ctrl+t"); await pilot.pause()
        b = app._panes[1]
        b.query_one(Input).value = "go"
        await b.query_one(Input).action_submit()
        await pilot.pause()
        assert b.state is AgentState.working
        a = app._panes[0].handle
        assert "busy" in await app.handoff(a, b.handle, "x")


@pytest.mark.asyncio
async def test_step_spacing_glues_tool_pair_and_single_gap_after_done():
    from aegis.events import AssistantThinking, ToolUse, ToolResult
    script = lambda t: [
        AssistantThinking("mm"), ToolUse(name="Read", summary="f.py"),
        ToolResult(text="ok", is_error=False), AssistantText("answer"),
        Result(duration_ms=1, is_error=False),
    ]
    app = _app(_factory(FakeSession(script), FakeSession()))
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.query_one(Input).value = "first"
        await pilot.press("enter")
        await pilot.pause(); await pilot.pause()
        pane.query_one(Input).value = "second"
        await pilot.press("enter")
        await pilot.pause(); await pilot.pause()
        lines = [l.text if hasattr(l, "text") else str(l)
                 for l in pane.query_one(RichLog).lines]
        tool_i = next(i for i, t in enumerate(lines) if "Read" in t)
        res_i = next(i for i, t in enumerate(lines) if t.strip().startswith("└"))
        done_i = next(i for i, t in enumerate(lines) if "done in" in t)
        u2_i = next(i for i, t in enumerate(lines) if t.startswith("› second"))
        # 1: tool_use is immediately followed by its result (no blank between)
        assert res_i == tool_i + 1, lines[tool_i:res_i + 1]
        # 2: a blank row follows the tool result
        assert lines[res_i + 1].strip() == ""
        # 3: exactly ONE blank between ── done ── and the next user line
        gap = [lines[j].strip() for j in range(done_i + 1, u2_i)]
        assert gap == [""], gap
