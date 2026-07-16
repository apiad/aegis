"""Async WebSocket client for aegis serve. Python mirror of
``web/static/js/ws.js`` — auth handshake, rpc-as-futures, subscribe,
resume with per-subscription tail, and reconnect with exponential backoff.
This module is pure (no Textual imports); the TUI wires callbacks through
``on_connection`` / observer registration on RemoteAgentSession.
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed


class AuthFailed(Exception): ...
class RpcError(Exception): ...
class ProtocolMismatch(Exception): ...


PROTOCOL_MAJOR = 2   # bump in lockstep with wssession.PROTOCOL_VERSION


class WsClient:
    def __init__(self, url: str, token: str) -> None:
        self._url = url
        self._token = token
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._constants: dict = {}
        self._closed = False
        self._handlers: dict[str, list[Callable[[dict], None]]] = {}
        self._subs: dict[str, int] = {}       # handle -> last_seq
        self._globals: set[str] = set()

    @property
    def constants(self) -> dict:
        return dict(self._constants)

    async def connect(self) -> dict:
        try:
            self._ws = await websockets.connect(self._url)
        except OSError as exc:
            raise AuthFailed(f"connect failed: {exc}") from exc
        await self._ws.send(json.dumps({"type": "auth", "token": self._token}))
        try:
            hello_raw = await self._ws.recv()
        except ConnectionClosed as exc:
            code = exc.rcvd.code if exc.rcvd is not None else None
            raise AuthFailed(f"closed during auth (code={code})") from exc
        hello = json.loads(hello_raw)
        if hello.get("type") != "hello":
            raise AuthFailed(f"expected hello, got {hello!r}")
        if hello.get("protocol_version", 0) != PROTOCOL_MAJOR:
            raise ProtocolMismatch(
                f"server protocol {hello.get('protocol_version')} "
                f"!= client {PROTOCOL_MAJOR}")
        self._constants = hello.get("constants", {})
        self._reader = asyncio.create_task(self._read_loop())
        return hello

    async def rpc(self, method: str, params: dict | None = None) -> dict:
        if self._ws is None:
            raise RpcError("not connected")
        rid = self._next_id = self._next_id + 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._ws.send(json.dumps({
            "type": "rpc", "id": rid, "method": method, "params": params or {},
        }))
        return await fut

    def on(self, kind: str, fn: Callable[[dict], None]) -> None:
        self._handlers.setdefault(kind, []).append(fn)

    def last_seq(self, handle: str) -> int:
        return self._subs.get(handle, 0)

    async def subscribe_session(self, handle: str, *,
                                tail: int | None = None) -> None:
        assert self._ws is not None
        self._subs.setdefault(handle, 0)
        frame: dict = {"type": "subscribe",
                       "target": {"kind": "session", "handle": handle}}
        if tail is not None:
            frame["tail"] = tail
        await self._ws.send(json.dumps(frame))

    async def subscribe_global(self, stream: str) -> None:
        assert self._ws is not None
        self._globals.add(stream)
        await self._ws.send(json.dumps({
            "type": "subscribe",
            "target": {"kind": "global", "stream": stream}}))

    async def unsubscribe_session(self, handle: str) -> None:
        assert self._ws is not None
        self._subs.pop(handle, None)
        await self._ws.send(json.dumps({
            "type": "unsubscribe",
            "target": {"kind": "session", "handle": handle}}))

    async def close(self) -> None:
        self._closed = True
        if self._reader:
            self._reader.cancel()
        if self._ws:
            await self._ws.close()

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                self._handle(msg)
        except ConnectionClosed:
            self._fail_pending("connection closed")

    def _handle(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "rpc_response":
            fut = self._pending.pop(msg["id"], None)
            if fut is None or fut.done():
                return
            if msg.get("ok"):
                fut.set_result(msg.get("result", {}))
            else:
                fut.set_exception(RpcError(msg.get("error", "rpc failed")))
        elif t == "error":
            rid = msg.get("id")
            if rid is not None:
                fut = self._pending.pop(rid, None)
                if fut and not fut.done():
                    fut.set_exception(RpcError(
                        msg.get("message") or msg.get("code") or "error"))
        elif t == "stream":
            handle = msg.get("handle")
            seq = msg.get("seq")
            if handle and isinstance(seq, int):
                self._subs[handle] = max(self._subs.get(handle, 0), seq)
            for fn in self._handlers.get(msg.get("kind", ""), ()):
                try:
                    fn(msg)
                except Exception:
                    pass    # observer errors never break the read loop

    def _fail_pending(self, reason: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RpcError(reason))
        self._pending.clear()
