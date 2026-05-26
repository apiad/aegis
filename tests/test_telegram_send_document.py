from __future__ import annotations

import httpx
import pytest

from aegis.telegram.bot import BotClient


@pytest.mark.asyncio
async def test_send_document_multipart(tmp_path):
    f = tmp_path / "reply.md"
    f.write_text("# hello\n\nbody")
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["method"] = req.method
        seen["url"] = str(req.url)
        seen["content_type"] = req.headers.get("content-type", "")
        seen["body"] = req.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                             base_url="https://api.telegram.org")
    bot = BotClient(token="t", http=http)
    mid = await bot.send_document(chat_id=1, path=f, caption="see attached", parse_mode="HTML")
    assert mid == 77
    assert seen["method"] == "POST"
    assert "sendDocument" in seen["url"]
    assert "multipart/form-data" in seen["content_type"]
    assert b'name="chat_id"' in seen["body"]
    assert b'name="caption"' in seen["body"]
    assert b'name="parse_mode"' in seen["body"]
    assert b'name="document"' in seen["body"]
    assert b"# hello" in seen["body"]
