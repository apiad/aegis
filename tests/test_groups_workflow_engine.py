from __future__ import annotations

import pytest


def _engine(*, runtime=None, wiring=None):
    from aegis.workflow.engine import WorkflowEngine
    return WorkflowEngine(name="t", workflow_id="w",
                          groups_runtime=runtime, groups_wiring=wiring,
                          session_manager=None)


@pytest.mark.asyncio
async def test_engine_spawn_group_delegates_to_wiring():
    class _W:
        async def spawn_group(self, name, profiles):
            return [f"{p}-h" for p in profiles]

    e = _engine(wiring=_W(), runtime=object())
    handles = await e.spawn_group("rev", ["sec", "style"])
    assert handles == ["sec-h", "style-h"]


@pytest.mark.asyncio
async def test_engine_broadcast_and_wait_all_delegate_to_runtime():
    from aegis.groups.models import GroupResult

    class _R:
        def __init__(self):
            self.broadcast_args = None
            self.wait_all_args = None

        async def broadcast(self, group, **kw):
            self.broadcast_args = (group, kw)
            return "br-1"

        async def wait_all(self, group, **kw):
            self.wait_all_args = (group, kw)
            return GroupResult("br-1", {}, "", {}, [])

    r = _R()
    e = _engine(runtime=r, wiring=object())
    bid = await e.broadcast("rev", objective="o", output_format="f",
                            tool_guidance="t", boundaries="b")
    assert bid == "br-1"
    grp, kw = r.broadcast_args
    assert grp == "rev"
    assert kw["sender"] == "workflow"
    assert kw["objective"] == "o"
    assert kw["output_format"] == "f"
    assert kw["tool_guidance"] == "t"
    assert kw["boundaries"] == "b"

    res = await e.wait_all("rev", timeout=10.0, reducer="join_by_handle")
    assert res.broadcast_id == "br-1"
    grp, kw = r.wait_all_args
    assert grp == "rev"
    assert kw["timeout"] == 10.0
    assert kw["reducer"] == "join_by_handle"


@pytest.mark.asyncio
async def test_engine_wait_any_delegates_to_runtime():
    from aegis.groups.models import GroupResult

    class _R:
        def __init__(self):
            self.wait_any_args = None

        async def wait_any(self, group, **kw):
            self.wait_any_args = (group, kw)
            return GroupResult("br-9", {}, "", {}, [])

    r = _R()
    e = _engine(runtime=r, wiring=object())
    res = await e.wait_any("rev", timeout=5.0, cancel_losers=False)
    assert res.broadcast_id == "br-9"
    grp, kw = r.wait_any_args
    assert grp == "rev"
    assert kw["timeout"] == 5.0
    assert kw["cancel_losers"] is False


@pytest.mark.asyncio
async def test_engine_dissolve_rename_move_delegate_to_registry():
    class _Reg:
        def __init__(self):
            self.calls = []

        def dissolve(self, name):
            self.calls.append(("dissolve", name))

        def rename(self, old, new):
            self.calls.append(("rename", old, new))

        def move_member(self, handle, *, from_group, to_group):
            self.calls.append(("move", handle, from_group, to_group))

    class _R:
        def __init__(self):
            self.registry = _Reg()

    r = _R()
    e = _engine(runtime=r, wiring=object())
    await e.dissolve_group("rev")
    await e.rename_group("rev", "review")
    await e.move_member("ada", from_group="review", to_group="audit")
    assert r.registry.calls == [
        ("dissolve", "rev"),
        ("rename", "rev", "review"),
        ("move", "ada", "review", "audit"),
    ]
