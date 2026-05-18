from __future__ import annotations

from collections.abc import Callable

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import ContentSwitcher

from aegis.config import Agent
from aegis.drivers.base import HarnessSession
from aegis.tui.names import generate_name
from aegis.tui.pane import ConversationPane, PaneStateChanged
from aegis.tui.themes import (
    THEMES, DEFAULT_THEME, AegisColors, aegis_colors, INK,
)
from aegis.tui.widgets import TabBar

SessionFactory = Callable[[Agent], HarnessSession]


class AegisApp(App):
    CSS = """
    Screen { background: $background; }
    TabBar { height: 1; background: $panel; color: $foreground;
             padding: 0 1; }
    ContentSwitcher { height: 1fr; background: $background; }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("escape", "interrupt", "Interrupt", priority=True),
        Binding("ctrl+t", "new_tab", "New tab", priority=True),
        Binding("ctrl+n", "pick_agent", "New tab (pick)", priority=True),
        Binding("ctrl+w", "close_tab", "Close tab", priority=True),
        Binding("ctrl+tab", "next_tab", "Next", priority=True),
        Binding("ctrl+right", "next_tab", "Next", priority=True),
        Binding("ctrl+left", "prev_tab", "Prev", priority=True),
        *[Binding(f"ctrl+{n}", f"goto({n})", f"Tab {n}", priority=True)
          for n in range(1, 10)],
    ]

    def __init__(self, agents: dict[str, Agent], default_agent: str,
                 make_session: SessionFactory) -> None:
        super().__init__()
        self._agents = agents
        self._default_agent = default_agent
        self._make_session = make_session
        self._panes: list[ConversationPane] = []
        self._palette: AegisColors = aegis_colors(INK)

    def compose(self) -> ComposeResult:
        yield TabBar()
        yield ContentSwitcher()

    @property
    def palette(self) -> AegisColors:
        return self._palette

    async def on_mount(self) -> None:
        for theme in THEMES.values():
            self.register_theme(theme)
        self.theme = DEFAULT_THEME
        self._palette = aegis_colors(self.current_theme)
        await self._spawn(self._default_agent)
        self.set_interval(1.0, self._tick)

    def watch_theme(self, theme_name: str | None) -> None:
        # Recompute seam — exercised once a 2nd theme exists. Dormant now
        # (one theme, set once pre-panes). No-op until running.
        if not self.is_running:
            return
        self._palette = aegis_colors(self.current_theme)
        for pane in self._panes:
            pane.set_colors(self._palette)
        self._refresh_tabbar()

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

    def _activate(self, idx: int) -> None:
        if not (0 <= idx < len(self._panes)):
            return
        pane = self._panes[idx]
        self.query_one(ContentSwitcher).current = pane.id
        pane.unseen = False
        pane.focus_input()
        self._refresh_tabbar()

    async def action_new_tab(self) -> None:
        await self._spawn(self._default_agent)

    def action_goto(self, n: int) -> None:
        self._activate(n - 1)

    def action_next_tab(self) -> None:
        active = self._active
        if not self._panes or active is None:
            return
        cur = self._panes.index(active)
        self._activate((cur + 1) % len(self._panes))

    def action_prev_tab(self) -> None:
        active = self._active
        if not self._panes or active is None:
            return
        cur = self._panes.index(active)
        self._activate((cur - 1) % len(self._panes))

    async def action_close_tab(self) -> None:
        active = self._active
        if active is None:
            return
        idx = self._panes.index(active)
        await active.close()
        await active.remove()
        self._panes.pop(idx)
        if not self._panes:
            self.exit()
            return
        self._activate(min(idx, len(self._panes) - 1))

    @work
    async def action_pick_agent(self) -> None:
        from aegis.tui.picker import AgentPicker

        slug = await self.push_screen_wait(
            AgentPicker(sorted(self._agents)))
        if slug:
            await self._spawn(slug)

    def on_pane_state_changed(self, message: PaneStateChanged) -> None:
        if message.finished:
            if message.pane is not self._active:
                message.pane.unseen = True
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
