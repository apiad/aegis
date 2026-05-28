from aegis.mcp.bridge import SessionInfo, AppBridge


def test_sessioninfo_fields():
    s = SessionInfo(handle="lucid-knuth", agent_slug="default",
                    state="ready", active=True, unseen=False)
    assert (s.handle, s.agent_slug, s.state, s.active, s.unseen) == \
        ("lucid-knuth", "default", "ready", True, False)


def test_appbridge_is_runtime_checkable_protocol():
    from aegis.queue import InboxRouter
    class Impl:
        queue_manager = object()
        inbox_router = InboxRouter()
        canvas_manager = object()
        terminal_manager = object()
        groups = object()
        remotes = {}
        scheduler = None
        state_root = object()
        workflow_registry = object()
        def inline_schedule_names(self): return set()
        def list_sessions(self): return []
        def list_agents(self): return []
        async def handoff(self, a, b, c): return "ok"
        async def spawn(self, profile, *, handle=None): return "h"
        async def close(self, handle): return None
        async def rename_handle(self, old, new): return {"ok": True}
        def register_agent(self, slug, agent): pass
        def register_queue(self, queue): pass
        def reload_plugins(self): pass
    assert isinstance(Impl(), AppBridge)
    assert not isinstance(object(), AppBridge)


def test_appbridge_requires_full_surface():
    from aegis.queue import InboxRouter

    class FullImpl:
        queue_manager = object()
        inbox_router = InboxRouter()
        canvas_manager = object()
        terminal_manager = object()
        groups = object()
        remotes = {}
        scheduler = None
        state_root = object()
        workflow_registry = object()
        def inline_schedule_names(self): return set()
        def list_sessions(self): return []
        def list_agents(self): return []
        async def handoff(self, a, b, c): return "ok"
        async def spawn(self, profile, *, handle=None): return "h"
        async def close(self, handle): return None
        async def rename_handle(self, old, new): return {"ok": True}
        def register_agent(self, slug, agent): pass
        def register_queue(self, queue): pass
        def reload_plugins(self): pass

    class MissingSpawn:
        queue_manager = object()
        inbox_router = InboxRouter()
        canvas_manager = object()
        terminal_manager = object()
        groups = object()
        remotes = {}
        scheduler = None
        state_root = object()
        workflow_registry = object()
        def inline_schedule_names(self): return set()
        def list_sessions(self): return []
        def list_agents(self): return []
        async def handoff(self, a, b, c): return "ok"
        async def close(self, handle): return None
        async def rename_handle(self, old, new): return {"ok": True}
        def register_agent(self, slug, agent): pass
        def register_queue(self, queue): pass
        def reload_plugins(self): pass

    assert isinstance(FullImpl(), AppBridge)
    assert not isinstance(MissingSpawn(), AppBridge)
