from __future__ import annotations

from collections.abc import Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import ContentSwitcher, Input

from aegis.config import Agent
from aegis.drivers.base import HarnessSession
from aegis.tui.names import generate_name
from aegis.tui.pane import ConversationPane, PaneStateChanged
from aegis.tui.widgets import TabBar

SessionFactory = Callable[[Agent], HarnessSession]


class AegisApp(App):
    CSS = """
    TabBar { height: 1; background: $panel; }
    ContentSwitcher { height: 1fr; }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("escape", "interrupt", "Interrupt", priority=True),
    ]

    def __init__(self, agents: dict[str, Agent], default_agent: str,
                 make_session: SessionFactory) -> None:
        super().__init__()
        self._agents = agents
        self._default_agent = default_agent
        self._make_session = make_session
        self._panes: list[ConversationPane] = []

    def compose(self) -> ComposeResult:
        yield TabBar()
        yield ContentSwitcher()

    async def on_mount(self) -> None:
        await self._spawn(self._default_agent)
        self.set_interval(1.0, self._tick)

    @property
    def _active(self) -> ConversationPane | None:
        cs = self.query_one(ContentSwitcher)
        if cs.current is None:
            return None
        return self.query_one(f"#{cs.current}", ConversationPane)

    async def _spawn(self, slug: str) -> None:
        agent = self._agents[slug]
        handle = generate_name({p.handle for p in self._panes})
        pane = ConversationPane(self._make_session(agent), agent,
                                slug, handle)
        self._panes.append(pane)
        cs = self.query_one(ContentSwitcher)
        await cs.mount(pane)
        cs.current = pane.id
        self._refresh_tabbar()
        pane.focus_input()

    def _refresh_tabbar(self) -> None:
        cs = self.query_one(ContentSwitcher)
        items = [
            (i + 1, p.handle, p.agent_slug, p.state, p.unseen,
             p.id == cs.current)
            for i, p in enumerate(self._panes)
        ]
        self.query_one(TabBar).set_tabs(items)

    def _tick(self) -> None:
        active = self._active
        if active is not None:
            active.refresh_metrics()

    def on_pane_state_changed(self, message: PaneStateChanged) -> None:
        if message.finished:
            self.bell()
        self._refresh_tabbar()

    def action_interrupt(self) -> None:
        active = self._active
        if active is not None:
            active.interrupt()

    async def action_quit(self) -> None:
        for pane in self._panes:
            await pane.close()
        self.exit()
