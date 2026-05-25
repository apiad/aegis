from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from aegis.mcp.bridge import SessionInfo
from aegis.mcp.server import build_server
from aegis.remote.config import RemotePlaneSpec, RemoteSpec


async def _call(server, name, **kwargs):
    tools = await server.list_tools()
    tool = next(t for t in tools if t.name == name)
    result = await tool.run(kwargs)
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]
    if sc is not None:
        return sc
    return result.content[0].text


@dataclass
class _FakeQM:
    enqueue_calls: list[Any] = field(default_factory=list)

    def enqueue(self, queue, payload, *, enqueued_by, callback,
                callback_to=None, callback_handle=None):
        self.enqueue_calls.append((queue, payload, enqueued_by, callback,
                                    callback_to, callback_handle))
        return ("local-tid", 0)


class _FakeBridge:
    def __init__(self, qm, remotes, remote_plane=None):
        from aegis.queue import InboxRouter
        self.queue_manager = qm
        self.remotes = remotes
        self.remote_plane = remote_plane
        self.inbox_router = InboxRouter()
        self.canvas_manager = None
        self.terminal_manager = None
        self.groups = None

    def list_sessions(self) -> list[SessionInfo]:
        return []
    def list_agents(self) -> list[str]:
        return []
    async def handoff(self, *a, **k) -> str: return ""
    async def spawn(self, *a, **k) -> str: return ""
    async def close(self, *a, **k) -> None: return None


@pytest.mark.asyncio
async def test_aegis_enqueue_remote_callback_passes_hints(monkeypatch):
    """When callback=true and remote_plane is configured, callback_to +
    callback_handle land in the outbound enqueue."""
    bridge = _FakeBridge(
        qm=_FakeQM(),
        remotes={"vps": RemoteSpec(url="http://1.2.3.4:8556",
                                    peer_name="laptop")},
        remote_plane=RemotePlaneSpec(bind="127.0.0.1:8556",
                                      peer_name="zion"))
    captured = {}
    async def fake_remote_enqueue(spec, queue, payload, from_,
                                   *, callback_to=None, callback_handle=None):
        captured.update(spec=spec, queue=queue, payload=payload, from_=from_,
                        callback_to=callback_to,
                        callback_handle=callback_handle)
        return {"task_id": "01J", "queued_position": 0}
    monkeypatch.setattr("aegis.mcp.server.remote_enqueue",
                        fake_remote_enqueue, raising=False)
    # The current aegis_enqueue imports remote_enqueue locally — so we
    # also need to patch the source:
    monkeypatch.setattr("aegis.remote.client.remote_enqueue",
                        fake_remote_enqueue)

    server = build_server(bridge)
    result = await _call(server, "aegis_enqueue",
                         queue="impl", payload="do it",
                         from_handle="lucid-knuth",
                         callback=True, target="vps")
    assert captured["callback_to"] == "laptop"
    assert captured["callback_handle"] == "lucid-knuth"
    assert result["target"] == "vps"
    assert "callback will deliver" in result["callback_note"]


@pytest.mark.asyncio
async def test_aegis_enqueue_callback_true_no_remote_plane_errors():
    """callback=true on a remote target requires this serve to have a
    remote_plane configured."""
    bridge = _FakeBridge(
        qm=_FakeQM(),
        remotes={"vps": RemoteSpec(url="http://1.2.3.4:8556")},
        remote_plane=None)
    server = build_server(bridge)
    result = await _call(server, "aegis_enqueue",
                         queue="impl", payload="x",
                         from_handle="h", callback=True, target="vps")
    assert "error" in result
    assert "remote_plane" in result["error"]


@pytest.mark.asyncio
async def test_aegis_enqueue_remote_default_is_fire_and_forget(monkeypatch):
    """v0.8.1: when callback is unspecified for a remote target, the
    default is fire-and-forget — no callback hints on the wire."""
    bridge = _FakeBridge(
        qm=_FakeQM(),
        remotes={"vps": RemoteSpec(url="http://1.2.3.4:8556",
                                    peer_name="laptop")},
        remote_plane=RemotePlaneSpec(bind="127.0.0.1:8556",
                                      peer_name="zion"))
    captured = {}
    async def fake_remote_enqueue(spec, queue, payload, from_,
                                   *, callback_to=None, callback_handle=None):
        captured.update(callback_to=callback_to,
                        callback_handle=callback_handle)
        return {"task_id": "01J", "queued_position": 0}
    monkeypatch.setattr("aegis.remote.client.remote_enqueue",
                        fake_remote_enqueue)
    server = build_server(bridge)
    result = await _call(server, "aegis_enqueue",
                         queue="impl", payload="x",
                         from_handle="h", target="vps")  # no callback kwarg
    assert captured.get("callback_to") is None
    assert captured.get("callback_handle") is None
    assert "fire-and-forget" in result["callback_note"]


@pytest.mark.asyncio
async def test_aegis_enqueue_callback_true_remote_missing_remote_plane_peer_name():
    """v0.8.1: callback=True + remote_plane has no peer_name → loud error."""
    bridge = _FakeBridge(
        qm=_FakeQM(),
        remotes={"vps": RemoteSpec(url="http://1.2.3.4:8556",
                                    peer_name="laptop")},
        remote_plane=RemotePlaneSpec(bind="127.0.0.1:8556"))  # no peer_name
    server = build_server(bridge)
    result = await _call(server, "aegis_enqueue",
                         queue="impl", payload="x",
                         from_handle="h", callback=True, target="vps")
    assert "error" in result
    assert "remote_plane.peer_name" in result["error"]


@pytest.mark.asyncio
async def test_aegis_enqueue_callback_true_remote_missing_target_peer_name():
    """v0.8.1: callback=True + remotes[target].peer_name unset → loud error."""
    bridge = _FakeBridge(
        qm=_FakeQM(),
        remotes={"vps": RemoteSpec(url="http://1.2.3.4:8556")},  # no peer_name
        remote_plane=RemotePlaneSpec(bind="127.0.0.1:8556",
                                      peer_name="zion"))
    server = build_server(bridge)
    result = await _call(server, "aegis_enqueue",
                         queue="impl", payload="x",
                         from_handle="h", callback=True, target="vps")
    assert "error" in result
    assert "peer_name" in result["error"]
    assert "vps" in result["error"]


@pytest.mark.asyncio
async def test_aegis_enqueue_callback_false_remote_omits_hints(monkeypatch):
    """callback=false on a remote target sends no callback hints (v0.7
    behavior preserved)."""
    bridge = _FakeBridge(
        qm=_FakeQM(),
        remotes={"vps": RemoteSpec(url="http://1.2.3.4:8556",
                                    peer_name="laptop")},
        remote_plane=RemotePlaneSpec(bind="127.0.0.1:8556",
                                      peer_name="zion"))
    captured = {}
    async def fake_remote_enqueue(spec, queue, payload, from_,
                                   *, callback_to=None, callback_handle=None):
        captured.update(callback_to=callback_to,
                        callback_handle=callback_handle)
        return {"task_id": "01J", "queued_position": 0}
    monkeypatch.setattr("aegis.remote.client.remote_enqueue",
                        fake_remote_enqueue)
    server = build_server(bridge)
    result = await _call(server, "aegis_enqueue",
                         queue="impl", payload="x",
                         from_handle="h", callback=False, target="vps")
    assert captured.get("callback_to") is None
    assert captured.get("callback_handle") is None
    assert "fire-and-forget" in result["callback_note"]
