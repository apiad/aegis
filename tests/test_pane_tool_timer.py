"""Per-tool spinner + timer + click-to-expand args (VS2/VS3)."""
import pytest
from rich.console import Console

from aegis.config import Agent
from aegis.events import ToolResult, ToolUse
from aegis.tui.app import AegisApp
from aegis.tui.pane import CopyableBlock
from aegis.tui.state import AgentState


def _agent():
    return Agent(harness="claude-code", model="opus", effort="high",
                 permission="auto")


class _FakeSession:
    def __init__(self): self.sent = []; self.started = self.closed = False
    async def start(self): self.started = True
    async def send(self, t): self.sent.append(t)
    async def events(self):
        if False:
            yield
    async def close(self): self.closed = True


class _FakeMCP:
    url = "http://127.0.0.1:0/mcp/"
    def __init__(self): self.started = self.stopped = False; self.bound = None
    def bind(self, b): self.bound = b
    async def start(self): self.started = True
    async def stop(self): self.stopped = True


def _app():
    return AegisApp({"default": _agent()}, "default",
                    lambda a, u, h: _FakeSession(), _FakeMCP())


def _text_at(pane, idx) -> str:
    con = Console(record=True, width=100)
    con.print(pane._history[idx].renderable)
    return con.export_text()


@pytest.mark.asyncio
async def test_tool_use_opens_running_track_and_timer():
    app = _app()
    async with app.run_test():
        pane = app._panes[0]
        pane._on_core_event(None, ToolUse(
            name="Bash", summary="ls", kind="execute", tool_call_id="c1",
            raw_input={"command": "ls -la", "description": "list files"}))
        track = pane._tools["c1"]
        assert not track.done                       # running
        assert pane._tool_timer is not None         # ticker armed
        # The block is tagged so a click toggles args (not copy).
        block = pane._mounted_blocks[-1]
        assert block._tool_call_id == "c1"
        # Description shows (not the raw args) on the collapsed line.
        assert "list files" in _text_at(pane, track.idx)


@pytest.mark.asyncio
async def test_tool_result_folds_freezes_timer_and_shows_result():
    app = _app()
    async with app.run_test():
        pane = app._panes[0]
        pane._on_core_event(None, ToolUse(
            name="Bash", summary="ls", kind="execute", tool_call_id="c1",
            raw_input={"command": "ls -la"}))
        pane._on_core_event(None, ToolResult(
            text="file-a\nfile-b", is_error=False, tool_call_id="c1"))
        track = pane._tools["c1"]
        assert track.done and track.elapsed is not None
        assert pane._tool_timer is None             # no runners left → stopped
        assert "file-a" in _text_at(pane, track.idx)


@pytest.mark.asyncio
async def test_click_expands_and_collapses_args():
    app = _app()
    async with app.run_test():
        pane = app._panes[0]
        cmd = "echo " + "x" * 40 + " && echo UNIQUE_TAIL_MARKER"
        pane._on_core_event(None, ToolUse(
            name="Bash", summary="echo", kind="execute", tool_call_id="c1",
            raw_input={"command": cmd, "description": "list files"}))
        idx = pane._tools["c1"].idx
        # Collapsed: long command is truncated, the tail marker is hidden.
        assert "UNIQUE_TAIL_MARKER" not in _text_at(pane, idx)

        pane.on_copyable_block_tool_expand_toggle(
            CopyableBlock.ToolExpandToggle("c1"))
        assert pane._tools["c1"].expanded
        expanded = _text_at(pane, idx)
        assert "UNIQUE_TAIL_MARKER" in expanded       # full command revealed
        assert "# list files" in expanded             # description comment

        pane.on_copyable_block_tool_expand_toggle(
            CopyableBlock.ToolExpandToggle("c1"))
        assert not pane._tools["c1"].expanded
        assert "UNIQUE_TAIL_MARKER" not in _text_at(pane, idx)  # collapsed


@pytest.mark.asyncio
async def test_turn_end_freezes_running_tools():
    app = _app()
    async with app.run_test():
        pane = app._panes[0]
        pane._on_core_event(None, ToolUse(
            name="Bash", summary="sleep", kind="execute", tool_call_id="c1",
            raw_input={"command": "sleep 5"}))
        assert not pane._tools["c1"].done
        pane._on_core_state(None, AgentState.ready, True)   # turn finished
        assert pane._tools["c1"].done
        assert pane._tool_timer is None
