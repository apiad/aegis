from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aegis.config import WebConfig
from aegis.web.server import build_web_app


class FakeManager:
    def __init__(self) -> None:
        self.spawned: list = []

    def list_agents(self):
        return ["claude", "gemini"]

    def list_sessions(self):
        return []

    def get(self, handle):
        return None

    async def spawn(self, profile):
        self.spawned.append(profile)
        return "agent-1"

    async def close(self, handle):
        pass

    async def interrupt(self, handle):
        pass


def _app(tmp_path: Path):
    return build_web_app(FakeManager(), WebConfig(token="secret"),
                         tmp_path / "state")


def test_healthz(tmp_path: Path):
    client = TestClient(_app(tmp_path))
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_ws_auth_then_hello_then_rpc(tmp_path: Path):
    client = TestClient(_app(tmp_path))
    with client.websocket_connect("/ws?t=secret") as ws:
        ws.send_json({"type": "auth", "token": "secret"})
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["protocol_version"] == 1
        assert "RESUME_GAP_CAP" in hello["constants"]

        ws.send_json({"type": "rpc", "id": 1, "method": "list_agents"})
        resp = ws.receive_json()
        assert resp["type"] == "rpc_response"
        assert resp["id"] == 1 and resp["ok"] is True
        assert resp["result"]["agents"] == ["claude", "gemini"]


def test_ws_spawn_session_over_real_transport(tmp_path: Path):
    client = TestClient(_app(tmp_path))
    with client.websocket_connect("/ws?t=secret") as ws:
        ws.send_json({"type": "auth", "token": "secret"})
        ws.receive_json()  # hello
        ws.send_json({"type": "rpc", "id": 2, "method": "spawn_session",
                      "params": {"agent_profile": "claude"}})
        resp = ws.receive_json()
        assert resp["ok"] is True
        assert resp["result"]["handle"] == "agent-1"


def test_ws_bad_token_closes(tmp_path: Path):
    client = TestClient(_app(tmp_path))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?t=secret") as ws:
            ws.send_json({"type": "auth", "token": "WRONG"})
            ws.receive_json()  # server closes 4401 → disconnect
