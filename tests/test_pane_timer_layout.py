"""The 10 Hz timers must not ask Textual for a layout pass.

A layout refresh rebuilds the whole compositor map. Two timers repaint at
10 Hz for the entire duration of every turn — the working indicator and
the per-tool spinner — and both went through `Static.update()`, which
defaults to `layout=True`. Neither can change height: the indicator is
`height: 1` in its own CSS, and a running tool block only rewrites the
elapsed digits on a line it already occupies.

Round 2 applied exactly this reasoning to StatusBar and missed these two,
which then accounted for most of the layout passes in a working turn.

The two paths that DO change height — attaching a tool result, and
expanding a tool call's args — must keep asking for layout, or the new
lines get drawn into stale geometry.
"""
import pytest

from aegis.config import Agent
from aegis.events import ToolResult, ToolUse
from aegis.tui.app import AegisApp


def _agent():
    return Agent(harness="claude-code", model="opus", effort="high",
                 permission="auto")


class _FakeSession:
    def __init__(self): self.sent = []
    async def start(self): pass
    async def send(self, t): self.sent.append(t)
    async def events(self):
        if False:
            yield  # pragma: no cover
    async def close(self): pass


class _FakeMCP:
    url = "http://127.0.0.1:0/mcp/"
    def bind(self, b): pass
    async def start(self): pass
    async def stop(self): pass


def _app():
    return AegisApp({"default": _agent()}, "default",
                    lambda *a, **kw: _FakeSession(), _FakeMCP())


class _LayoutSpy:
    """Counts app-wide refresh(layout=True), by widget class."""
    def __enter__(self):
        from textual.widget import Widget
        self.counts = {}
        self._orig = Widget.refresh
        orig = self._orig
        counts = self.counts

        def spy(w, *regions, repaint=True, layout=False, recompose=False,
                **kw):
            if layout or recompose:
                name = type(w).__name__
                counts[name] = counts.get(name, 0) + 1
            return orig(w, *regions, repaint=repaint, layout=layout,
                        recompose=recompose, **kw)

        Widget.refresh = spy
        return self

    def __exit__(self, *exc):
        from textual.widget import Widget
        Widget.refresh = self._orig
        return False

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@pytest.mark.asyncio
async def test_working_indicator_tick_asks_for_no_layout():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane._start_indicator()
        await pilot.pause()
        ind = pane._working_indicator()
        assert ind is not None
        # _refresh no-ops until the indicator is actually running.
        assert ind._started_at is not None

        with _LayoutSpy() as spy:
            for _ in range(10):
                ind._tick()
            await pilot.pause()

        assert spy.counts.get("WorkingIndicator", 0) == 0, (
            f"the spinner asked for a layout pass: {spy.counts}")


@pytest.mark.asyncio
async def test_tool_spinner_tick_asks_for_no_layout():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane._on_core_event(None, ToolUse(
            name="Bash", summary="ls", kind="execute", tool_call_id="c1",
            raw_input={"command": "ls -la", "description": "list files"}))
        await pilot.pause()

        with _LayoutSpy() as spy:
            for _ in range(10):
                pane._tick_tools()
            await pilot.pause()

        assert spy.counts.get("CopyableBlock", 0) == 0, (
            f"the tool spinner asked for a layout pass: {spy.counts}")


@pytest.mark.asyncio
async def test_attaching_a_tool_result_still_asks_for_layout():
    """The block grows by the result's lines — stale geometry would clip it."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane._on_core_event(None, ToolUse(
            name="Bash", summary="ls", kind="execute", tool_call_id="c1",
            raw_input={"command": "ls -la"}))
        await pilot.pause()

        with _LayoutSpy() as spy:
            pane._on_core_event(None, ToolResult(
                tool_call_id="c1", text="a\nb\nc", is_error=False))
            await pilot.pause()

        assert spy.counts.get("CopyableBlock", 0) >= 1, (
            f"attaching a result skipped layout: {spy.counts}")


@pytest.mark.asyncio
async def test_expanding_a_tool_block_still_asks_for_layout():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane._on_core_event(None, ToolUse(
            name="Bash", summary="ls", kind="execute", tool_call_id="c1",
            raw_input={"command": "ls -la", "description": "list files"}))
        await pilot.pause()
        track = pane._tools["c1"]

        with _LayoutSpy() as spy:
            track.expanded = True
            pane._render_tool_block(track, scroll=True)
            await pilot.pause()

        assert spy.counts.get("CopyableBlock", 0) >= 1, (
            f"expanding args skipped layout: {spy.counts}")
