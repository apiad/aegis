from __future__ import annotations

import httpx
import pytest

from aegis.telegram.bot import BotClient


def _client(handler):
    transport = httpx.MockTransport(handler)
    return BotClient(
        token="T",
        http=httpx.AsyncClient(transport=transport,
                               base_url="https://api.telegram.org"),
    )


@pytest.mark.asyncio
async def test_send_message_returns_message_id():
    def h(req):
        return httpx.Response(200, json={"ok": True,
                                         "result": {"message_id": 42}})
    b = _client(h)
    assert await b.send_message(1, "hi") == 42


@pytest.mark.asyncio
async def test_get_updates_passes_offset_and_returns_list():
    seen: dict = {}

    def h(req):
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"ok": True,
                                         "result": [{"update_id": 7}]})
    b = _client(h)
    up = await b.get_updates(offset=5, timeout=0)
    assert up == [{"update_id": 7}] and "offset=5" in seen["url"]


@pytest.mark.asyncio
async def test_send_message_html_parse_mode(monkeypatch):
    seen = {}
    async def fake_call(self, method, **params):
        seen["method"] = method
        seen["params"] = params
        return {"message_id": 99}
    monkeypatch.setattr("aegis.telegram.bot.BotClient._call", fake_call)
    bot = BotClient(token="t")
    mid = await bot.send_message(chat_id=1, text="<b>x</b>", parse_mode="HTML")
    assert mid == 99
    assert seen["params"]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_send_message_no_parse_mode_when_none(monkeypatch):
    seen = {}
    async def fake_call(self, method, **params):
        seen["params"] = params
        return {"message_id": 1}
    monkeypatch.setattr("aegis.telegram.bot.BotClient._call", fake_call)
    bot = BotClient(token="t")
    await bot.send_message(chat_id=1, text="plain")
    assert "parse_mode" not in seen["params"]


@pytest.mark.asyncio
async def test_edit_message_html_parse_mode(monkeypatch):
    seen = {}
    async def fake_call(self, method, **params):
        seen["params"] = params
        return {}
    monkeypatch.setattr("aegis.telegram.bot.BotClient._call", fake_call)
    bot = BotClient(token="t")
    await bot.edit_message(chat_id=1, message_id=2, text="<i>x</i>", parse_mode="HTML")
    assert seen["params"]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_retry_after_on_429(monkeypatch):
    calls = {"n": 0}

    async def nosleep(_):
        pass

    monkeypatch.setattr("asyncio.sleep", nosleep)

    def h(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429, json={"ok": False, "parameters": {"retry_after": 1}})
        return httpx.Response(200,
                              json={"ok": True, "result": {"message_id": 1}})
    b = _client(h)
    assert await b.send_message(1, "x") == 1 and calls["n"] == 2
