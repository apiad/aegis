"""Two-serve hermetic fixture for callback + schedule round-trip tests."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from httpx import ASGITransport

from aegis.config.yaml_loader import load_config
from aegis.queue import InboxRouter, QueueManager
from aegis.remote.callback_observer import install_callback_observer
from aegis.remote.client import (
    remote_schedule_logs,
    remote_schedule_push,
    remote_schedule_remove,
)
from aegis.remote.config import RemotePlaneSpec, RemoteSpec
from aegis.remote.plane import build_plane
from aegis.scheduler.clock import FakeClock
from aegis.scheduler.scheduler import Scheduler
from aegis.workflow.decorator import get_workflow

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
    state_root: Path | None = None
    scheduler: Any = None
    workflow_registry: Any = None
    _inline_names: set | None = None

    def inline_schedule_names(self) -> set:
        return self._inline_names or set()

    # AppBridge surface minimal stubs.
    def list_sessions(self): return []
    def list_agents(self): return []
    async def handoff(self, *a, **k): return ""
    async def spawn(self, *a, **k): return ""
    async def close(self, *a, **k): return None


class Pair:
    """Holds both sides + provides high-level helpers used by tests."""
    def __init__(self, qm_a, inbox_a, bridge_a, app_a,
                 qm_b, inbox_b, bridge_b, app_b,
                 *, scheduler_b=None, clock_b=None,
                 state_root_b=None):
        self.qm_a, self.inbox_a, self.bridge_a, self.app_a = \
            qm_a, inbox_a, bridge_a, app_a
        self.qm_b, self.inbox_b, self.bridge_b, self.app_b = \
            qm_b, inbox_b, bridge_b, app_b
        self.scheduler_b = scheduler_b
        self.clock_b = clock_b
        self.state_root_b = state_root_b

    async def wait_for_inbox_on_a(self, handle, timeout=2.0):
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            msgs = self.inbox_a.pending(handle)
            if msgs:
                return msgs
            await asyncio.sleep(0.01)
        raise TimeoutError(
            f"inbox_a got no message for {handle!r} within {timeout}s")

    async def push_schedule_a_to_b(self, *, name: str,
                                    spec_body: dict) -> dict:
        return await remote_schedule_push(
            self.bridge_a.remotes["b"],
            name=name, spec_body=spec_body,
            pushed_from="peer:a")

    async def reload_b(self) -> None:
        assert self.scheduler_b is not None and self.state_root_b is not None
        cfg = load_config(self.state_root_b)
        self.scheduler_b.replace_schedules(cfg.schedules)

    async def wait_for_schedule_on_b(self, name: str,
                                      timeout: float = 5.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if name in self.scheduler_b.schedules:
                return
            await asyncio.sleep(0.02)
        raise TimeoutError(
            f"scheduler_b never saw {name!r} within {timeout}s")

    async def tick_b(self, seconds: int) -> None:
        assert self.clock_b is not None and self.scheduler_b is not None
        self.clock_b.advance(seconds=seconds)
        await self.scheduler_b.tick()
        tasks = self.scheduler_b.pending_fire_tasks()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def wait_for_fire_count_on_b(self, name: str, count: int,
                                         timeout: float = 5.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            st = self.scheduler_b._state.get(name)
            if st is not None and st.fire_count >= count:
                return
            await asyncio.sleep(0.02)
        raise TimeoutError(
            f"scheduler_b never reached fire_count>={count} for {name!r}")

    async def fetch_schedule_logs_from_a(self, name: str) -> dict:
        return await remote_schedule_logs(
            self.bridge_a.remotes["b"], name)

    async def remove_schedule_from_a(self, name: str) -> dict:
        result = await remote_schedule_remove(
            self.bridge_a.remotes["b"], name)
        await self.reload_b()
        return result

    async def wait_for_schedule_gone_on_b(self, name: str,
                                            timeout: float = 5.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if name not in self.scheduler_b.schedules:
                return
            await asyncio.sleep(0.02)
        raise TimeoutError(
            f"scheduler_b still has {name!r} after {timeout}s")

    async def shutdown(self):
        await self.qm_a.stop()
        await self.qm_b.stop()


async def build_two_serves(monkeypatch, tmp_path: Path | None = None, *,
                            b_remotes_includes_a: bool = True) -> Pair:
    """Build a pair of in-process aegis-serves wired as each other's peers.

    Side B optionally carries a real Scheduler + FakeClock when
    ``tmp_path`` is provided (Task 10 e2e). When omitted, side B has no
    scheduler — used by the Task 6 callback tests.
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

    scheduler_b = None
    clock_b = None
    state_root_b = None
    workflow_registry = None
    if tmp_path is not None:
        state_root_b = tmp_path / "b"
        state_root_b.mkdir(parents=True, exist_ok=True)
        (state_root_b / ".aegis.yaml").write_text("")  # ensure load_config works
        scheduler_state_dir = state_root_b / ".aegis" / "state"
        scheduler_state_dir.mkdir(parents=True, exist_ok=True)
        clock_b = FakeClock(start=datetime.now(timezone.utc))

        async def _run_workflow(name: str, args: dict):
            fn = get_workflow(name)
            if fn is None:
                raise KeyError(f"unknown workflow: {name!r}")
            # Engine arg is unused by the test workflow; pass a stub.
            return await fn(SimpleNamespace(), **args) if args \
                else await fn(SimpleNamespace())
        scheduler_b = Scheduler(
            schedules={}, state_dir=scheduler_state_dir,
            run_workflow=_run_workflow, clock=clock_b)
        workflow_registry = SimpleNamespace(get=get_workflow)

    bridge_b = _Bridge(
        queue_manager=qm_b, inbox_router=inbox_b,
        remotes=b_remotes, remote_plane=plane_spec_b,
        state_root=state_root_b,
        scheduler=scheduler_b,
        workflow_registry=workflow_registry,
        _inline_names=set())
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
                qm_b, inbox_b, bridge_b, app_b,
                scheduler_b=scheduler_b, clock_b=clock_b,
                state_root_b=state_root_b)
