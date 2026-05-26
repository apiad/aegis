from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest

from aegis.telegram.commands import (
    Command, CmdContext, COMMANDS, register,
)


async def _noop(ctx, args): return


def test_register_adds_to_registry():
    cmd = Command(name="test_one", summary="first test command",
                  detail="more detail", handler=_noop)
    try:
        register(cmd)
        assert COMMANDS["test_one"] is cmd
    finally:
        COMMANDS.pop("test_one", None)


def test_register_rejects_duplicate():
    cmd1 = Command(name="test_dup", summary="x", detail="x", handler=_noop)
    cmd2 = Command(name="test_dup", summary="y", detail="y", handler=_noop)
    try:
        register(cmd1)
        with pytest.raises(ValueError, match="duplicate"):
            register(cmd2)
    finally:
        COMMANDS.pop("test_dup", None)


def test_cmdcontext_carries_required_fields():
    replies: list[str] = []
    async def reply(text: str) -> None: replies.append(text)
    ctx = CmdContext(bridge=object(), cfg=object(), manager=object(),
                      target=None, reply=reply, frontend=object())
    assert ctx.target is None
    assert ctx.frontend is not None
    asyncio.run(ctx.reply("hello"))
    assert replies == ["hello"]


def test_cmdcontext_with_target():
    async def reply(text: str) -> None: return
    ctx = CmdContext(bridge=object(), cfg=object(), manager=object(),
                      target="vps", reply=reply, frontend=object())
    assert ctx.target == "vps"
