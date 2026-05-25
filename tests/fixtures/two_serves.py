"""Two-serve hermetic fixture for callback + schedule round-trip tests."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
from httpx import ASGITransport

from aegis.queue import InboxRouter, QueueManager
from aegis.queue.schema import Queue
from aegis.remote.callback_observer import install_callback_observer
from aegis.remote.config import RemotePlaneSpec, RemoteSpec
from aegis.remote.plane import build_plane

# Re-use the StubSessionManager + _q from test_queue_manager.
from tests.test_queue_manager import StubSessionManager, _q


@dataclass
class _Bridge:
    queue_manager: Any
    inbox_router: Any
    remotes: dict
    remote_plane: Any = None
    canvas_manager: Any = None
    terminal_manager: Any = None
    groups: Any = None

    # AppBridge surface minimal stubs (only needed if you call
    # build_server(bridge) for the MCP path — for direct tool invocation
    # via build_server, you'll need list_sessions etc.).
    def list_sessions(self): return []
    def list_agents(self): return []
    async def handoff(self, *a, **k): return ""
    async def spawn(self, *a, **k): return ""
    async def close(self, *a, **k): return None


class Pair:
    """Holds both sides + provides high-level helpers used by tests."""
    def __init__(self, qm_a, inbox_a, bridge_a, app_a,
                 qm_b, inbox_b, bridge_b, app_b):
        self.qm_a, self.inbox_a, self.bridge_a, self.app_a = \
            qm_a, inbox_a, bridge_a, app_a
        self.qm_b, self.inbox_b, self.bridge_b, self.app_b = \
            qm_b, inbox_b, bridge_b, app_b

    async def wait_for_inbox_on_a(self, handle, timeout=2.0):
        """Poll until inbox_a has a message for `handle` (in pending)."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            msgs = self.inbox_a.pending(handle)
            if msgs:
                return msgs
            await asyncio.sleep(0.01)
        raise TimeoutError(
            f"inbox_a got no message for {handle!r} within {timeout}s")

    async def shutdown(self):
        await self.qm_a.stop()
        await self.qm_b.stop()


async def build_two_serves(monkeypatch, *,
                            b_remotes_includes_a: bool = True) -> Pair:
    """Build a pair of in-process aegis-serves wired as each other's peers.

    Monkey-patches _build_client to route POSTs by spec.url between the
    two ASGI apps. Side B's observer is installed pointing at side A.

    If b_remotes_includes_a=False, side B will not have a ``remotes["a"]``
    entry — used by the unknown_peer failure-mode test.
    """
    # --- Side A ---
    sm_a = StubSessionManager()
    inbox_a = InboxRouter()
    qm_a = QueueManager({"impl": _q(cap=1)}, sm_a, inbox_a,
                        handle_factory=lambda used: "w1")
    plane_spec_a = RemotePlaneSpec(bind="127.0.0.1:8000")
    bridge_a = _Bridge(
        queue_manager=qm_a, inbox_router=inbox_a,
        remotes={"b": RemoteSpec(url="http://b", peer_name="a")},
        remote_plane=plane_spec_a)
    app_a = build_plane(bridge_a, plane_spec_a)

    # --- Side B ---
    sm_b = StubSessionManager()
    inbox_b = InboxRouter()
    qm_b = QueueManager({"impl": _q(cap=1)}, sm_b, inbox_b,
                        handle_factory=lambda used: "w1")
    plane_spec_b = RemotePlaneSpec(bind="127.0.0.1:8001")
    b_remotes = ({"a": RemoteSpec(url="http://a", peer_name="b")}
                  if b_remotes_includes_a else {})
    bridge_b = _Bridge(
        queue_manager=qm_b, inbox_router=inbox_b,
        remotes=b_remotes, remote_plane=plane_spec_b)
    app_b = build_plane(bridge_b, plane_spec_b)
    install_callback_observer(qm_b, remotes=bridge_b.remotes,
                               self_peer_name="b")

    # --- Route _build_client by url ---
    async def _client_factory(spec: RemoteSpec) -> httpx.AsyncClient:
        if spec.url == "http://a":
            return httpx.AsyncClient(transport=ASGITransport(app=app_a),
                                      base_url=spec.url)
        if spec.url == "http://b":
            return httpx.AsyncClient(transport=ASGITransport(app=app_b),
                                      base_url=spec.url)
        raise ValueError(f"unknown peer url: {spec.url!r}")
    monkeypatch.setattr(
        "aegis.remote.client._build_client", _client_factory)

    return Pair(qm_a, inbox_a, bridge_a, app_a,
                qm_b, inbox_b, bridge_b, app_b)
