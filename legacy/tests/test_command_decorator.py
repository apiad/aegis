import asyncio

import pytest

from aegis.commands import REGISTRY, CommandResult, CommandCollision, command
from aegis.commands.args import Arg, ArgSpec


def _clear(*names):
    for n in names:
        REGISTRY.pop(n, None)


def test_bare_decorator_defaults():
    @command
    async def ping(ctx, args):
        "ping a thing"
        return CommandResult(True, "pong")
    try:
        c = REGISTRY["ping"]
        assert c.source == "plugin"
        assert c.summary == "ping a thing"
        assert c.usage == "/ping"
        res = asyncio.run(c.run(None, None))
        assert res.title == "pong"
    finally:
        _clear("ping")


def test_kwargs_form():
    @command(name="pp", summary="s", usage="/pp <x>",
             spec=ArgSpec(positionals=(Arg("x"),)))
    async def _h(ctx, args):
        return CommandResult(True, args["x"])
    try:
        c = REGISTRY["pp"]
        assert c.usage == "/pp <x>"
        assert c.spec.positionals[0].name == "x"
    finally:
        _clear("pp")


def test_usage_auto_derived_from_spec():
    @command(name="qq", spec=ArgSpec(positionals=(
        Arg("a"), Arg("b", required=False))))
    async def _h(ctx, args):
        return CommandResult(True, "ok")
    try:
        assert REGISTRY["qq"].usage == "/qq <a> [b]"
    finally:
        _clear("qq")


def test_collision_with_builtin_raises():
    with pytest.raises(CommandCollision):
        @command(name="help")
        async def _h(ctx, args):
            return CommandResult(True, "x")


def test_non_coroutine_rejected():
    with pytest.raises(TypeError):
        @command
        def _sync(ctx, args):        # not async
            return None


def test_wrong_signature_rejected():
    with pytest.raises(TypeError):
        @command
        async def _bad(only_one):    # must be (ctx, args)
            return None
