import pytest
from aegis.config import Agent
from aegis.events import AssistantText, Result
from aegis.tui.app import AegisApp
from aegis.tui.pane import ConversationPane
from aegis.tui.state import AgentState
from aegis.tui.widgets import StatusBar, TabBar
from textual.containers import VerticalScroll
from textual.widgets import Input
from aegis.tui.pane import CopyableBlock


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
    def make(agent, mcp_url, handle):
        try:
            s = next(it)
        except StopIteration:
            s = FakeSession()
        made.append(s)
        return s
    make.made = made
    return make


def _app(factory=None, mcp=None, *, queues=None, agents=None):
    f = factory or _factory()
    a = agents or {"default": _agent()}
    return AegisApp(a, "default", f, mcp or FakeMCP(), queues=queues)


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
        assert pane.query_one(VerticalScroll) and pane.query_one(StatusBar)
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
async def test_user_lines_are_separate_blocks():
    """Each user submission becomes its own CopyableBlock; visual
    separation is provided by CSS margin-bottom, no manual blank rows."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.query_one(Input).value = "first"
        await pilot.press("enter")
        await pilot.pause(); await pilot.pause()
        pane.query_one(Input).value = "second"
        await pilot.press("enter")
        await pilot.pause(); await pilot.pause()
        payloads = [b.text_payload() for b in pane.query(CopyableBlock)]
        # Both user inputs land as full-text payloads, in order.
        i1 = payloads.index("first")
        i2 = payloads.index("second")
        assert i2 > i1


@pytest.mark.asyncio
async def test_ink_layout_has_breathing_padding():
    # Regression: the Ink look requires real padding (spec §Wiring). It
    # shipped cramped because ConversationPane.DEFAULT_CSS had none.
    app = _app()
    async with app.run_test():
        pane = app._panes[0]
        log_pad = pane.query_one("#transcript", VerticalScroll).styles.padding
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
async def test_user_block_precedes_agent_block():
    """User input + agent reply land in two separate CopyableBlocks
    in order (no shared block, no agent-first ordering)."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.query_one(Input).value = "ping"
        await pilot.press("enter")
        await pilot.pause(); await pilot.pause()
        payloads = [b.text_payload() for b in pane.query(CopyableBlock)]
        ui = payloads.index("ping")
        ai = next(i for i, p in enumerate(payloads) if "echo: ping" in p)
        assert ai > ui


@pytest.mark.asyncio
async def test_agent_steps_become_distinct_blocks():
    """Thinking, tool call, tool result, and final answer are each
    their own CopyableBlock — natural visual separation via CSS
    margin, copyability per step."""
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
        payloads = [b.text_payload() for b in pane.query(CopyableBlock)]
        # User input first, then ordered agent steps as separate blocks.
        assert payloads[0] == "go"
        # Thinking block carries the accumulated thinking text payload.
        think_i = next(i for i, p in enumerate(payloads) if p == "mm")
        tool_i = next(i for i, p in enumerate(payloads) if p == "Read(f.py)")
        assert tool_i > think_i


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
async def test_block_ordering_across_two_turns():
    """Tool call is immediately followed by its result, and the
    sequence (tool → result → done → next-user) preserves order
    across two turns. Spacing is now CSS, no blank-row assertions."""
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
        payloads = [b.text_payload() for b in pane.query(CopyableBlock)]
        tool_i = next(i for i, p in enumerate(payloads) if p == "Read(f.py)")
        res_i = next(i for i, p in enumerate(payloads) if p == "ok")
        done_i = next(i for i, p in enumerate(payloads) if "done in" in p)
        u2_i = next(i for i, p in enumerate(payloads) if p == "second")
        # tool_use is immediately followed by its result
        assert res_i == tool_i + 1, payloads[tool_i:res_i + 1]
        # ordering: tool → result → done → next-user, no other blocks between
        assert done_i > res_i and u2_i > done_i


@pytest.mark.asyncio
async def test_working_indicator_mounts_in_transcript_and_clears_on_finish():
    """While a turn is in flight, a WorkingIndicator is mounted as
    the last child of the transcript scroll (so it sits right under
    the latest block). When the turn settles, it's removed entirely."""
    from aegis.tui.pane import WorkingIndicator

    app = _app()   # FakeSession echo (responds + Result)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        transcript = pane.query_one("#transcript")
        # Idle: no indicator at all.
        assert len(pane.query(WorkingIndicator)) == 0
        pane.query_one(Input).value = "ping"
        await pilot.press("enter")
        await pilot.pause()
        # If still working, an indicator exists INSIDE the transcript.
        if pane.state is AgentState.working:
            inds = pane.query(WorkingIndicator)
            assert len(inds) == 1
            ind = inds.first()
            assert ind.parent is transcript
            assert ind._started_at is not None
        # Wait for turn to complete.
        for _ in range(20):
            await pilot.pause()
            if pane.state is not AgentState.working:
                break
        # Settled: indicator removed.
        assert pane.state is AgentState.ready
        assert len(pane.query(WorkingIndicator)) == 0


@pytest.mark.asyncio
async def test_clicking_a_block_copies_its_payload_to_clipboard():
    """Click handler on a CopyableBlock calls app.copy_to_clipboard
    with the block's text payload."""
    app = _app()
    copied: list[str] = []
    async with app.run_test() as pilot:
        # Replace the clipboard hook to avoid OSC52 in the test runner.
        app.copy_to_clipboard = lambda text: copied.append(text)
        app.notify = lambda *a, **kw: None
        pane = app._panes[0]
        pane.query_one(Input).value = "hello"
        await pilot.press("enter")
        await pilot.pause(); await pilot.pause()
        # Find the user block and click it.
        blocks = list(pane.query(CopyableBlock))
        user_block = next(b for b in blocks
                           if b.text_payload() == "hello")
        await pilot.click(user_block)
        await pilot.pause()
        assert copied == ["hello"], copied


@pytest.mark.asyncio
async def test_each_block_has_click_to_copy_tooltip():
    """Every CopyableBlock advertises 'click to copy' via Textual's
    tooltip system (floating overlay; no layout shift, no extra row)."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.query_one(Input).value = "x"
        await pilot.press("enter")
        await pilot.pause(); await pilot.pause()
        for b in pane.query(CopyableBlock):
            assert b.tooltip == "click to copy", b


@pytest.mark.asyncio
async def test_queue_manager_built_from_queues_kwarg():
    """When queues are configured, AegisApp constructs a real QueueManager
    bound to the inbox_router; bridge.queue_manager.list_queues reflects."""
    from aegis.queue import InboxRouter, Queue, QueueManager

    queues = {"impl": Queue(name="impl", agent_profile="default",
                            max_parallel=1)}
    app = _app(queues=queues)
    async with app.run_test():
        assert isinstance(app.queue_manager, QueueManager)
        assert app.queue_manager.list_queues() == ["impl"]
        assert isinstance(app.inbox_router, InboxRouter)


@pytest.mark.asyncio
async def test_initial_pane_is_bound_to_inbox_router():
    """The default pane spawned in on_mount must be bound — peer handoffs
    and queue callbacks routed to its handle reach the right pane."""
    app = _app()
    async with app.run_test():
        pane = app._panes[0]
        # Internal: InboxRouter._sessions maps handle → session-like.
        assert pane.handle in app.inbox_router._sessions
        assert app.inbox_router._sessions[pane.handle] is pane._core


@pytest.mark.asyncio
async def test_close_tab_unbinds_inbox_session():
    """Closing a pane removes its inbox binding so the router doesn't
    hold a dangling reference to a torn-down AgentSession."""
    f = _factory(FakeSession(), FakeSession())
    app = _app(f)
    async with app.run_test() as pilot:
        await app._spawn(app._default_agent)   # second pane
        handles = [p.handle for p in app._panes]
        assert all(h in app.inbox_router._sessions for h in handles)
        closed = app._panes[-1].handle
        await pilot.press("ctrl+w")
        assert closed not in app.inbox_router._sessions


@pytest.mark.asyncio
async def test_queue_enqueue_spawns_worker_pane_and_dispatches_payload():
    """Mid-flight check: enqueue produces a worker pane bound to the inbox
    and feeds the payload to the worker's first turn. Uses a hanging
    worker so we can observe the pane before _finalize tears it down."""
    import asyncio

    from aegis.queue import Queue, sender_agent

    class HangingSession:
        def __init__(self):
            self.sent: list[str] = []
            self.started = self.closed = False
            self._gate = asyncio.Event()

        async def start(self): self.started = True
        async def send(self, t): self.sent.append(t)
        async def events(self):
            await self._gate.wait()
            yield AssistantText("never")  # pragma: no cover
        async def close(self): self.closed = True

    producer_sess = FakeSession()
    worker_sess = HangingSession()
    f = _factory(producer_sess, worker_sess)
    queues = {"impl": Queue(name="impl", agent_profile="default",
                            max_parallel=1)}
    app = _app(f, queues=queues)
    async with app.run_test() as pilot:
        producer = app._panes[0]
        tid, pos = app.queue_manager.enqueue(
            "impl", "do the thing",
            enqueued_by=sender_agent(producer.handle), callback=True)
        assert pos == 1
        # _SessionManagerAdapter.spawn appends synchronously, then schedules
        # mount + opening-prompt as a task. After enqueue returns the pane
        # is already in app._panes; one pilot.pause runs the mount-and-kick
        # task so the worker sees its payload.
        assert len(app._panes) == 2
        worker_pane = app._panes[-1]
        assert worker_pane.handle in app.inbox_router._sessions
        await pilot.pause()
        await pilot.pause()
        assert worker_sess.sent == ["do the thing"]


@pytest.mark.asyncio
async def test_queue_spawn_works_without_active_app_context():
    """Regression for the NoActiveAppError crash: when an MCP tool handler
    fires enqueue, the handler is on the App's asyncio loop but outside
    Textual's active_app ContextVar (FastMCP/uvicorn doesn't propagate
    it). The adapter must use App.run_worker (which sets up the context)
    rather than bare asyncio.create_task. Simulated here by invoking
    enqueue inside a fresh contextvars.Context that lacks the var."""
    import contextvars

    from aegis.queue import Queue, sender_agent

    producer_sess = FakeSession()
    worker_sess = FakeSession(
        script=lambda t: [AssistantText(text="OK"),
                          Result(duration_ms=1, is_error=False)])
    f = _factory(producer_sess, worker_sess)
    queues = {"impl": Queue(name="impl", agent_profile="default",
                            max_parallel=1)}
    app = _app(f, queues=queues)
    async with app.run_test() as pilot:
        producer = app._panes[0]

        def call_enqueue():
            return app.queue_manager.enqueue(
                "impl", "go",
                enqueued_by=sender_agent(producer.handle), callback=True)

        # Run enqueue under a fresh context — no active_app inherited.
        ctx = contextvars.Context()
        tid, _ = ctx.run(call_enqueue)
        # If the adapter used bare asyncio.create_task the worker pane's
        # compose() would crash NoActiveAppError on the next tick. With
        # App.run_worker, the mount task runs inside Textual's context.
        for _ in range(30):
            await pilot.pause()
            if app.queue_manager.status(tid)["status"] == "completed":
                break
        st = app.queue_manager.status(tid)
        assert st["status"] == "completed"
        assert "OK" in (st["result"] or "")


@pytest.mark.asyncio
async def test_queue_callback_round_trip_into_producer_pane():
    """Full loop: completion → finalize → InboxRouter.deliver →
    producer pane's AgentSession.deliver renders the substrate-tagged
    batch into the producer's harness as a fresh user turn."""
    from aegis.queue import Queue, sender_agent

    producer_sess = FakeSession()
    worker_sess = FakeSession(
        script=lambda t: [AssistantText(text="WORKER-DONE"),
                          Result(duration_ms=1, is_error=False)])
    f = _factory(producer_sess, worker_sess)
    queues = {"impl": Queue(name="impl", agent_profile="default",
                            max_parallel=1)}
    app = _app(f, queues=queues)
    async with app.run_test() as pilot:
        producer = app._panes[0]
        tid, _ = app.queue_manager.enqueue(
            "impl", "do the thing",
            enqueued_by=sender_agent(producer.handle), callback=True)
        for _ in range(30):
            await pilot.pause()
            if app.queue_manager.status(tid)["status"] == "completed":
                break
        st = app.queue_manager.status(tid)
        assert st["status"] == "completed"
        assert "WORKER-DONE" in (st["result"] or "")
        # Producer's harness received the wake-on-idle turn — its sent
        # list now holds the substrate-rendered batch (header + body).
        await pilot.pause()
        await pilot.pause()
        callback_turns = [s for s in producer_sess.sent
                          if "> from queue:impl" in s]
        assert callback_turns, (
            f"producer never woke with callback; sent={producer_sess.sent}")
        body = callback_turns[0]
        assert tid in body and "WORKER-DONE" in body


@pytest.mark.asyncio
async def test_aegisapp_spawn_mounts_pane_and_returns_handle():
    app = _app(_factory(FakeSession()))
    async with app.run_test() as pilot:
        h = await app.spawn("default", handle="vivid-laplace")
        await pilot.pause()
        assert h == "vivid-laplace"
        assert any(p.handle == "vivid-laplace" for p in app._panes)


@pytest.mark.asyncio
async def test_aegisapp_close_removes_pane():
    app = _app(_factory(FakeSession(), FakeSession()))
    async with app.run_test() as pilot:
        h = await app.spawn("default", handle="vivid-laplace")
        await pilot.pause()
        await app.close(h)
        await pilot.pause()
        assert not any(p.handle == "vivid-laplace" for p in app._panes)
