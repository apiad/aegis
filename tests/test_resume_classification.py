from aegis.state.workspace import Workspace, WorkspaceTab
from aegis.tui.resume_plan import (
    SkipReason, plan_resume, TabPlan,
)


def _tab(handle, profile, provider, session_id, order=0):
    return WorkspaceTab(handle=handle, profile=profile, order=order,
                        provider=provider, session_id=session_id,
                        created_at="2026-05-21T00:00:00Z")


def _agents_with(*profiles):
    return {p: object() for p in profiles}


def _drivers_with(**flags):
    # flags: provider -> supports_resume
    return {name: type("D", (), {"supports_resume": v})() for name, v in flags.items()}


def test_resumable_when_all_conditions_met():
    ws = Workspace(active_handle="a", tabs=[_tab("a", "default", "claude-code", "sid-1")])
    agents = _agents_with("default")
    drivers = _drivers_with(**{"claude-code": True})
    plan = plan_resume(ws, agents, drivers)
    assert len(plan.resumable) == 1
    assert plan.resumable[0].tab.handle == "a"
    assert plan.skipped == []


def test_skip_when_profile_missing():
    ws = Workspace(active_handle="a", tabs=[_tab("a", "ghost", "claude-code", "sid-1")])
    plan = plan_resume(ws, _agents_with("default"),
                       _drivers_with(**{"claude-code": True}))
    assert plan.resumable == []
    assert plan.skipped[0].reason == SkipReason.profile_missing


def test_skip_when_driver_no_resume():
    ws = Workspace(active_handle="a", tabs=[_tab("a", "default", "gemini", "sid-1")])
    plan = plan_resume(ws, _agents_with("default"),
                       _drivers_with(**{"gemini": False}))
    assert plan.skipped[0].reason == SkipReason.driver_no_resume


def test_skip_when_session_id_missing():
    ws = Workspace(active_handle="a", tabs=[_tab("a", "default", "claude-code", None)])
    plan = plan_resume(ws, _agents_with("default"),
                       _drivers_with(**{"claude-code": True}))
    assert plan.skipped[0].reason == SkipReason.no_session_id


def test_mixed_workspace_partitions_correctly():
    ws = Workspace(active_handle="ok", tabs=[
        _tab("ok", "default", "claude-code", "sid", order=0),
        _tab("ghost", "missing", "claude-code", "sid", order=1),
        _tab("gem", "default", "gemini", "sid", order=2),
    ])
    plan = plan_resume(ws, _agents_with("default"),
                       _drivers_with(**{"claude-code": True, "gemini": False}))
    assert [r.tab.handle for r in plan.resumable] == ["ok"]
    assert {s.tab.handle for s in plan.skipped} == {"ghost", "gem"}
