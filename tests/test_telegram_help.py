from __future__ import annotations

import pytest

from aegis.telegram.commands import COMMANDS, Command, register


@pytest.fixture(autouse=True)
def _clean_registry():
    snap = dict(COMMANDS)
    yield
    COMMANDS.clear()
    COMMANDS.update(snap)


def _make_frontend():
    import tempfile
    from pathlib import Path
    from aegis.telegram.frontend import TelegramFrontend
    state_dir = Path(tempfile.mkdtemp())

    class _Bot:
        sent: list[str] = []
        async def send_message(self, chat, text, markdown=False):
            self.sent.append(text)
            return 1
        async def edit_message(self, *a, **k): return None

    class _Mgr:
        def list_sessions(self): return []
        def list_agents(self): return []

    class _Bridge: queue_manager = scheduler = None
    class _Cfg: remotes: dict = {}

    bot = _Bot()
    fe = TelegramFrontend(bot, _Mgr(), _Bridge(), _Cfg(),
                          chat_id=42, auto_prompt="", state_dir=state_dir)
    return fe, bot


@pytest.mark.asyncio
async def test_help_lists_all_registered_commands():
    fe, bot = _make_frontend()
    await fe._command("/help")
    out = bot.sent[-1]
    # Every registered command should appear in the bare /help listing.
    for cmd_name in COMMANDS:
        # Multi-word names like "queue list" should show up as is.
        assert cmd_name in out or cmd_name.replace(" ", " ") in out


@pytest.mark.asyncio
async def test_help_for_named_command_prints_detail():
    fe, bot = _make_frontend()
    await fe._command("/help new")
    out = bot.sent[-1]
    # The /new command's detail mentions "spawn a new agent".
    assert "spawn a new agent" in out.lower()


@pytest.mark.asyncio
async def test_help_for_unknown_command_errors():
    fe, bot = _make_frontend()
    await fe._command("/help ghost-command")
    out = bot.sent[-1]
    assert "no such command" in out.lower() or "unknown" in out.lower()


@pytest.mark.asyncio
async def test_help_for_resource_filters_by_prefix():
    """`/help queue` lists every command whose name starts with `queue `."""
    # queue list and queue show are registered by the real commands module;
    # no need to re-register them here.
    fe, bot = _make_frontend()
    await fe._command("/help queue")
    out = bot.sent[-1]
    assert "queue list" in out
    assert "queue show" in out


def _noop_handler():
    async def _h(ctx, args):
        await ctx.reply("noop")
    return _h
