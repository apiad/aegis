from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, RichLog

from aegis.config import Agent
from aegis.drivers.base import HarnessSession
from aegis.events import Result
from aegis.render import render_event
from aegis.tui.state import AgentState
from aegis.tui.widgets import StatusBar, TabStrip


class AegisApp(App):
    CSS = """
    TabStrip { height: 1; background: $panel; }
    StatusBar { height: 1; background: $panel; }
    RichLog { height: 1fr; }
    Input { height: 3; }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("escape", "interrupt", "Interrupt", priority=True),
    ]

    def __init__(self, session: HarnessSession, agent: Agent,
                 agent_name: str) -> None:
        super().__init__()
        self._session = session
        self._agent = agent
        self._agent_name = agent_name
        self.state = AgentState.ready

    def compose(self) -> ComposeResult:
        yield TabStrip(self._agent_name)
        with Vertical():
            yield RichLog(markup=False, wrap=True, auto_scroll=True)
            yield StatusBar(self._agent_name, self._agent.model,
                            self._agent.permission.value)
            yield Input(placeholder="type a message…")

    async def on_mount(self) -> None:
        await self._session.start()
        self._set_state(AgentState.ready)
        self.query_one(Input).focus()

    def _set_state(self, state: AgentState) -> None:
        self.state = state
        self.query_one(TabStrip).set_state(state)
        self.query_one(StatusBar).set_state(state)

    def _write(self, renderable) -> None:
        self.query_one(RichLog).write(renderable)

    def _transcript_has(self, needle: str) -> bool:
        log = self.query_one(RichLog)
        for line in log.lines:
            # Strip.text is the public API on textual.strip.Strip objects
            seg_text = line.text if hasattr(line, "text") else str(line)
            if needle in seg_text:
                return True
        return False

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or self.state is AgentState.working:
            return
        inp = self.query_one(Input)
        inp.value = ""
        inp.disabled = True
        self._write(Text.assemble(("› ", "bold"), text))
        self._set_state(AgentState.working)
        self.run_worker(self._run_turn(text), group="turn",
                        exclusive=True)

    async def _run_turn(self, text: str) -> None:
        saw_result = False
        try:
            await self._session.send(text)
            async for ev in self._session.events():
                renderable = render_event(ev)
                if renderable is not None:
                    self._write(renderable)
                if isinstance(ev, Result):
                    saw_result = True
                    self._finish(error=ev.is_error)
        except Exception:  # noqa: BLE001 - surface, don't crash the UI
            self._write(Text("⚠ harness error", style="red"))
            if not saw_result:  # don't double-finish (double bell) if Result already handled
                self._finish(error=True)
            return
        if not saw_result:
            self._write(Text("⚠ harness exited", style="red"))
            self._finish(error=True)

    def _finish(self, *, error: bool) -> None:
        self._set_state(AgentState.error if error else AgentState.ready)
        inp = self.query_one(Input)
        inp.disabled = False
        inp.focus()
        self.bell()

    def action_interrupt(self) -> None:
        if self.state is not AgentState.working:
            return
        self.workers.cancel_group(self, "turn")
        self._write(Text("^C — interrupted", style="dim"))
        self._set_state(AgentState.ready)
        inp = self.query_one(Input)
        inp.disabled = False
        inp.focus()

    async def action_quit(self) -> None:
        await self._session.close()
        self.exit()
