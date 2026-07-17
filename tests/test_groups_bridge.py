"""The _GroupsBridge.list_groups read method backing the /groups command."""
from __future__ import annotations

import pytest

from aegis.groups.bridge import make_groups_bridge


class _FakeSM:
    def live_handles(self):
        return set()


@pytest.mark.asyncio
async def test_list_groups_returns_name_and_member_count():
    b = make_groups_bridge(session_manager=_FakeSM(), inbox_router=None)
    b.runtime.registry.create("g1")
    from aegis.groups.models import MemberRef
    b.runtime.registry.add_member("g1", MemberRef(handle="a", profile="opus"))
    b.runtime.registry.add_member("g1", MemberRef(handle="b", profile="opus"))
    rows = b.list_groups()
    assert {"name": "g1", "members": 2} in rows


@pytest.mark.asyncio
async def test_list_groups_empty():
    b = make_groups_bridge(session_manager=_FakeSM(), inbox_router=None)
    assert b.list_groups() == []
