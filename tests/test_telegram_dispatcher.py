from __future__ import annotations

import asyncio

import pytest

from aegis.telegram.commands import (
    COMMANDS, Command, CmdContext, register,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Don't leak test commands between tests."""
    snap = dict(COMMANDS)
    yield
    COMMANDS.clear()
    COMMANDS.update(snap)


def _make_frontend(commands_to_register=None):
    """Build a TelegramFrontend with stub bot/manager/bridge/cfg."""
    from aegis.telegram.frontend import TelegramFrontend

    class _Bot:
        sent: list[str] = []
        async def send_message(self, chat, text, markdown=False):
            self.sent.append(text)
            return 1
        async def edit_message(self, *a, **k): return None

    class _Mgr:
        def list_sessions(self): return []
        def list_agents(self): return ["default"]
        def get(self, handle): return None
        async def close(self, handle): return None
        async def interrupt(self, handle): return None
        def _sync_spawn(self, slug): raise KeyError(slug)

    class _Bridge:
        queue_manager = None
        scheduler = None

    class _Cfg:
        remotes: dict = {}

    for cmd in (commands_to_register or []):
        register(cmd)

    bot = _Bot()
    fe = TelegramFrontend(bot, _Mgr(), _Bridge(), _Cfg(),
                          chat_id=42, auto_prompt="")
    return fe, bot


@pytest.mark.asyncio
async def test_dispatcher_routes_registered_verb():
    """A registered command is dispatched via the registry."""
    called: list[tuple] = []

    async def _h(ctx, args):
        called.append((args, ctx.target))
        await ctx.reply("ok")

    cmd = Command(name="ping", summary="x", detail="x", handler=_h)
    fe, bot = _make_frontend([cmd])
    await fe._command("/ping foo bar")
    assert called == [(["foo", "bar"], None)]
    assert "ok" in bot.sent[-1]


@pytest.mark.asyncio
async def test_dispatcher_parses_at_peer():
    """An @<peer> token is pulled out of args and exposed as ctx.target."""
    called: list[tuple] = []

    async def _h(ctx, args):
        called.append((args, ctx.target))
        await ctx.reply("ok")

    cmd = Command(name="ping", summary="x", detail="x", handler=_h)
    fe, bot = _make_frontend([cmd])
    await fe._command("/ping foo @vps bar")
    assert called == [(["foo", "bar"], "vps")]


@pytest.mark.asyncio
async def test_dispatcher_only_first_at_token_taken():
    called: list[tuple] = []
    async def _h(ctx, args):
        called.append((args, ctx.target))

    cmd = Command(name="ping", summary="x", detail="x", handler=_h)
    fe, bot = _make_frontend([cmd])
    await fe._command("/ping @vps @desktop")
    assert called == [([], "vps")]
    # "@desktop" is dropped — first wins.


@pytest.mark.asyncio
async def test_dispatcher_longest_prefix_match():
    """`/test sub` resolves before `/test` when both registered."""
    async def _sub(ctx, args): await ctx.reply("SUB")
    async def _bare(ctx, args): await ctx.reply("BARE")

    fe, bot = _make_frontend([
        Command(name="test sub", summary="x", detail="x", handler=_sub),
        Command(name="test",     summary="x", detail="x", handler=_bare),
    ])
    await fe._command("/test sub")
    assert "SUB" in bot.sent[-1]
    await fe._command("/test")
    assert "BARE" in bot.sent[-1]


@pytest.mark.asyncio
async def test_dispatcher_unknown_verb_falls_through_to_legacy_alias():
    """Unknown verb tries /<handle> alias-routing."""
    fe, bot = _make_frontend()
    await fe._command("/no-such-command")
    # The fallback emits 'no session ...' since there's no such handle.
    assert "no session" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_dispatcher_empty_at_is_args_only():
    """Bare '@' is not a target — stays in args (handler can validate)."""
    called: list[tuple] = []
    async def _h(ctx, args):
        called.append((args, ctx.target))

    cmd = Command(name="ping", summary="x", detail="x", handler=_h)
    fe, bot = _make_frontend([cmd])
    await fe._command("/ping @")
    assert called == [(["@"], None)]
