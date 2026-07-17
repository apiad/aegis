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


@pytest.mark.asyncio
async def test_complete_items_include_source():
    from aegis.commands import REGISTRY, SlashCommand, CommandResult

    async def _n(ctx, args):
        return CommandResult(True, "x")

    REGISTRY["zzuser"] = SlashCommand("zzuser", "s", "/zzuser", _n,
                                      source="user")
    try:
        res = await _session(FakeCore())._complete("/zzuser")
        item = next(i for i in res["items"] if i["label"] == "/zzuser")
        assert item["source"] == "user"
    finally:
        REGISTRY.pop("zzuser", None)
