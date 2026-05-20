from __future__ import annotations

from collections.abc import Callable

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import ContentSwitcher

from aegis.config import Agent
from aegis.drivers.base import HarnessSession
from aegis.mcp.bridge import SessionInfo
from aegis.queue import InboxRouter, QueueManager
from aegis.tui.names import generate_name
from aegis.tui.pane import ConversationPane, PaneStateChanged
from aegis.tui.state import AgentState
from aegis.tui.themes import (
    THEMES, DEFAULT_THEME, AegisColors, aegis_colors, INK,
)
from aegis.tui.widgets import TabBar

SessionFactory = Callable[[Agent, str, str], HarnessSession]


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
                 make_session: SessionFactory, mcp,
                 *, queues: "dict | None" = None) -> None:
        super().__init__()
        self._agents = agents
        self._default_agent = default_agent
        self._make_session = make_session
        self._mcp = mcp
        self._panes: list[ConversationPane] = []
        self._palette: AegisColors = aegis_colors(INK)
        self._queues = queues or {}
        # AppBridge surface. AegisApp is the bridge in the interactive
        # (TUI) path. QueueManager spawns workers through an adapter that
        # creates real ConversationPanes (so workers are visible tabs Alex
        # can click into), and the per-pane inbox binding lives in _spawn.
        self.inbox_router = InboxRouter()
        self.queue_manager = QueueManager(
            self._queues, _SessionManagerAdapter(self), self.inbox_router)
        self._mcp.bind(self)

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
        self.query_one(TabBar).set_palette(self._palette)
        await self._mcp.start()
        await self.queue_manager.start()
        await self._spawn(self._default_agent)
        self.set_interval(1.0, self._tick)

    def watch_theme(self, theme_name: str | None) -> None:
        # Recompute seam — exercised once a 2nd theme exists. Dormant now
        # (one theme, set once pre-panes). No-op until running.
        if not self.is_running:
            return
        self._palette = aegis_colors(self.current_theme)
        for pane in self._panes:
            pane.set_palette(self._palette)
        self.query_one(TabBar).set_palette(self._palette)
        self._refresh_tabbar()

    @property
    def _active(self) -> ConversationPane | None:
        cs = self.query_one(ContentSwitcher)
        if cs.current is None:
            return None
        return self.query_one(f"#{cs.current}", ConversationPane)

    async def _spawn(self, slug: str, *,
                     handle: str | None = None,
                     opening_prompt: str | None = None,
                     foreground: bool = True) -> ConversationPane:
        agent = self._agents[slug]
        h = handle or generate_name({p.handle for p in self._panes})
        pane = ConversationPane(
            self._make_session(agent, self._mcp.url, h), agent,
            slug, h, self._palette)
        self._panes.append(pane)
        # Inbox binding goes through the pane's _core AgentSession — the
        # pane's renderer hooks stay primary; queue/handoff observers
        # ride add_*_observer (see core/session.py).
        self.inbox_router.bind_session(h, pane._core)
        cs = self.query_one(ContentSwitcher)
        await cs.mount(pane)
        if foreground:
            cs.current = pane.id
        self._refresh_tabbar()
        if foreground:
            pane.focus_input()
        if opening_prompt is not None:
            # _submit is sync but launches the turn as a worker task.
            pane._submit(opening_prompt)
        return pane

    async def _close_pane(self, pane: ConversationPane) -> None:
        """Unified pane teardown — inbox unbind, then close, remove, list-pop."""
        self.inbox_router.unbind_session(pane.handle)
        await pane.close()
        if pane in self._panes:
            self._panes.remove(pane)
        try:
            await pane.remove()
        except Exception:  # noqa: BLE001 — pane may already be detached
            pass

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
        await self._close_pane(active)
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
        for pane in list(self._panes):
            self.inbox_router.unbind_session(pane.handle)
            await pane.close()
        await self.queue_manager.stop()
        await self._mcp.stop()
        self.exit()

    # --- AppBridge --------------------------------------------------------
    def list_sessions(self) -> list[SessionInfo]:
        active = self._active
        return [
            SessionInfo(handle=p.handle, agent_slug=p.agent_slug,
                        state=p.state.value, active=(p is active),
                        unseen=p.unseen)
            for p in self._panes
        ]

    def list_agents(self) -> list[str]:
        return sorted(self._agents)

    async def handoff(self, from_handle: str, target_handle: str,
                      context: str) -> str:
        # Legacy AppBridge entry point — kept for back-compat with any
        # external caller. The MCP aegis_handoff tool (T4.2) no longer
        # calls this; it routes through inbox_router directly.
        if from_handle == target_handle:
            return "handoff rejected: cannot hand off to yourself"
        target = next((p for p in self._panes
                       if p.handle == target_handle), None)
        if target is None:
            return (f"handoff rejected: no session {target_handle!r} "
                    f"(use aegis_list_sessions)")
        if target.state is AgentState.working:
            return (f"handoff rejected: {target_handle!r} is busy, "
                    f"retry shortly")
        await target.deliver_handoff(from_handle, context)
        return f"delivered to {target_handle}"


class _SessionManagerAdapter:
    """SessionManager-shaped facade over AegisApp for QueueManager.

    QueueManager's dispatch loop expects ``spawn(slug, *, opening_prompt,
    handle)`` to return *synchronously* with an object whose ``handle``,
    ``add_event_observer``, and ``add_state_observer`` are usable
    immediately. AegisApp's real spawn is async (Textual mount lifecycle),
    so we split: the pane and its ``AgentSession`` are constructed
    synchronously (so the adapter has something to hand back), and the
    Textual mount + the worker's opening turn are scheduled as a task.
    """

    def __init__(self, app: "AegisApp") -> None:
        self._app = app

    @property
    def _sessions(self):
        # QueueManager reads this for handle uniqueness (existing helper).
        return [p._core for p in self._app._panes]

    def spawn(self, slug: str, *,
              opening_prompt: str | None = None,
              handle: str | None = None):
        agent = self._app._agents[slug]
        h = handle or generate_name({p.handle for p in self._app._panes})
        pane = ConversationPane(
            self._app._make_session(agent, self._app._mcp.url, h), agent,
            slug, h, self._app._palette)
        self._app._panes.append(pane)
        self._app.inbox_router.bind_session(h, pane._core)
        # App.run_worker (not asyncio.create_task) so the mount task runs
        # inside Textual's active_app ContextVar. Otherwise the pane's
        # compose() fails NoActiveAppError when invoked from an MCP tool
        # handler — same asyncio loop, but the context isn't propagated
        # by bare create_task. foreground=False (mount-and-kick doesn't
        # cs.current = pane.id) so a queue worker doesn't steal focus.
        self._app.run_worker(
            self._mount_and_kick(pane, opening_prompt),
            group=f"queue-spawn-{h}", exclusive=False)
        return pane._core

    async def _mount_and_kick(self, pane: ConversationPane,
                              opening_prompt: str | None) -> None:
        cs = self._app.query_one(ContentSwitcher)
        await cs.mount(pane)
        self._app._refresh_tabbar()
        if opening_prompt is not None:
            pane._submit(opening_prompt)

    async def close(self, handle: str) -> None:
        pane = next((p for p in self._app._panes if p.handle == handle),
                    None)
        if pane is None:
            return
        await self._app._close_pane(pane)
        self._app._refresh_tabbar()
