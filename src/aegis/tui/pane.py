from __future__ import annotations

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, RichLog

from aegis.config import Agent
from aegis.core.session import AgentSession
from aegis.drivers.base import HarnessSession
from aegis.events import Result, ToolUse
from aegis.render import render_event, render_user_line
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
        self._agent = agent
        self.agent_slug = agent_slug
        self.handle = handle
        self._palette = palette
        self.unseen = False
        self._had_turn = False
        self._core = AgentSession(session, agent, agent_slug, handle)
        self._core.on_event = self._on_core_event
        self._core.on_state = self._on_core_state

    @property
    def state(self) -> AgentState:
        return self._core.state

    @property
    def _session(self) -> HarnessSession:
        return self._core._session

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
        self.query_one(StatusBar).set_state(AgentState.ready)
        self.refresh_metrics()

    def refresh_metrics(self) -> None:
        self.query_one(StatusBar).set_metrics(
            self._core.metrics.render(time.monotonic()))

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
        self._submit(text)

    def _submit(self, text: str) -> None:
        inp = self.query_one(Input)
        inp.disabled = True
        if self._had_turn:
            self._write(Text(""))
        self._had_turn = True
        log = self.query_one(RichLog)
        width = log.size.width or 80
        self._write(render_user_line(text, self._palette, width))
        self._write(Text(""))
        self.run_worker(self._core.send(text), group="turn", exclusive=True)

    async def deliver_handoff(self, from_handle: str,
                              context: str) -> None:
        self._submit(f"[handoff from {from_handle}] {context}")

    def _on_core_event(self, _core, ev) -> None:
        renderable = render_event(ev, self._palette)
        if renderable is not None:
            self._write(renderable)
            if not isinstance(ev, (ToolUse, Result)):
                self._write(Text(""))
        self.refresh_metrics()

    def _on_core_state(self, _core, state: AgentState,
                       finished: bool) -> None:
        self.query_one(StatusBar).set_state(state)
        if finished and state is AgentState.error \
                and not self._transcript_has("⚠ harness"):
            self._write(Text("⚠ harness error", style=self._palette.err))
        self.post_message(PaneStateChanged(self, finished))
        if finished:
            inp = self.query_one(Input)
            inp.disabled = False
            inp.focus()
        self.refresh_metrics()

    def interrupt(self) -> None:
        if self.state is not AgentState.working:
            return

        async def _do() -> None:
            await self._core.interrupt()
            self._write(Text("^C — interrupted", style=self._palette.muted))
            self.refresh_metrics()
            inp = self.query_one(Input)
            inp.disabled = False
            inp.focus()

        self.run_worker(_do(), group="turn", exclusive=True)

    async def close(self) -> None:
        await self._core.close()
