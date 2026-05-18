from __future__ import annotations

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, RichLog

from aegis.config import Agent
from aegis.drivers.base import HarnessSession
from aegis.events import Result, ToolResult, ToolUse
from aegis.render import render_event, render_user_line
from aegis.tui.metrics import SessionMetrics
from aegis.tui.state import AgentState
from aegis.tui.widgets import StatusBar


class PaneStateChanged(Message):
    def __init__(self, pane: "ConversationPane", finished: bool) -> None:
        self.pane = pane
        self.finished = finished
        super().__init__()


class ConversationPane(Widget):
    DEFAULT_CSS = """
    ConversationPane { layout: vertical; height: 1fr;
                       background: $background; }
    ConversationPane RichLog { height: 1fr; background: $background;
                               padding: 1 4; scrollbar-size: 0 0; }
    ConversationPane StatusBar { height: 1; background: $panel;
                                 color: $foreground; padding: 0 2; }
    ConversationPane Input { height: 3; background: $surface;
                             color: $foreground; padding: 0 2;
                             border: none;
                             border-top: solid $foreground 20%;
                             border-bottom: solid $foreground 20%;
                             margin-top: 1; }
    ConversationPane Input:focus { border: none;
                             border-top: solid $foreground 20%;
                             border-bottom: solid $foreground 20%; }
    """

    def __init__(self, session: HarnessSession, agent: Agent,
                 agent_slug: str, handle: str, palette) -> None:
        super().__init__(id=f"pane-{handle}")
        self._session = session
        self._agent = agent
        self.agent_slug = agent_slug
        self.handle = handle
        self._palette = palette
        self.state = AgentState.ready
        self.unseen = False
        self._started = False
        self._had_turn = False
        self._metrics = SessionMetrics()

    def set_palette(self, palette) -> None:
        self._palette = palette

    def compose(self) -> ComposeResult:
        with Vertical():
            yield RichLog(markup=False, wrap=True, auto_scroll=True)
            yield StatusBar(self.handle, self.agent_slug,
                            self._agent.model,
                            self._agent.permission.value, self._palette)
            yield Input(placeholder="type a message…")

    async def on_mount(self) -> None:
        # Lazy: the harness subprocess is not started until the first
        # message (see _run_turn). The pane is "ready" with no process yet.
        self._set_state(AgentState.ready, finished=False)
        self.refresh_metrics()

    def _now(self) -> float:
        return time.monotonic()

    def refresh_metrics(self) -> None:
        self.query_one(StatusBar).set_metrics(
            self._metrics.render(self._now()))

    def _set_state(self, state: AgentState, *, finished: bool) -> None:
        self.state = state
        self.query_one(StatusBar).set_state(state)
        self.post_message(PaneStateChanged(self, finished))

    def _write(self, renderable) -> None:
        self.query_one(RichLog).write(renderable)

    def _transcript_has(self, needle: str) -> bool:
        for line in self.query_one(RichLog).lines:
            txt = line.text if hasattr(line, "text") else str(line)
            if needle in txt:
                return True
        return False

    def focus_input(self) -> None:
        self.query_one(Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        text = event.value.strip()
        if not text or self.state is AgentState.working:
            return
        inp = self.query_one(Input)
        inp.value = ""
        inp.disabled = True
        if self._had_turn:
            self._write(Text(""))      # one blank row between turns
        self._had_turn = True
        log = self.query_one(RichLog)
        width = log.size.width or 80
        self._write(render_user_line(text, self._palette, width))
        self._write(Text(""))         # space between user and agent
        self._set_state(AgentState.working, finished=False)
        self._metrics.start_turn(self._now())
        self.run_worker(self._run_turn(text), group="turn", exclusive=True)

    async def _run_turn(self, text: str) -> None:
        saw_result = False
        try:
            if not self._started:
                await self._session.start()
                self._started = True
                self._metrics.begin_session(self._now())
            await self._session.send(text)
            async for ev in self._session.events():
                renderable = render_event(ev, self._palette)
                if renderable is not None:
                    self._write(renderable)
                    # Air after a *completed* step — but keep a tool call
                    # glued to its result, and don't trail the ── done ──
                    # line (the next turn's leading blank separates it).
                    if not isinstance(ev, (ToolUse, Result)):
                        self._write(Text(""))
                if isinstance(ev, ToolUse):
                    self._metrics.record_tool()
                elif isinstance(ev, ToolResult) and ev.is_error:
                    self._metrics.record_tool_error()
                if isinstance(ev, Result):
                    self._metrics.commit(ev.usage, self._now())
                    saw_result = True
                    self._finish(error=ev.is_error)
                else:
                    u = getattr(ev, "usage", None)
                    if u is not None:
                        self._metrics.observe(u)  # provisional, live
                self.refresh_metrics()
        except Exception:  # noqa: BLE001
            self._write(Text("⚠ harness error", style=self._palette.err))
            if not saw_result:
                self._metrics.commit(None, self._now())
                self._finish(error=True)
            return
        if not saw_result:
            self._write(Text("⚠ harness exited", style=self._palette.err))
            self._metrics.commit(None, self._now())
            self._finish(error=True)

    def _finish(self, *, error: bool) -> None:
        self._set_state(AgentState.error if error else AgentState.ready,
                        finished=True)
        inp = self.query_one(Input)
        inp.disabled = False
        inp.focus()

    def interrupt(self) -> None:
        if self.state is not AgentState.working:
            return
        self.workers.cancel_group(self, "turn")
        self._metrics.cancel_turn(self._now())
        self._write(Text("^C — interrupted", style=self._palette.muted))
        self._set_state(AgentState.ready, finished=False)
        self.refresh_metrics()
        inp = self.query_one(Input)
        inp.disabled = False
        inp.focus()

    async def close(self) -> None:
        self.workers.cancel_group(self, "turn")
        if self._started:
            await self._session.close()
