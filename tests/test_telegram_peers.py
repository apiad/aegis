from __future__ import annotations

import httpx
import pytest

from aegis.remote.config import RemoteSpec
from aegis.telegram.commands import COMMANDS


@pytest.fixture(autouse=True)
def _clean_registry():
    snap = dict(COMMANDS)
    yield
    COMMANDS.clear()
    COMMANDS.update(snap)


def _make_frontend(remotes: dict | None = None):
    from aegis.telegram.frontend import TelegramFrontend

    class _Bot:
        sent: list[str] = []
        async def send_message(self, chat, text, markdown=False):
            self.sent.append(text); return 1
        async def edit_message(self, *a, **k): return None

    class _Mgr:
        def list_sessions(self): return []
        def list_agents(self): return []
        def get(self, handle): return None

    class _Bridge: queue_manager = scheduler = None
    class _Cfg:
        def __init__(self): self.remotes = remotes or {}

    bot = _Bot()
    bot.sent = []
    fe = TelegramFrontend(bot, _Mgr(), _Bridge(), _Cfg(),
                          chat_id=42, auto_prompt="")
    return fe, bot


@pytest.mark.asyncio
async def test_peers_empty():
    fe, bot = _make_frontend(remotes={})
    await fe._command("/peers")
    assert "no peers" in bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_peers_shows_url_and_auth(httpx_mock):
    httpx_mock.add_response(
        method="GET", url="http://1.2.3.4:8556/remote/v1/",
        status_code=404)        # any response = reachable
    fe, bot = _make_frontend(remotes={
        "vps": RemoteSpec(url="http://1.2.3.4:8556", token="secret"),
    })
    await fe._command("/peers")
    out = bot.sent[-1]
    assert "vps" in out
    assert "1.2.3.4" in out
    assert "token" in out.lower()


@pytest.mark.asyncio
async def test_peers_unreachable(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("nope"))
    fe, bot = _make_frontend(remotes={
        "down": RemoteSpec(url="http://5.6.7.8:8556"),
    })
    await fe._command("/peers")
    out = bot.sent[-1]
    assert "unreachable" in out.lower() or "✗" in out
