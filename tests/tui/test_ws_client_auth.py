# tests/tui/test_ws_client_auth.py
import asyncio
import json
import pytest
import websockets

from aegis.tui.ws_client import WsClient, AuthFailed, RpcError


async def _echo_server_that_expects_token(token: str, port_holder: list):
    """Tiny in-process WS server that mimics the aegis hello handshake."""
    async def handler(ws):
        first = json.loads(await ws.recv())
        if first.get("type") != "auth" or first.get("token") != token:
            await ws.close(4401, "unauthorized")
            return
        await ws.send(json.dumps({
            "type": "hello", "server_version": "0.16.0",
            "protocol_version": 2,
            "constants": {"REPLAY_TAIL": 10, "RESUME_GAP_CAP": 1000},
            "supported_kinds": ["event", "state"],
        }))
        # Then honour a single rpc echo
        req = json.loads(await ws.recv())
        if req["method"] == "boom":
            await ws.send(json.dumps({
                "type": "rpc_response", "id": req["id"], "ok": False,
                "error": "kaboom"}))
            return
        await ws.send(json.dumps({
            "type": "rpc_response", "id": req["id"], "ok": True,
            "result": {"echo": req["params"]}}))
    async with websockets.serve(handler, "127.0.0.1", 0) as s:
        port_holder.append(s.sockets[0].getsockname()[1])
        await asyncio.Future()   # run forever


@pytest.mark.asyncio
async def test_auth_success_returns_hello_with_constants():
    port_holder: list = []
    server = asyncio.create_task(_echo_server_that_expects_token("goodtoken", port_holder))
    await asyncio.sleep(0.05)
    try:
        client = WsClient(f"ws://127.0.0.1:{port_holder[0]}", "goodtoken")
        hello = await client.connect()
        assert hello["protocol_version"] == 2
        assert client.constants["REPLAY_TAIL"] == 10
        result = await client.rpc("list_agents", {})
        assert result == {"echo": {}}
        await client.close()
    finally:
        server.cancel()


@pytest.mark.asyncio
async def test_auth_failure_raises():
    port_holder: list = []
    server = asyncio.create_task(_echo_server_that_expects_token("right", port_holder))
    await asyncio.sleep(0.05)
    try:
        client = WsClient(f"ws://127.0.0.1:{port_holder[0]}", "wrong")
        with pytest.raises(AuthFailed):
            await client.connect()
    finally:
        server.cancel()


@pytest.mark.asyncio
async def test_rpc_error_propagates():
    port_holder: list = []
    server = asyncio.create_task(_echo_server_that_expects_token("t", port_holder))
    await asyncio.sleep(0.05)
    try:
        client = WsClient(f"ws://127.0.0.1:{port_holder[0]}", "t")
        await client.connect()
        with pytest.raises(RpcError, match="kaboom"):
            await client.rpc("boom", {})
        await client.close()
    finally:
        server.cancel()
