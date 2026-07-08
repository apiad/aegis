import pytest

from aegis.config import Agent
from aegis.events import ToolUse, ToolResult, AssistantText
from aegis.tui.app import AegisApp
from aegis.tui.pane import SubagentBox, CopyableBlock


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


def _reset(pane):
    for b in list(pane.query(CopyableBlock)):
        b.remove()
    pane._history.clear()
    pane._mounted_blocks.clear()
    pane._window_start = 0
    pane._tool_use_idx.clear()
    pane._subagent_boxes.clear()


@pytest.mark.asyncio
async def test_task_children_group_into_a_subagent_box():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        _reset(pane)
        pane._on_core_event(None, ToolUse(
            name="Task", summary="explore X", kind="think", tool_call_id="T1"))
        pane._on_core_event(None, AssistantText(
            text="looking…", parent_tool_use_id="T1"))
        pane._on_core_event(None, ToolUse(
            name="Read", summary="a.py", kind="read", tool_call_id="c1",
            parent_tool_use_id="T1"))
        pane._on_core_event(None, ToolResult(
            text="file body", is_error=False, tool_call_id="c1",
            parent_tool_use_id="T1"))
        pane._on_core_event(None, ToolResult(
            text="subagent done", is_error=False, tool_call_id="T1"))
        await pilot.pause()

        assert len(pane._history) == 1              # one top-level block
        box = pane._subagent_boxes["T1"]
        assert isinstance(box, SubagentBox)
        payload = box.text_payload()
        assert "explore X" in payload               # header
        assert "looking" in payload                 # child text
        assert "a.py" in payload                     # child tool use
        assert "file body" in payload               # child result (folded)
        assert "subagent done" in payload           # footer


@pytest.mark.asyncio
async def test_parallel_tasks_route_to_their_own_boxes():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        _reset(pane)
        pane._on_core_event(None, ToolUse(
            name="Task", summary="task A", kind="think", tool_call_id="TA"))
        pane._on_core_event(None, ToolUse(
            name="Task", summary="task B", kind="think", tool_call_id="TB"))
        # Interleaved children.
        pane._on_core_event(None, AssistantText(
            text="from-B", parent_tool_use_id="TB"))
        pane._on_core_event(None, AssistantText(
            text="from-A", parent_tool_use_id="TA"))
        await pilot.pause()
        assert len(pane._history) == 2
        assert "from-A" in pane._subagent_boxes["TA"].text_payload()
        assert "from-A" not in pane._subagent_boxes["TB"].text_payload()
        assert "from-B" in pane._subagent_boxes["TB"].text_payload()


@pytest.mark.asyncio
async def test_replay_reconstructs_subagent_box_flattened():
    from aegis.state.session_log import EventReplay
    events = [
        ToolUse(name="Task", summary="explore X", kind="think",
                tool_call_id="T1"),
        AssistantText(text="looking", parent_tool_use_id="T1"),
        ToolUse(name="Read", summary="a.py", kind="read", tool_call_id="c1",
                parent_tool_use_id="T1"),
        ToolResult(text="file body", is_error=False, tool_call_id="c1",
                   parent_tool_use_id="T1"),
        ToolResult(text="subagent done", is_error=False, tool_call_id="T1"),
    ]
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for b in list(pane.query(CopyableBlock)):
            b.remove()
        pane._history.clear()
        pane._mounted_blocks.clear()
        pane._window_start = 0
        pane._replay = EventReplay(events=events, interrupted=False)
        await pilot.pause()
        pane._mount_replay()
        await pilot.pause()
        # One record — the whole subagent folded into a single grouped block.
        assert len(pane._history) == 1
        payload = pane._history[0].payload
        assert "explore X" in payload and "looking" in payload
        assert "a.py" in payload and "file body" in payload
        assert "subagent done" in payload


@pytest.mark.asyncio
async def test_child_without_known_box_falls_back_inline():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        _reset(pane)
        pane._on_core_event(None, AssistantText(
            text="orphan child", parent_tool_use_id="UNKNOWN"))
        await pilot.pause()
        assert len(pane._history) == 1  # rendered inline, not dropped
