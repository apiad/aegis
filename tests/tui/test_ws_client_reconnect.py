# tests/tui/test_ws_client_reconnect.py
import asyncio
import json
import socket

import pytest
import websockets

from aegis.tui.ws_client import WsClient


def _pick_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _until(cond, *, interval: float = 0.05):
    """Poll cond() every interval until True."""
    while not cond():
        await asyncio.sleep(interval)


async def test_reconnect_sends_resume_with_tail_and_flips_connection():
    """Kill the server mid-session, restart, confirm client re-auths and
    sends a `resume` frame with tail=10 and last_seq=9 for the tracked handle."""
    port = _pick_free_port()
    seen_resume: list[dict] = []

    async def one_shot_close(ws):
        await ws.recv()  # auth
        await ws.send(json.dumps({"type": "hello", "protocol_version": 2,
                                  "constants": {}, "supported_kinds": []}))
        await ws.recv()  # subscribe
        await ws.send(json.dumps({
            "type": "stream", "kind": "event", "handle": "h", "seq": 9,
            "event_type": "AssistantText",
            "event": {"type": "AssistantText", "text": "x", "message_id": None}}))
        await ws.close()

    resume_received = asyncio.Event()

    async def accept_resume(ws):
        await ws.recv()  # auth
        await ws.send(json.dumps({"type": "hello", "protocol_version": 2,
                                  "constants": {}, "supported_kinds": []}))
        frame = json.loads(await ws.recv())
        seen_resume.append(frame)
        resume_received.set()
        await asyncio.Future()   # hold the connection open

    connected_events: list[bool] = []

    # Server v1: close after one event
    s1 = await websockets.serve(one_shot_close, "127.0.0.1", port)
    c = WsClient(f"ws://127.0.0.1:{port}", "t", default_tail=10)
    c.on_connection(connected_events.append)
    await c.connect()
    await c.subscribe_session("h")
    await asyncio.sleep(0.3)   # let event arrive + server close
    s1.close()
    await s1.wait_closed()
    assert connected_events[-1] is False

    # Server v2: expect resume (use task so we can cancel later)
    async def s2_task():
        async with websockets.serve(accept_resume, "127.0.0.1", port) as s2:
            await asyncio.Future()   # keep server alive

    s2_srv = asyncio.create_task(s2_task())
    await asyncio.wait_for(resume_received.wait(), 5.0)
    assert seen_resume[0]["type"] == "resume"
    assert seen_resume[0]["subscriptions"] == [
        {"handle": "h", "last_seq": 9, "tail": 10}]
    assert connected_events[-1] is True
    await c.close()
    s2_srv.cancel()


async def test_on_connection_fires_true_on_initial_connect():
    """on_connection(True) fires after the first successful connect()."""
    events: list[bool] = []
    port_holder: list[int] = []

    async def server_task():
        async def handler(ws):
            await ws.recv()  # auth
            await ws.send(json.dumps({"type": "hello", "protocol_version": 2,
                                      "constants": {}, "supported_kinds": []}))
            await asyncio.Future()
        async with websockets.serve(handler, "127.0.0.1", 0) as s:
            port_holder.append(s.sockets[0].getsockname()[1])
            await asyncio.Future()

    srv = asyncio.create_task(server_task())
    await _until(lambda: bool(port_holder))
    c = WsClient(f"ws://127.0.0.1:{port_holder[0]}", "t")
    c.on_connection(events.append)
    await c.connect()
    assert events == [True]
    await c.close()
    srv.cancel()


async def test_observer_exception_does_not_break_client():
    """Exceptions raised by on_connection observers are swallowed."""
    events: list[bool] = []
    port_holder: list[int] = []

    async def server_task():
        async def handler(ws):
            await ws.recv()  # auth
            await ws.send(json.dumps({"type": "hello", "protocol_version": 2,
                                      "constants": {}, "supported_kinds": []}))
            await asyncio.Future()
        async with websockets.serve(handler, "127.0.0.1", 0) as s:
            port_holder.append(s.sockets[0].getsockname()[1])
            await asyncio.Future()

    srv = asyncio.create_task(server_task())
    await _until(lambda: bool(port_holder))
    c = WsClient(f"ws://127.0.0.1:{port_holder[0]}", "t")
    c.on_connection(lambda up: (_ for _ in ()).throw(RuntimeError("boom")))
    c.on_connection(events.append)  # second observer still fires
    await c.connect()
    assert events == [True]
    await c.close()
    srv.cancel()


async def test_default_tail_stored():
    """WsClient stores default_tail from kwarg."""
    c = WsClient("ws://localhost:9999", "t", default_tail=42)
    assert c._default_tail == 42


async def test_close_cancels_reconnect_task():
    """After close(), the reconnect loop does not keep running."""
    port_holder: list[int] = []

    async def server_task():
        async def one_shot(ws):
            await ws.recv()
            await ws.send(json.dumps({"type": "hello", "protocol_version": 2,
                                      "constants": {}, "supported_kinds": []}))
            await ws.close()
        async with websockets.serve(one_shot, "127.0.0.1", 0) as s:
            port_holder.append(s.sockets[0].getsockname()[1])
            await asyncio.Future()

    srv = asyncio.create_task(server_task())
    await _until(lambda: bool(port_holder))
    c = WsClient(f"ws://127.0.0.1:{port_holder[0]}", "t")
    await c.connect()
    await asyncio.sleep(0.2)  # let server close and _read_loop detect it
    await c.close()
    # Give reconnect task a moment to be cancelled
    await asyncio.sleep(0.05)
    if c._reconnect_task is not None:
        assert c._reconnect_task.done()
    srv.cancel()
