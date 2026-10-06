from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_broadcast_passes_four_fields_and_returns_id():
    from aegis.mcp.server import _aegis_group_broadcast_impl

    class _G:
        def __init__(self): self.kw = None
        async def broadcast(self, group, *, sender, objective,
                             output_format, tool_guidance, boundaries):
            self.kw = dict(group=group, sender=sender, objective=objective,
                           output_format=output_format,
                           tool_guidance=tool_guidance, boundaries=boundaries)
            return "br-1"

    class _B:
        def __init__(self): self.groups = _G()

    b = _B()
    out = await _aegis_group_broadcast_impl(
        b, group="rev", sender="agent:host",
        objective="audit", output_format="md",
        tool_guidance="read-only", boundaries="20 reads",
    )
    assert out == {"broadcast_id": "br-1"}
    assert b.groups.kw["objective"] == "audit"
    assert b.groups.kw["boundaries"] == "20 reads"


@pytest.mark.asyncio
async def test_broadcast_rejects_missing_four_field_field():
    from aegis.mcp.server import _aegis_group_broadcast_impl

    class _B:
        groups = None

    with pytest.raises(TypeError):
        await _aegis_group_broadcast_impl(
            _B(), group="rev", sender="x",
            objective="o", output_format="f", boundaries="b",
        )
