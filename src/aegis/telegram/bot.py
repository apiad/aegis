from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

log = logging.getLogger("aegis.telegram")


class BotClient:
    def __init__(self, token: str,
                 http: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._http = http or httpx.AsyncClient(
            base_url="https://api.telegram.org",
            timeout=httpx.Timeout(65.0))

    def _url(self, method: str) -> str:
        return f"/bot{self._token}/{method}"

    async def _call(self, method: str, **params):
        for attempt in range(5):
            try:
                r = await self._http.get(self._url(method), params=params)
            except httpx.HTTPError as e:
                wait = min(2 ** attempt, 30)
                log.warning("telegram %s network error: %s (retry %ss)",
                            method, e, wait)
                await asyncio.sleep(wait)
                continue
            if r.status_code == 429:
                ra = r.json().get("parameters", {}).get("retry_after", 1)
                await asyncio.sleep(ra)
                continue
            data = r.json()
            if not data.get("ok"):
                log.warning("telegram %s !ok: %s", method, data)
                return None
            return data["result"]
        log.error("telegram %s gave up after retries", method)
        return None

    async def get_updates(self, offset: int,
                          timeout: int = 50) -> list[dict]:
        res = await self._call("getUpdates", offset=offset, timeout=timeout)
        return res or []

    async def send_message(self, chat_id: int, text: str,
                           *, parse_mode: str | None = None) -> int | None:
        params: dict = {"chat_id": chat_id, "text": text}
        if parse_mode is not None:
            params["parse_mode"] = parse_mode
        res = await self._call("sendMessage", **params)
        return res["message_id"] if res else None

    async def edit_message(self, chat_id: int, message_id: int, text: str,
                           *, parse_mode: str | None = None) -> None:
        params: dict = {"chat_id": chat_id,
                        "message_id": message_id,
                        "text": text}
        if parse_mode is not None:
            params["parse_mode"] = parse_mode
        await self._call("editMessageText", **params)

    async def send_document(self, chat_id: int, path: Path, *,
                            caption: str | None = None,
                            parse_mode: str | None = None) -> int | None:
        url = self._url("sendDocument")
        data: dict[str, str] = {"chat_id": str(chat_id)}
        if caption is not None:
            data["caption"] = caption
        if parse_mode is not None:
            data["parse_mode"] = parse_mode
        for attempt in range(5):
            try:
                with path.open("rb") as fp:
                    files = {"document": (path.name, fp, "text/markdown")}
                    r = await self._http.post(url, data=data, files=files)
            except httpx.HTTPError as e:
                wait = min(2 ** attempt, 30)
                log.warning("telegram sendDocument network error: %s (retry %ss)", e, wait)
                await asyncio.sleep(wait)
                continue
            if r.status_code == 429:
                ra = r.json().get("parameters", {}).get("retry_after", 1)
                await asyncio.sleep(ra)
                continue
            body = r.json()
            if not body.get("ok"):
                log.warning("telegram sendDocument !ok: %s", body)
                return None
            return body["result"]["message_id"]
        log.error("telegram sendDocument gave up after retries")
        return None

    async def aclose(self) -> None:
        await self._http.aclose()
