"""Transcript windowing: bounded mounted widget count, scroll-up reloads."""
import pytest

from aegis.config import Agent
from aegis.events import AssistantText, Result, ToolUse
from aegis.tui.app import AegisApp
from aegis.tui.pane import CopyableBlock


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
        if False:
            yield  # pragma: no cover
    async def close(self): self.closed = True


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"
    def __init__(self):
        self.started = self.stopped = False
        self.bound = None
    def bind(self, bridge): self.bound = bridge
    async def start(self): self.started = True
    async def stop(self): self.stopped = True


def _factory(*sessions):
    it = iter(sessions or (FakeSession(),))
    def make(agent, mcp_url, handle):
        try:
            return next(it)
        except StopIteration:
            return FakeSession()
    return make


def _app():
    return AegisApp({"default": _agent()}, "default",
                    _factory(), FakeMCP())


@pytest.mark.asyncio
async def test_replay_populates_full_history_but_mounts_only_tail():
    """_mount_replay fills _history from the full replay but mounts only the
    last REPLAY_TAIL blocks — so a long resumed session paints instantly
    instead of mounting (then evicting) hundreds of widgets. The rest come
    back on scroll-up from the retained _history."""
    from aegis.state.session_log import EventReplay
    from aegis.tui.pane import N_MAX, REPLAY_TAIL

    events = [
        ToolUse(name="Read", summary=f"f{i}.py", kind="read")
        for i in range(N_MAX + 150)
    ]

    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        # Wipe any state from the default on_mount so we exercise replay
        # on a clean pane.
        for b in list(pane.query(CopyableBlock)):
            b.remove()
        pane._history.clear()
        pane._mounted_blocks.clear()
        pane._window_start = 0
        pane._replay = EventReplay(events=events, interrupted=False)
        await pilot.pause()

        pane._mount_replay()
        await pilot.pause()
        await pilot.pause()

        # Full history retained (for scroll-up), but only the tail mounted.
        assert len(pane._history) == N_MAX + 150
        assert len(pane._mounted_blocks) == REPLAY_TAIL
        assert pane._window_start == len(pane._history) - REPLAY_TAIL
        # The mounted tail is the *last* REPLAY_TAIL records, in order.
        assert (pane._mounted_blocks[-1].text_payload()
                == pane._history[-1].payload)


@pytest.mark.asyncio
async def test_scroll_up_reloads_older_blocks():
    """Scrolling to the top re-mounts up to LOAD_BATCH older blocks."""
    import asyncio
    from textual.containers import VerticalScroll
    from aegis.tui.pane import N_MAX, LOAD_BATCH, DEBOUNCE_S
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        # Build a history big enough that eviction has happened.
        for i in range(N_MAX + 200):
            pane._on_core_event(None, ToolUse(
                name="Read", summary=f"f{i}.py", kind="read"))
        await pilot.pause()
        await pilot.pause()
        start_before = pane._window_start
        assert start_before > 0  # eviction ran

        # Scroll to top to trigger load-older.
        t = pane.query_one("#transcript", VerticalScroll)
        t.scroll_y = 0
        await asyncio.sleep(DEBOUNCE_S + 0.1)
        await pilot.pause()
        await pilot.pause()

        # _window_start moved back by LOAD_BATCH (or to 0).
        expected = max(0, start_before - LOAD_BATCH)
        assert pane._window_start == expected


@pytest.mark.asyncio
async def test_load_older_is_idempotent_while_pending():
    """Multiple rapid scroll events near the top coalesce into one load."""
    import asyncio
    from textual.containers import VerticalScroll
    from aegis.tui.pane import N_MAX, LOAD_BATCH, DEBOUNCE_S
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for i in range(N_MAX + 250):
            pane._on_core_event(None, ToolUse(
                name="Read", summary=f"f{i}.py", kind="read"))
        await pilot.pause()
        await pilot.pause()
        start_before = pane._window_start

        t = pane.query_one("#transcript", VerticalScroll)
        # Burst of three scroll-near-top events before the timer fires.
        t.scroll_y = 0
        t.scroll_y = 1
        t.scroll_y = 0
        await asyncio.sleep(DEBOUNCE_S + 0.1)
        await pilot.pause()
        # Only one batch loaded, not three.
        assert pane._window_start == max(0, start_before - LOAD_BATCH)


@pytest.mark.asyncio
async def test_eviction_caps_mounted_widget_count():
    """Once history exceeds N_MAX and user is at the bottom, eviction
    keeps the mounted CopyableBlock count bounded."""
    from aegis.tui.pane import N_MAX
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        # Pump enough non-streaming events to exceed N_MAX.
        for i in range(N_MAX + 80):
            pane._on_core_event(None, ToolUse(
                name="Read", summary=f"f{i}.py", kind="read"))
        await pilot.pause()
        await pilot.pause()
        assert len(pane._history) == N_MAX + 80
        mounted = len(pane.query(CopyableBlock))
        assert mounted <= N_MAX
        # _window_start advanced past the first eviction.
        assert pane._window_start >= 50


@pytest.mark.asyncio
async def test_no_eviction_while_user_scrolled_up():
    """User reading old content does not get yanked when new events arrive."""
    from textual.containers import VerticalScroll
    from aegis.tui.pane import N_MAX
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        # Fill close to but under N_MAX so no eviction has run yet.
        for i in range(N_MAX - 10):
            pane._on_core_event(None, ToolUse(
                name="Read", summary=f"a{i}.py", kind="read"))
        await pilot.pause()
        await pilot.pause()
        # Scroll up.
        t = pane.query_one("#transcript", VerticalScroll)
        t.scroll_y = 0
        await pilot.pause()
        assert pane._stick_to_bottom is False
        start_before = pane._window_start
        # Pump more events that, with sticky=True, would have triggered eviction.
        for i in range(50):
            pane._on_core_event(None, ToolUse(
                name="Read", summary=f"b{i}.py", kind="read"))
        await pilot.pause()
        # No eviction happened — user's scroll position protected.
        assert pane._window_start == start_before


@pytest.mark.asyncio
async def test_sticky_bottom_flag_starts_true_and_flips_on_scroll_up():
    """Pane starts sticky; scrolling away from the bottom flips the flag."""
    from textual.containers import VerticalScroll
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        # Fresh pane is at scroll_y=0 == max_scroll_y=0 → sticky.
        assert pane._stick_to_bottom is True

        # Pump enough events to make the transcript scrollable.
        for i in range(60):
            pane._on_core_event(
                None, ToolUse(name="Read", summary=f"f{i}.py", kind="read"))
        await pilot.pause()
        await pilot.pause()

        t = pane.query_one("#transcript", VerticalScroll)
        # Scroll to top.
        t.scroll_y = 0
        await pilot.pause()
        assert pane._stick_to_bottom is False

        # Scroll back to bottom.
        t.scroll_y = t.max_scroll_y
        await pilot.pause()
        assert pane._stick_to_bottom is True


@pytest.mark.asyncio
async def test_streaming_updates_history_record_in_place():
    """Three streamed AssistantText chunks coalesce into one widget AND
    one BlockRecord whose payload reflects the full concatenated text."""
    app = _app()
    async with app.run_test():
        pane = app._panes[0]
        pane._on_core_event(None, AssistantText(text="hel", usage=None))
        pane._on_core_event(None, AssistantText(text="lo ", usage=None))
        pane._on_core_event(None, AssistantText(text="world", usage=None))
        assert len(pane._history) == 1
        assert pane._history[0].payload == "hello world"


@pytest.mark.asyncio
async def test_history_records_every_event():
    """Every rendered event appends a BlockRecord to _history."""
    app = _app()
    async with app.run_test():
        pane = app._panes[0]
        pane._on_core_event(None, AssistantText(text="hello", usage=None))
        pane._on_core_event(None, ToolUse(name="Read", summary="x.py", kind="read"))
        pane._on_core_event(None, Result(duration_ms=1, is_error=False))
        # Streaming text + tool use + result → 3 records.
        assert len(pane._history) == 3
        # Each record has a renderable and a payload string.
        for rec in pane._history:
            assert rec.renderable is not None
            assert isinstance(rec.payload, str)


@pytest.mark.asyncio
async def test_parallel_tool_results_fold_into_their_use():
    """Parallel tool calls emit all uses first, then results (often out of
    order). Each result must fold into its OWN use block by tool_call_id —
    not pile up as trailing blocks."""
    from aegis.events import ToolResult
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for b in list(pane.query(CopyableBlock)):
            b.remove()
        pane._history.clear()
        pane._mounted_blocks.clear()
        pane._window_start = 0
        pane._tool_use_idx.clear()
        # Two parallel uses, then results in REVERSE order.
        pane._on_core_event(None, ToolUse(
            name="Read", summary="a.py", kind="read", tool_call_id="A"))
        pane._on_core_event(None, ToolUse(
            name="Read", summary="b.py", kind="read", tool_call_id="B"))
        pane._on_core_event(None, ToolResult(
            text="result-of-B", is_error=False, tool_call_id="B"))
        pane._on_core_event(None, ToolResult(
            text="result-of-A", is_error=False, tool_call_id="A"))
        await pilot.pause()
        # One block per call, in use order — results folded in, not appended.
        assert len(pane._history) == 2
        assert "a.py" in pane._history[0].payload
        assert "result-of-A" in pane._history[0].payload
        assert "b.py" in pane._history[1].payload
        assert "result-of-B" in pane._history[1].payload


@pytest.mark.asyncio
async def test_tool_result_without_known_use_appends():
    """A ToolResult with no matching use (e.g. use scrolled out) falls back
    to a standalone block rather than being dropped."""
    from aegis.events import ToolResult
    app = _app()
    async with app.run_test():
        pane = app._panes[0]
        for b in list(pane.query(CopyableBlock)):
            b.remove()
        pane._history.clear()
        pane._mounted_blocks.clear()
        pane._window_start = 0
        pane._tool_use_idx.clear()
        pane._on_core_event(None, ToolResult(
            text="orphan", is_error=False, tool_call_id="ZZZ"))
        assert len(pane._history) == 1
        assert "orphan" in pane._history[0].payload
