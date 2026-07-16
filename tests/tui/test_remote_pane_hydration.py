"""Tests for B2: remote TUI opens with zero tabs; Ctrl+N crashes.

Covers:
- Pane hydration: on_mount (remote branch) creates ConversationPanes for
  pre-existing sessions returned by list_sessions().
- Ctrl+N safety: action_new_tab / action_pick_agent in remote mode delegates
  to _remote_manager.spawn() instead of the local _spawn() path.
- Default-agent fallback: when CLI passes agent="" and _agents is populated
  from remote list_agents(), _default_agent falls back to first key.
"""
from __future__ import annotations

import pathlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Shared fake helpers
# ---------------------------------------------------------------------------

class _FakeSessionInfo:
    def __init__(self, handle: str, agent_slug: str = "main") -> None:
        self.handle = handle
        self.agent_slug = agent_slug
        self.state = "ready"
        self.active = False
        self.unseen = False
        self.spawned_by = None


class _FakeWs:
    def on(self, kind, fn):
        pass

    def on_connection(self, fn):
        pass


class _FakeRemoteManager:
    def __init__(self, sessions=None, agents=None) -> None:
        self._ws = _FakeWs()
        self._tunnel = None
        self._sessions_list = sessions or []
        self._agents_list = agents or ["main"]
        self.spawned = []
        self.close_called = False

        # Aux plane stubs
        from aegis.tui.remote_manager import _DisabledPlane
        self.queue_manager = _DisabledPlane("queue_manager")
        self.inbox_router = _DisabledPlane("inbox_router")
        self.canvas_manager = _DisabledPlane("canvas_manager")
        self.terminal_manager = _DisabledPlane("terminal_manager")
        self.groups = _DisabledPlane("groups")
        self.locks = _DisabledPlane("locks")
        self.workflow_registry = _DisabledPlane("workflow_registry")
        self.remotes: dict = {}
        self.scheduler = None
        self.state_root = pathlib.Path.cwd()

    def list_sessions(self):
        return list(self._sessions_list)

    def list_agents(self):
        return list(self._agents_list)

    def get(self, handle):
        for s in self._sessions_list:
            if s.handle == handle:
                return s
        return None

    def make_pane_core(self, handle: str):
        """Return a minimal fake core for use in ConversationPane(core=...)."""
        from unittest.mock import MagicMock
        from types import SimpleNamespace
        from aegis.tui.state import AgentState
        core = MagicMock()
        core.handle = handle
        core.state = AgentState.ready
        core.spawned_by = None
        core.metrics = SimpleNamespace(render=lambda _t: "")
        core.add_event_observer = MagicMock()
        core.add_state_observer = MagicMock()
        core.add_inbox_observer = MagicMock()
        core.add_dispatch_observer = MagicMock()
        return core

    def _add_session(self, si: dict) -> None:
        from aegis.mcp.bridge import SessionInfo
        info = SessionInfo(
            handle=si["handle"],
            agent_slug=si.get("agent_slug", "main"),
            state=si.get("state", "ready"),
            active=si.get("active", False),
            unseen=si.get("unseen", False),
            spawned_by=si.get("spawned_by"),
        )
        # Add to our list if not already there
        existing = [s.handle for s in self._sessions_list]
        if info.handle not in existing:
            self._sessions_list.append(info)

    async def spawn(self, profile, *, handle=None, opening_prompt=None,
                    spawned_by=None):
        self.spawned.append(profile)
        return f"new-{profile}"

    async def close(self, handle: str) -> None:
        pass  # session close (by handle)

    async def shutdown(self) -> None:
        self.close_called = True

    def inline_schedule_names(self):
        return set()


# ---------------------------------------------------------------------------
# B2 pane hydration test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spawn_remote_pane_adds_to_panes_list():
    """_spawn_remote_pane must append a ConversationPane to app._panes.

    Tests the core hydration primitive independently of Textual lifecycle.
    """
    from aegis.tui.app import AegisApp
    from aegis.tui.pane import ConversationPane

    sessions = [
        _FakeSessionInfo("alpha-session", "main"),
    ]
    mgr = _FakeRemoteManager(sessions=sessions, agents=["main"])

    app = AegisApp(
        agents={"main": None},
        default_agent="main",
        make_session=None,
        mcp=None,
        manager=mgr,
    )

    # Stub out the ContentSwitcher mount and focus_input.
    mounted = []

    class _FakeCS:
        current = None
        async def mount(self, widget):
            mounted.append(widget)

    class _FakePaneNoMount(ConversationPane):
        async def on_mount(self):
            pass  # skip Textual widget queries

        def focus_input(self):
            pass  # no widgets mounted

    fake_cs = _FakeCS()
    fake_tabbar = MagicMock(set_tabs=MagicMock(), set_palette=MagicMock())

    def _query_one(cls):
        from aegis.tui.widgets import TabBar
        from textual.widgets import ContentSwitcher
        if cls is TabBar:
            return fake_tabbar
        return fake_cs

    app.query_one = _query_one
    # Patch ConversationPane to avoid Textual widget query in __init__ / on_mount
    info = _FakeSessionInfo("alpha-session", "main")

    # Directly test _spawn_remote_pane
    with patch("aegis.tui.app.ConversationPane", _FakePaneNoMount):
        await app._spawn_remote_pane(info, foreground=False)

    assert len(app._panes) == 1, (
        f"Expected 1 pane after _spawn_remote_pane, got {len(app._panes)}"
    )
    assert isinstance(app._panes[0], ConversationPane)


@pytest.mark.asyncio
async def test_on_mount_remote_hydrates_panes_for_existing_sessions():
    """on_mount in remote mode must call _spawn_remote_pane for each session
    returned by list_sessions() so _panes is populated."""
    from aegis.tui.app import AegisApp

    sessions = [
        _FakeSessionInfo("alpha-session", "main"),
        _FakeSessionInfo("beta-session", "main"),
    ]
    mgr = _FakeRemoteManager(sessions=sessions, agents=["main"])

    app = AegisApp(
        agents={"main": None},
        default_agent="main",
        make_session=None,
        mcp=None,
        manager=mgr,
    )

    # Replace _spawn_remote_pane with a counter.
    spawn_calls = []

    async def _fake_spawn_remote_pane(info, foreground=False):
        spawn_calls.append(info.handle)

    app._spawn_remote_pane = _fake_spawn_remote_pane
    app._wire_remote_handlers = MagicMock()
    app.set_interval = MagicMock()
    app.register_theme = MagicMock()
    app.bind = MagicMock()
    app.query_one = MagicMock(return_value=MagicMock(current=None))
    type(app).theme = property(fget=lambda self: "default",
                               fset=lambda self, v: None)
    type(app).current_theme = property(fget=lambda self: MagicMock())

    await app.on_mount()

    assert len(spawn_calls) == 2, (
        f"Expected 2 _spawn_remote_pane calls (one per session), "
        f"got {len(spawn_calls)}: {spawn_calls}"
    )


# ---------------------------------------------------------------------------
# B2 Ctrl+N safety test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_action_new_tab_remote_delegates_to_manager_spawn():
    """In remote mode, action_new_tab must call _remote_manager.spawn()
    instead of local _spawn(). The old path crashes with KeyError on
    self._agents[slug] (since agents values are None) and AttributeError on
    self._mcp.url (since _mcp is None)."""
    from aegis.tui.app import AegisApp

    mgr = _FakeRemoteManager(sessions=[], agents=["main"])
    app = AegisApp(
        agents={"main": None},
        default_agent="main",
        make_session=None,
        mcp=None,
        manager=mgr,
    )

    # Stub _spawn_remote_pane so we don't need the Textual screen stack.
    pane_spawned = []

    async def _fake_spawn_pane(info, foreground=False):
        pane_spawned.append(info.handle)

    app._spawn_remote_pane = _fake_spawn_pane

    # Must not raise KeyError or AttributeError
    try:
        await app.action_new_tab()
    except (KeyError, AttributeError) as exc:
        pytest.fail(f"action_new_tab raised {type(exc).__name__}: {exc}")

    # The manager's spawn should have been called
    assert mgr.spawned, "manager.spawn() was not called by action_new_tab"


@pytest.mark.asyncio
async def test_default_agent_fallback_when_empty():
    """When CLI passes agent="" (no explicit --agent flag), and _agents is
    populated from list_agents(), _default_agent must fall back to the first
    key so Ctrl+N can spawn something."""
    from aegis.tui.app import AegisApp

    mgr = _FakeRemoteManager(sessions=[], agents=["alpha", "beta"])
    # _run_tui_with_manager passes agent or "" as default_agent
    app = AegisApp(
        agents={"alpha": None, "beta": None},
        default_agent="",
        make_session=None,
        mcp=None,
        manager=mgr,
    )

    # Stub _spawn_remote_pane so we don't need the Textual screen stack.
    pane_spawned = []

    async def _fake_spawn_pane(info, foreground=False):
        pane_spawned.append(getattr(info, "handle", "?"))

    app._spawn_remote_pane = _fake_spawn_pane

    # Trigger the fallback (normally done in on_mount or action_new_tab)
    try:
        await app.action_new_tab()
    except (KeyError, AttributeError) as exc:
        pytest.fail(
            f"action_new_tab with empty default_agent raised "
            f"{type(exc).__name__}: {exc}"
        )
    assert mgr.spawned, "manager.spawn() was not called"
