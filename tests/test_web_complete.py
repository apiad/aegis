"""Web parity: the `complete` RPC returns palette completions for a slash
input and empties for a plain message."""
from __future__ import annotations

import pytest

from tests.test_web_slash import FakeCore, _session


@pytest.mark.asyncio
async def test_complete_rpc_returns_items_for_slash():
    session = _session(FakeCore())
    res = await session._complete("/sess")
    assert any(it["label"] == "/sessions" for it in res["items"])


@pytest.mark.asyncio
async def test_complete_rpc_empty_for_plain():
    session = _session(FakeCore())
    res = await session._complete("hello")
    assert res["items"] == []
