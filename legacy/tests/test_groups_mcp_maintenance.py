from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_status_includes_members_and_no_current_broadcast():
    from aegis.mcp.server import _aegis_group_status_impl

    class _G:
        async def status(self, group):
            return {"name": group,
                    "members": [{"handle": "ada", "profile": "p"}],
                    "current_broadcast": None}

    class _B:
        groups = _G()

    out = await _aegis_group_status_impl(_B(), group="g")
    assert out["name"] == "g"
    assert out["members"][0]["handle"] == "ada"
    assert out["current_broadcast"] is None


@pytest.mark.asyncio
async def test_dissolve_returns_dissolved_name():
    from aegis.mcp.server import _aegis_group_dissolve_impl

    class _G:
        async def dissolve(self, group):
            return {"dissolved": group}

    class _B:
        groups = _G()

    out = await _aegis_group_dissolve_impl(_B(), group="g")
    assert out == {"dissolved": "g"}


@pytest.mark.asyncio
async def test_rename_returns_old_and_new():
    from aegis.mcp.server import _aegis_group_rename_impl

    class _G:
        async def rename(self, old, new):
            return {"old": old, "new": new}

    class _B:
        groups = _G()

    out = await _aegis_group_rename_impl(_B(), old="a", new="b")
    assert out == {"old": "a", "new": "b"}


@pytest.mark.asyncio
async def test_move_member_returns_handle_from_to():
    from aegis.mcp.server import _aegis_group_move_member_impl

    class _G:
        async def move_member(self, handle, *, from_group, to_group):
            return {"handle": handle, "from": from_group,
                    "to": to_group}

    class _B:
        groups = _G()

    out = await _aegis_group_move_member_impl(
        _B(), handle="ada", from_group="x", to_group="y")
    assert out == {"handle": "ada", "from": "x", "to": "y"}
