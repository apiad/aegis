from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_aegis_group_spawn_calls_bridge_spawn():
    from aegis.mcp.server import _aegis_group_spawn_impl

    class _Calls:
        def __init__(self): self.args = None
        async def spawn(self, *, profile, group, handle=None):
            self.args = (profile, group, handle); return "ada"

    class _Bridge:
        def __init__(self): self.groups = _Calls()

    b = _Bridge()
    out = await _aegis_group_spawn_impl(b, profile="opus", group="rev")
    assert out == {"handle": "ada", "group": "rev"}
    assert b.groups.args == ("opus", "rev", None)
