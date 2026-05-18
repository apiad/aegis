from aegis.mcp.bridge import SessionInfo, AppBridge


def test_sessioninfo_fields():
    s = SessionInfo(handle="lucid-knuth", agent_slug="default",
                    state="ready", active=True, unseen=False)
    assert (s.handle, s.agent_slug, s.state, s.active, s.unseen) == \
        ("lucid-knuth", "default", "ready", True, False)


def test_appbridge_is_runtime_checkable_protocol():
    class Impl:
        def list_sessions(self): return []
        def list_agents(self): return []
        async def handoff(self, a, b, c): return "ok"
    assert isinstance(Impl(), AppBridge)
    assert not isinstance(object(), AppBridge)
