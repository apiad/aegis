from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace as _SN

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import ContentSwitcher

from aegis.config import Agent, VoiceConfig
from aegis.drivers.base import HarnessSession
from aegis.mcp.bridge import SessionInfo
from aegis.queue import InboxRouter, QueueDigest, QueueManager
from aegis.state.workspace import WorkspaceTab, state_dir
from aegis.tui.names import generate_name
from aegis.tui.pane import ConversationPane, PaneStateChanged
from aegis.tui.state import AgentState
from aegis.voice import (
    VoiceSession, prewarm, unavailable_reason, voice_available,
)
from aegis.tui.themes import (
    THEMES, DEFAULT_THEME, AegisColors, aegis_colors, INK,
)
from aegis.tui.widgets import TabBar

SessionFactory = Callable[[Agent, str, str], HarnessSession]


def bootstrap_resume(*, state_dir_path, ws, agents, drivers, cwd, mcp_url,
                     open_tab, open_failed_tab=None):
    """Drive the resume flow. Pure orchestrator.

    - state_dir_path: project state dir.
    - ws: optional pre-loaded Workspace. If None, loads from disk
      (returns "" if no workspace.json present).
    - agents: dict[profile_name -> Agent].
    - drivers: dict[provider_slug -> driver instance with supports_resume +
      resume(agent, cwd, mcp_url, handle, session_id)].
    - open_tab(handle, replay, session): called per resumable tab.
    - open_failed_tab(handle, reason): optional; called when resume() raises.
      If None, the failure is treated as a silent skip with a banner mention.

    Returns the startup-banner string for the active pane:
      ""                                  — nothing to do
      "no resumable tabs (..)"            — caller should exit clean
      "↻ resumed N · skipped M (..)"      — banner to show in active pane
      "↻ resumed N"                       — only resumed, no skips
    """
    from aegis.state.workspace import load
    from aegis.state.session_log import replay_events
    from aegis.tui.resume_plan import plan_resume

    if ws is None:
        ws = load(state_dir_path)
        if ws is None:
            return ""

    plan = plan_resume(ws, agents, drivers)

    if not plan.resumable:
        if not plan.skipped:
            return ""
        return _no_resumable_message(plan.skipped)

    failures: list[tuple[str, str]] = []
    for tp in plan.resumable:
        tab = tp.tab
        drv = drivers[tab.provider]
        agent = agents[tab.profile]
        try:
            session = drv.resume(agent, cwd, mcp_url, tab.handle, tab.session_id)
        except Exception as e:
            if open_failed_tab is not None:
                open_failed_tab(handle=tab.handle, reason=str(e))
            else:
                failures.append((tab.handle, str(e)))
            continue
        replay = replay_events(state_dir_path, tab.handle)
        open_tab(handle=tab.handle, replay=replay, session=session)

    return _banner(resumed=len(plan.resumable) - len(failures),
                   skipped=plan.skipped, failures=failures)


def _no_resumable_message(skipped):
    by_provider: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for s in skipped:
        by_provider[s.tab.provider] = by_provider.get(s.tab.provider, 0) + 1
        by_reason[s.reason.value] = by_reason.get(s.reason.value, 0) + 1
    parts = []
    for prov, n in sorted(by_provider.items()):
        parts.append(f"{n} {prov}")
    reason_parts = sorted(by_reason.items())
    reason_str = ", ".join(f"{r}" for r, _ in reason_parts)
    return (f"no resumable tabs ({len(skipped)} tabs in last workspace: "
            f"{', '.join(parts)} — {reason_str})")


def _banner(resumed: int, skipped, failures) -> str:
    if resumed == 0 and not skipped and not failures:
        return ""
    if not skipped and not failures:
        return f"↻ resumed {resumed} tab(s)"
    parts = []
    if skipped:
        # Group providers for the parenthetical
        provs = sorted({s.tab.provider for s in skipped})
        parts.append(f"skipped {len(skipped)} ({', '.join(provs)})")
    if failures:
        parts.append(f"failed {len(failures)}")
    return f"↻ resumed {resumed} · " + " · ".join(parts)


def pick_workspace_to_resume(state_dir_path: Path, clean: bool) -> "Workspace | None":
    """Return the Workspace to resume, or None for a fresh start.

    None can mean: clean=True, no workspace.json exists, or the file
    was empty. CorruptWorkspace bubbles up to the caller, which is
    responsible for printing a clear error and exiting nonzero.
    """
    if clean:
        return None
    from aegis.state.workspace import load
    return load(state_dir_path)


def write_workspace_snapshot(state_dir_path: Path, tabs, active_handle,
                             *, terminals=None, files=None) -> None:
    """Persist the current tab roster to workspace.json."""
    from aegis.state.workspace import Workspace, save
    save(state_dir_path,
         Workspace(active_handle=active_handle, tabs=list(tabs),
                   terminals=list(terminals or []),
                   files=list(files or [])))


def _provider_slug(pane: ConversationPane) -> str:
    """Return the provider slug string for a pane (e.g. 'claude-code')."""
    # agent.harness is the canonical slug: 'claude-code', 'gemini', 'opencode'
    return pane._agent.harness


def _pane_to_tab(pane: ConversationPane, order: int) -> WorkspaceTab:
    return WorkspaceTab(
        handle=pane.handle,
        profile=pane.agent_slug,
        order=order,
        provider=_provider_slug(pane),
        session_id=getattr(pane._core, "session_id", None),
        created_at=pane._created_at,
    )


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
        Binding("ctrl+e", "new_terminal", "New terminal", priority=True),
        Binding("ctrl+w", "close_tab", "Close tab", priority=True),
        Binding("ctrl+d", "open_dashboard", "Queues", priority=True),
        Binding("ctrl+o", "open_file_picker", "Open file", priority=True),
        Binding("f2", "open_config_panel", "Config", priority=True),
        Binding("ctrl+tab", "next_tab", "Next", priority=True),
        Binding("ctrl+right", "next_tab", "Next", priority=True),
        Binding("ctrl+left", "prev_tab", "Prev", priority=True),
        *[Binding(f"ctrl+{n}", f"goto({n})", f"Tab {n}", priority=True)
          for n in range(1, 10)],
    ]

    def __init__(self, agents: dict[str, Agent], default_agent: str,
                 make_session: SessionFactory, mcp,
                 *, queues: "dict | None" = None,
                 clean: bool = False,
                 drivers: "dict | None" = None,
                 cwd: "str | None" = None,
                 voice: "VoiceConfig | None" = None) -> None:
        super().__init__()
        self._agents = agents
        self._default_agent = default_agent
        self._make_session = make_session
        self._mcp = mcp
        self._clean = clean
        # Driver registry used for workspace resume. Default empty so the
        # test harness doesn't accidentally engage real harness binaries;
        # production wires the full DRIVERS map from cli.py.
        self._drivers: dict = drivers or {}
        self._cwd: str = cwd if cwd is not None else str(Path.cwd())
        # Guards against premature workspace.json writes during on_mount.
        # Setting self.theme = ... in on_mount triggers watch_theme →
        # _refresh_tabbar → _write_snapshot BEFORE the resume path has had
        # a chance to load the prior workspace; without this flag that
        # write would clobber the saved roster with an empty one. Flipped
        # to True after the resume decision lands.
        self._boot_done: bool = False
        self._panes: list[ConversationPane] = []
        self._voice_cfg = voice or VoiceConfig()
        self._voice: VoiceSession | None = None
        self._voice_pane: ConversationPane | None = None
        self._voice_session_factory = VoiceSession
        self._loop = None
        self._palette: AegisColors = aegis_colors(INK)
        self._queues = queues or {}
        self._state_dir: Path = state_dir(Path.cwd())
        from aegis.tui.file_index import FileIndexer
        self._file_indexer = FileIndexer()
        # AppBridge surface. AegisApp is the bridge in the interactive
        # (TUI) path. QueueManager spawns workers through an adapter that
        # creates real ConversationPanes (so workers are visible tabs Alex
        # can click into), and the per-pane inbox binding lives in _spawn.
        self.inbox_router = InboxRouter()
        self.queue_manager = QueueManager(
            self._queues, _SessionManagerAdapter(self), self.inbox_router)
        self.queue_digest = QueueDigest(self.queue_manager)
        self.queue_digest.start()
        # Canvas plane — shared markdown blackboards. Notifier dispatches
        # write events to subscribers via the inbox router.
        from aegis.canvas.manager import CanvasManager
        from aegis.canvas.notify import make_canvas_notifier
        self.canvas_manager = CanvasManager(
            state_dir=self._state_dir,
            notifier=make_canvas_notifier(self.inbox_router))
        # Terminal plane — live shared PTYs reachable via MCP.
        from aegis.terminal.manager import TerminalManager
        from aegis.terminal.notify import make_terminal_notifier
        self.terminal_manager = TerminalManager(
            state_dir=self._state_dir / "terminals")
        self.terminal_manager.set_notifier(
            make_terminal_notifier(self.inbox_router))
        from aegis.groups.bridge import make_groups_bridge
        self.groups = make_groups_bridge(
            session_manager=_GroupSessionAdapter(self),
            inbox_router=self.inbox_router)
        from aegis.locks.bridge import make_locks_bridge
        self.locks = make_locks_bridge(
            live_handles=lambda: {p.handle for p in self._panes
                                  if isinstance(p, ConversationPane)},
            root_fn=lambda: self.state_root or Path.cwd(),
            state_dir=self._state_dir)
        self.remotes: dict = {}  # populated later from loaded YAML
        # Scheduler-context stubs to satisfy AppBridge. The TUI does not
        # run a scheduler; the aegis_schedule_* MCP tools will gracefully
        # return errors when scheduler is None.
        self.scheduler = None
        self.state_root: Path = Path.cwd()
        self.workflow_registry = _SN(get=lambda _: None)
        self._mcp.bind(self)

    def inline_schedule_names(self) -> set[str]:
        return set()

    def compose(self) -> ComposeResult:
        yield TabBar()
        yield ContentSwitcher()

    @property
    def palette(self) -> AegisColors:
        return self._palette

    @property
    def session_manager(self):
        return _SessionFocusAdapter(self)

    async def on_mount(self) -> None:
        for theme in THEMES.values():
            self.register_theme(theme)
        self.theme = DEFAULT_THEME
        self._palette = aegis_colors(self.current_theme)
        self.query_one(TabBar).set_palette(self._palette)
        if self._voice_cfg.enabled:
            import asyncio
            import threading
            self._loop = asyncio.get_running_loop()
            self.bind(self._voice_cfg.key, "toggle_voice", description="Voice")
            # Load whisper + Silero in the background so the first ctrl+g is
            # responsive instead of blocking several seconds on a cold model.
            threading.Thread(
                target=prewarm, args=(self._voice_cfg,),
                name="voice-prewarm", daemon=True).start()
        await self._mcp.start()
        self._file_indexer.start(Path.cwd())
        await self.queue_manager.start()

        # Bootstrap mode: no agents declared yet → open ConfigPanel as
        # the only tab and skip the normal session spawn. The watchdog
        # / explicit F2 refresh path picks up the first agent and
        # the user can then close the panel and `/new` to start a real
        # session (or relaunch — re-binding running session managers
        # to a changed agent set is slice 16).
        if not self._agents:
            from aegis.tui.config_panel import ConfigPanel
            panel = ConfigPanel(Path.cwd())
            self._panes.append(panel)
            cs = self.query_one(ContentSwitcher)
            await cs.mount(panel)
            cs.current = panel.id
            self._boot_done = True
            self._refresh_tabbar()
            panel.focus_input()
            self.set_interval(1.0, self._tick)
            return

        # Load the prior workspace ONCE and share it across every resume
        # path. If we let `_maybe_resume_terminals` / `_maybe_resume_files`
        # each re-load from disk, the default-agent `_spawn` (which writes
        # a fresh snapshot when no agent tabs were resumable) would have
        # already clobbered the on-disk terminals/files list.
        ws = (None if self._clean
              else pick_workspace_to_resume(self._state_dir, clean=False))
        resumed_agents = await self._resume_agent_tabs(ws) if ws else False
        self._boot_done = True
        if not resumed_agents:
            await self._spawn(self._default_agent)
        else:
            # Persist the resumed roster now that the guard is open.
            self._write_snapshot()
        if ws is not None:
            await self._resume_terminals(ws)
            await self._resume_files(ws)
        self.set_interval(1.0, self._tick)

    async def _resume_agent_tabs(self, ws) -> bool:
        """Restore agent tabs from the in-memory workspace snapshot.
        Returns True iff at least one tab was resumed (caller skips the
        default spawn). False means: --clean, no workspace, or nothing
        resumable — fall through to the default spawn."""
        if not ws.tabs:
            return False

        from aegis.state.session_log import replay_events
        from aegis.tui.resume_plan import plan_resume

        plan = plan_resume(ws, self._agents, self._drivers)
        if not plan.resumable:
            return False

        cs = self.query_one(ContentSwitcher)
        failures: list[tuple[str, str]] = []
        for tp in plan.resumable:
            tab = tp.tab
            drv = self._drivers[tab.provider]
            agent = self._agents[tab.profile]
            try:
                session = drv.resume(
                    agent, self._cwd, self._mcp.url,
                    tab.handle, tab.session_id)
            except Exception as e:  # noqa: BLE001
                failures.append((tab.handle, str(e)))
                continue
            replay = replay_events(self._state_dir, tab.handle)
            pane = ConversationPane(
                session, agent, tab.profile, tab.handle, self._palette,
                digest=self.queue_digest, state_dir_path=self._state_dir,
                replay=replay)
            self._panes.append(pane)
            self.inbox_router.bind_session(tab.handle, pane._core)
            # Mount hidden. ContentSwitcher only hides children at its own
            # mount or on a current old→new transition; setting cs.current
            # once after the loop (old=None) would leave every prior pane
            # display=True — N stacked query bars. Revealing just the active
            # tab below keeps exactly one visible.
            pane.display = False
            await cs.mount(pane)

        if not self._panes:
            return False

        # Activate the previously-active tab if it came back; otherwise
        # the first restored tab.
        active = next(
            (p for p in self._panes
             if isinstance(p, ConversationPane)
             and p.handle == ws.active_handle),
            self._panes[0])
        cs.current = active.id
        active.focus_input()
        self._refresh_tabbar()

        resumed = len([p for p in self._panes
                       if isinstance(p, ConversationPane)])
        parts = [f"↻ resumed {resumed} tab(s)"]
        if plan.skipped:
            provs = sorted({s.tab.provider for s in plan.skipped})
            parts.append(f"skipped {len(plan.skipped)} ({', '.join(provs)})")
        if failures:
            parts.append(f"failed {len(failures)}")
        active.show_resume_banner(" · ".join(parts))
        return True

    async def _resume_terminals(self, ws) -> None:
        """Re-spawn each saved terminal as a fresh shell over its
        existing ledger. TerminalTab's on_mount renders prior records
        dimmed."""
        if not ws.terminals:
            return
        for t in ws.terminals:
            try:
                await self._spawn_terminal_from_snapshot(t)
            except Exception:  # noqa: BLE001
                # One bad terminal shouldn't block the others or the app.
                continue

    async def _resume_files(self, ws) -> None:
        """Re-open each saved file tab. Editor-state (dirty buffer, cursor)
        is intentionally NOT preserved — file tabs are viewers, not
        long-lived sessions."""
        if not ws.files:
            return
        for f in sorted(ws.files, key=lambda x: x.order):
            try:
                await self._open_file_tab(Path(f.path))
            except Exception:  # noqa: BLE001
                continue

    async def _spawn_terminal_from_snapshot(self, snap) -> None:
        from aegis.tui.terminal_tab import TerminalTab
        info = await self.terminal_manager.spawn(
            name=snap.name, shell=snap.shell, cwd=snap.cwd)
        tab = TerminalTab(self.terminal_manager, info, palette=self._palette)
        self._panes.append(tab)
        cs = self.query_one(ContentSwitcher)
        await cs.mount(tab)
        self._refresh_tabbar()

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
    def _active(self):
        cs = self.query_one(ContentSwitcher)
        if cs.current is None:
            return None
        for p in self._panes:
            if p.id == cs.current:
                return p
        return None

    async def _spawn(self, slug: str, *,
                     handle: str | None = None,
                     opening_prompt: str | None = None,
                     foreground: bool = True) -> ConversationPane:
        agent = self._agents[slug]
        h = handle or generate_name(
            {p.handle for p in self._panes
             if isinstance(p, ConversationPane)})
        pane = ConversationPane(
            self._make_session(agent, self._mcp.url, h), agent,
            slug, h, self._palette, digest=self.queue_digest,
            state_dir_path=self._state_dir)
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

    async def _close_pane(self, pane) -> None:
        """Unified pane teardown — inbox unbind (agent panes only), then
        close, remove, list-pop."""
        if isinstance(pane, ConversationPane):
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
        self._write_snapshot()

    def _write_snapshot(self) -> None:
        if not self._boot_done:
            # Pre-resume: persist nothing so the saved workspace.json
            # survives long enough for _maybe_resume_workspace to load it.
            return
        cs = self.query_one(ContentSwitcher)
        active_handle = None
        if cs.current is not None:
            for p in self._panes:
                if p.id == cs.current:
                    active_handle = p.handle
                    break
        tabs = [_pane_to_tab(p, i) for i, p in enumerate(self._panes)
                if isinstance(p, ConversationPane)]
        from aegis.tui.terminal_tab import TerminalTab
        from aegis.tui.file_tab import FileTab
        from aegis.state.workspace import WorkspaceFile, WorkspaceTerminal
        terms = [
            WorkspaceTerminal(
                name=p._info.name, shell=p._info.shell,
                cwd=p._info.cwd, created_at=p._created_at)
            for p in self._panes if isinstance(p, TerminalTab)
        ]
        from datetime import datetime, timezone
        _now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        files = [
            WorkspaceFile(
                path=str(p._path), order=i,
                created_at=getattr(p, "_created_at", _now))
            for i, p in enumerate(self._panes)
            if isinstance(p, FileTab)
        ]
        write_workspace_snapshot(self._state_dir, tabs=tabs,
                                 active_handle=active_handle,
                                 terminals=terms, files=files)

    def _tick(self) -> None:
        active = self._active
        if active is not None and hasattr(active, "refresh_metrics"):
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

    @work
    async def action_new_terminal(self) -> None:
        from aegis.tui.picker import TerminalNamePrompt

        name = await self.push_screen_wait(TerminalNamePrompt())
        if not name:
            return
        await self._spawn_terminal(name)

    async def _spawn_terminal(self, name: str):
        from aegis.tui.terminal_tab import TerminalTab
        try:
            info = await self.terminal_manager.spawn(name=name)
        except Exception as e:
            self.notify(f"spawn failed: {e}", timeout=3.0)
            return None
        tab = TerminalTab(self.terminal_manager, info, palette=self._palette)
        self._panes.append(tab)
        cs = self.query_one(ContentSwitcher)
        await cs.mount(tab)
        cs.current = tab.id
        self._refresh_tabbar()
        tab.focus_input()
        return tab

    async def action_open_config_panel(self) -> None:
        """Open (or focus) the ConfigPanel tab."""
        cs = self.query_one(ContentSwitcher)
        # Focus existing panel if one is mounted.
        from aegis.tui.config_panel import ConfigPanel
        for p in self._panes:
            if isinstance(p, ConfigPanel):
                cs.current = p.id
                p.unseen = False
                p.focus_input()
                p.refresh_view()
                self._refresh_tabbar()
                return
        # Otherwise mount a fresh one.
        root = Path.cwd()
        panel = ConfigPanel(root)
        self._panes.append(panel)
        await cs.mount(panel)
        cs.current = panel.id
        self._refresh_tabbar()
        panel.focus_input()

    @work
    async def action_open_file_picker(self, prefill: str = "") -> None:
        from aegis.tui.picker import FilePickerModal
        path = await self.push_screen_wait(FilePickerModal(prefill=prefill))
        if path is not None:
            await self._open_file_tab(path)

    async def _open_file_tab(self, path: Path) -> None:
        from aegis.tui.file_tab import FileTab
        resolved = path.resolve()
        tab_id = f"filetab-{abs(hash(str(resolved)))}"
        for p in self._panes:
            if p.id == tab_id:
                cs = self.query_one(ContentSwitcher)
                cs.current = tab_id
                p.unseen = False
                p.focus_input()
                self._refresh_tabbar()
                return
        tab = FileTab(resolved)
        self._panes.append(tab)
        cs = self.query_one(ContentSwitcher)
        await cs.mount(tab)
        cs.current = tab.id
        self._refresh_tabbar()
        tab.focus_input()

    async def open_file(self, path: str) -> dict:
        """AppBridge entry point for aegis_view_file MCP tool."""
        try:
            resolved = Path(path).resolve()
        except Exception as e:
            return {"status": "error", "reason": str(e)}
        if not resolved.is_file():
            return {"status": "error", "reason": "file not found",
                    "path": str(path)}
        tab_id = f"filetab-{abs(hash(str(resolved)))}"
        for p in self._panes:
            if p.id == tab_id:
                self.run_worker(self._focus_existing_tab(p),
                                group=f"focus-{tab_id}", exclusive=False)
                return {"status": "focused", "path": str(resolved)}
        self.run_worker(self._open_file_tab(resolved),
                        group=f"open-file-{tab_id}", exclusive=False)
        return {"status": "opened", "path": str(resolved)}

    async def _focus_existing_tab(self, tab) -> None:
        cs = self.query_one(ContentSwitcher)
        cs.current = tab.id
        tab.unseen = False
        tab.focus_input()
        self._refresh_tabbar()

    def on_pane_state_changed(self, message: PaneStateChanged) -> None:
        if message.finished:
            if message.pane is not self._active:
                message.pane.unseen = True
            self.bell()
        self._refresh_tabbar()

    def on_terminal_tab_state_changed(self, message) -> None:
        if message.finished and message.tab is not self._active:
            message.tab.unseen = True
        self._refresh_tabbar()

    async def action_open_dashboard(self) -> None:
        from aegis.tui.dashboard import QueueDashboard
        await self.push_screen(QueueDashboard())

    def action_interrupt(self) -> None:
        # The escape binding is priority=True at the app level, so it
        # would otherwise eat escape presses meant to dismiss a modal
        # (the dashboard, the agent picker). Dismiss the modal first
        # and only fall through to interrupt on the default screen.
        from textual.screen import ModalScreen
        if isinstance(self.screen, ModalScreen):
            self.screen.dismiss()
            return
        active = self._active
        if active is not None and hasattr(active, "interrupt"):
            active.interrupt()

    async def action_toggle_voice(self) -> None:
        if self._voice is not None:
            self._stop_voice()
            return
        if not voice_available():
            self.notify(unavailable_reason(), severity="warning")
            return
        pane = self._active
        if not isinstance(pane, ConversationPane):
            return
        base = pane.input_widget().value

        def on_final(text: str, pane=pane, base=base) -> None:
            # Fires from the decode worker thread -> marshal onto the UI loop.
            # Target pane + base are captured per-recording so a late decode
            # always lands on the input it started from.
            self._marshal(self._apply_voice_text, pane, base, text)

        try:
            self._voice = self._voice_session_factory(self._voice_cfg, on_final)
            self._voice.start()
        except Exception as exc:  # noqa: BLE001 — mic/model open failure
            self._voice = None
            self.notify(f"voice failed: {exc}", severity="error")
            return
        self._voice_pane = pane
        pane.set_recording(True)

    def _marshal(self, fn, *args) -> None:
        loop = self._loop
        if loop is None:
            fn(*args)
        else:
            loop.call_soon_threadsafe(fn, *args)

    def _apply_voice_text(self, pane, base: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        joiner = "" if (not base or base.endswith((" ", "\n"))) else " "
        pane.input_widget().value = base + joiner + text

    def _stop_voice(self) -> None:
        voice, pane = self._voice, self._voice_pane
        self._voice = None
        self._voice_pane = None
        if pane is not None:
            pane.set_recording(False)
        if voice is not None:
            voice.stop()   # non-blocking; decode + insert happen off-thread

    async def action_quit(self) -> None:
        if self._voice is not None:
            self._stop_voice()
        # Persist the current roster BEFORE teardown so any session_ids
        # latched mid-turn (after the last tab event) reach disk and the
        # next boot can resume.
        try:
            self._write_snapshot()
        except Exception:  # noqa: BLE001 — snapshot failure must not block quit
            pass
        for pane in list(self._panes):
            if isinstance(pane, ConversationPane):
                self.inbox_router.unbind_session(pane.handle)
            await pane.close()
        self.queue_digest.stop()
        await self.queue_manager.stop()
        await self._mcp.stop()
        self._file_indexer.stop()
        self.exit()

    # --- AppBridge --------------------------------------------------------
    def list_sessions(self) -> list[SessionInfo]:
        active = self._active
        return [
            SessionInfo(handle=p.handle, agent_slug=p.agent_slug,
                        state=p.state.value, active=(p is active),
                        unseen=p.unseen,
                        spawned_by=getattr(p._core, "spawned_by", None))
            for p in self._panes
            if isinstance(p, ConversationPane)
        ]

    def list_agents(self) -> list[str]:
        return sorted(self._agents)

    def register_agent(self, slug: str, agent) -> None:
        existing = self._agents.get(slug)
        if existing is not None:
            if existing == agent:
                return
            raise ValueError(f"agent {slug!r} already registered")
        self._agents[slug] = agent

    def register_queue(self, queue) -> None:
        self.queue_manager.register_queue(queue)

    def reload_plugins(self) -> None:
        from aegis.config import yaml_loader
        cfg = yaml_loader.load_config(self.state_root)
        yaml_loader.import_plugins(cfg)

    async def handoff(self, from_handle: str, target_handle: str,
                      context: str) -> str:
        # Legacy AppBridge entry point — kept for back-compat with any
        # external caller. The MCP aegis_handoff tool (T4.2) no longer
        # calls this; it routes through inbox_router directly.
        if from_handle == target_handle:
            return "handoff rejected: cannot hand off to yourself"
        target = next((p for p in self._panes
                       if isinstance(p, ConversationPane)
                       and p.handle == target_handle), None)
        if target is None:
            return (f"handoff rejected: no session {target_handle!r} "
                    f"(use aegis_list_sessions)")
        if target.state is AgentState.working:
            return (f"handoff rejected: {target_handle!r} is busy, "
                    f"retry shortly")
        await target.deliver_handoff(from_handle, context)
        return f"delivered to {target_handle}"

    async def spawn(self, profile: str, *,
                    handle: str | None = None,
                    opening_prompt: str | None = None,
                    spawned_by: str | None = None) -> str:
        """AppBridge-shaped: spawn a long-lived agent as a TUI pane."""
        sm_adapter = _SessionManagerAdapter(self)
        sess = sm_adapter.spawn(profile, handle=handle,
                                opening_prompt=opening_prompt,
                                spawned_by=spawned_by)
        return sess.handle

    async def close(self, handle: str) -> None:
        """AppBridge-shaped: close a pane by handle."""
        pane = next((p for p in self._panes
                     if isinstance(p, ConversationPane)
                     and p.handle == handle), None)
        if pane is not None:
            await self._close_pane(pane)
            self._refresh_tabbar()

    async def rename_handle(self, old: str, new: str) -> dict:
        """AppBridge-shaped: rename a live pane's handle in-place.

        Swaps the pane's handle, the inbox-router binding, and the
        tabbar label. Used by the ``aegis_rename`` MCP tool.
        """
        from aegis.core.manager import is_valid_handle
        if old == new:
            pane = next((p for p in self._panes
                         if isinstance(p, ConversationPane)
                         and p.handle == old), None)
            if pane is None:
                return {"error": f"no session {old!r}"}
            return {"ok": True, "old": old, "new": new}
        if not is_valid_handle(new):
            return {"error":
                    f"new handle {new!r} fails format: must be 2-3 "
                    f"kebab-case alphanumeric segments, starting with a "
                    f"letter (e.g. 'lucid-river-runs')"}
        pane = next((p for p in self._panes
                     if isinstance(p, ConversationPane)
                     and p.handle == old), None)
        if pane is None:
            return {"error":
                    f"no session {old!r} (use aegis_list_sessions)"}
        collision = next((p for p in self._panes
                          if isinstance(p, ConversationPane)
                          and p.handle == new), None)
        if collision is not None:
            return {"error":
                    f"handle {new!r} already in use by another session"}
        pane.handle = new
        pane._core.handle = new
        self.inbox_router.rename(old, new)
        self.locks.rename(old, new)
        self._refresh_tabbar()
        return {"ok": True, "old": old, "new": new}


class _GroupSessionAdapter:
    """SessionManager-shaped facade over AegisApp for groups wiring.

    GroupWiring needs ``async spawn(profile, handle) -> handle`` and
    ``get(handle) -> session-with-add_event_observer``. AegisApp already
    has both pieces — this adapter unifies them under one surface.
    """

    def __init__(self, app: "AegisApp") -> None:
        self._app = app

    async def spawn(self, *, profile: str,
                    handle: str | None = None) -> str:
        sess = _SessionManagerAdapter(self._app).spawn(
            profile, handle=handle)
        return sess.handle

    def get(self, handle: str):
        for p in self._app._panes:
            if p.handle == handle:
                return p._core
        return None


class _SessionFocusAdapter:
    """Tab-focus facade over AegisApp for QueueDashboard's `>` action.

    Separate from `_SessionManagerAdapter` (which is QueueManager's spawn
    surface). This one only resolves handles to panes and switches the
    ContentSwitcher to the matching tab.
    """

    def __init__(self, app: "AegisApp") -> None:
        self._app = app

    def get(self, handle: str):
        for p in self._app._panes:
            if p.handle == handle:
                return p
        return None

    def focus(self, handle: str) -> None:
        for p in self._app._panes:
            if p.handle == handle:
                self._app.query_one(ContentSwitcher).current = p.id
                return


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
              handle: str | None = None,
              spawned_by: str | None = None):
        agent = self._app._agents[slug]
        h = handle or generate_name({p.handle for p in self._app._panes})
        pane = ConversationPane(
            self._app._make_session(agent, self._app._mcp.url, h), agent,
            slug, h, self._app._palette, digest=self._app.queue_digest,
            state_dir_path=self._app._state_dir)
        pane._core.spawned_by = spawned_by
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
