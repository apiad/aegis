# tests/tui/test_ws_client_streams.py
import asyncio
import json
import pytest
import websockets

from aegis.tui.ws_client import WsClient


async def _stream_server(port_holder: list):
    """In-process WS server that mimics aegis hello handshake then fires stream frames."""
    async def handler(ws):
        first = json.loads(await ws.recv())
        assert first["type"] == "auth"
        await ws.send(json.dumps({
            "type": "hello", "protocol_version": 2,
            "constants": {}, "supported_kinds": ["event"]}))
        sub = json.loads(await ws.recv())
        assert sub == {"type": "subscribe", "tail": 5,
                       "target": {"kind": "session", "handle": "swift-bohr"}}
        # Fire 3 event frames + history_complete
        for i in (7, 8, 9):
            await ws.send(json.dumps({
                "type": "stream", "kind": "event",
                "handle": "swift-bohr", "seq": i,
                "event_type": "AssistantText",
                "event": {"type": "AssistantText", "text": f"m{i}",
                          "message_id": None}}))
        await ws.send(json.dumps({
            "type": "stream", "kind": "history_complete",
            "handle": "swift-bohr", "current_seq": 9}))
        await asyncio.Future()

    async with websockets.serve(handler, "127.0.0.1", 0) as s:
        port_holder.append(s.sockets[0].getsockname()[1])
        await asyncio.Future()


@pytest.mark.asyncio
async def test_subscribe_dispatches_stream_frames_and_tracks_last_seq():
    port_holder: list = []
    server = asyncio.create_task(_stream_server(port_holder))
    await asyncio.sleep(0.05)
    try:
        c = WsClient(f"ws://127.0.0.1:{port_holder[0]}", "t")
        await c.connect()
        seen: list[dict] = []
        c.on("event", seen.append)
        done = asyncio.Event()
        c.on("history_complete", lambda fr: done.set())
        await c.subscribe_session("swift-bohr", tail=5)
        await asyncio.wait_for(done.wait(), 1.0)
        assert [fr["seq"] for fr in seen] == [7, 8, 9]
        assert c.last_seq("swift-bohr") == 9
        await c.close()
    finally:
        server.cancel()
