"""MCP-level tests for the aegis_schedule_* tools (Task 11)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from aegis.mcp.bridge import SessionInfo
from aegis.mcp.server import build_server
from aegis.remote.config import RemotePlaneSpec, RemoteSpec
from aegis.remote.plane import build_plane
from aegis.scheduler.push import write_atomic
from aegis.scheduler.scheduler import Scheduler


async def _call(server, tool_name, **kwargs):
    tools = await server.list_tools()
    tool = next(t for t in tools if t.name == tool_name)
    result = await tool.run(kwargs)
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]
    if sc is not None:
        return sc
    return result.content[0].text


def _registry(known: set[str]):
    return SimpleNamespace(
        get=lambda n: object() if n in known else None)


async def _noop_run(name: str, args: dict):
    return None


def _build_scheduler(state_root, schedules):
    state_dir = state_root / ".aegis" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return Scheduler(
        schedules=schedules, state_dir=state_dir,
        run_workflow=_noop_run)


class _FakeBridge:
    def __init__(self, *, state_root, scheduler=None,
                 inline_names: set[str] | None = None,
                 known_workflows: set[str] = frozenset({"enqueue"}),
                 remotes: dict | None = None):
        from aegis.queue import InboxRouter
        self.queue_manager = None
        self.remotes = remotes or {}
        self.inbox_router = InboxRouter()
        self.canvas_manager = None
        self.terminal_manager = None
        self.groups = None
        self.scheduler = scheduler
        self.state_root = state_root
        self.workflow_registry = _registry(set(known_workflows))
        self._inline = set(inline_names or set())

    def inline_schedule_names(self) -> set[str]:
        return set(self._inline)

    def list_sessions(self) -> list[SessionInfo]:
        return []

    def list_agents(self) -> list[str]:
        return []

    async def handoff(self, *a, **k) -> str:
        return ""

    async def spawn(self, *a, **k) -> str:
        return ""

    async def close(self, *a, **k) -> None:
        return None


# ── push ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schedule_push_local_writes_yaml(tmp_path):
    bridge = _FakeBridge(state_root=tmp_path)
    server = build_server(bridge)
    result = await _call(
        server, "aegis_schedule_push",
        name="nightly",
        spec={"workflow": "enqueue", "cron": "0 2 * * *",
              "args": {"queue": "impl", "payload": "x", "callback": False},
              "lifecycle": "forever"},
        from_handle="zion-alex")
    assert result["name"] == "nightly"
    assert result["written_to"].endswith("nightly.yaml")
    written = tmp_path / ".aegis" / "schedules" / "nightly.yaml"
    assert written.exists()
    content = written.read_text()
    assert content.startswith("# pushed_from: agent:zion-alex")


@pytest.mark.asyncio
async def test_schedule_push_unknown_target_errors(tmp_path):
    bridge = _FakeBridge(state_root=tmp_path)
    server = build_server(bridge)
    result = await _call(
        server, "aegis_schedule_push",
        name="n", spec={"workflow": "enqueue", "cron": "0 2 * * *",
                        "args": {}, "lifecycle": "forever"},
        from_handle="h", target="vps")
    assert "error" in result
    assert "unknown target" in result["error"]


@pytest.mark.asyncio
async def test_schedule_push_invalid_spec_returns_error(tmp_path):
    bridge = _FakeBridge(state_root=tmp_path)
    server = build_server(bridge)
    result = await _call(
        server, "aegis_schedule_push",
        name="bad", spec={"workflow": "does_not_exist",
                          "cron": "0 2 * * *", "args": {},
                          "lifecycle": "forever"},
        from_handle="h")
    assert "error" in result
    assert "unknown workflow" in result["error"]


@pytest.mark.asyncio
async def test_schedule_push_remote_routes_through_client(tmp_path,
                                                          monkeypatch):
    captured: dict[str, Any] = {}

    async def _fake_push(spec, *, name, spec_body, pushed_from):
        captured["spec"] = spec
        captured["name"] = name
        captured["spec_body"] = spec_body
        captured["pushed_from"] = pushed_from
        return {"name": name, "written_to": f"schedules/{name}.yaml"}

    monkeypatch.setattr(
        "aegis.mcp.server.remote_schedule_push", _fake_push)

    bridge = _FakeBridge(
        state_root=tmp_path,
        remotes={"vps": RemoteSpec(url="http://stub")})
    server = build_server(bridge)
    result = await _call(
        server, "aegis_schedule_push",
        name="rem", spec={"workflow": "enqueue", "cron": "0 2 * * *",
                          "args": {}, "lifecycle": "forever"},
        from_handle="zion-alex", target="vps")
    assert result["name"] == "rem"
    assert captured["name"] == "rem"
    assert captured["pushed_from"] == "agent:zion-alex"


# ── list ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schedule_list_local_classifies(tmp_path):
    inline_spec = {"workflow": "enqueue", "cron": "0 1 * * *",
                   "args": {"queue": "impl", "payload": "x"}}
    pushed_spec = {"workflow": "enqueue", "cron": "0 3 * * *",
                   "args": {"queue": "impl", "payload": "x"}}
    write_atomic(tmp_path, "psh", pushed_spec, "agent:h")
    schedules = {"inl": inline_spec, "psh": pushed_spec}
    sched = _build_scheduler(tmp_path, schedules)
    bridge = _FakeBridge(state_root=tmp_path, scheduler=sched,
                         inline_names={"inl"})
    server = build_server(bridge)
    result = await _call(server, "aegis_schedule_list", from_handle="h")
    rows = {r["name"]: r for r in result["schedules"]}
    assert rows["inl"]["source"] == "inline"
    assert rows["psh"]["source"] == "pushed"


@pytest.mark.asyncio
async def test_schedule_list_no_scheduler_returns_empty(tmp_path):
    bridge = _FakeBridge(state_root=tmp_path)  # scheduler=None
    server = build_server(bridge)
    result = await _call(server, "aegis_schedule_list", from_handle="h")
    assert result == {"schedules": []}


# ── show ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schedule_show_local_404(tmp_path):
    sched = _build_scheduler(tmp_path, {})
    bridge = _FakeBridge(state_root=tmp_path, scheduler=sched)
    server = build_server(bridge)
    result = await _call(server, "aegis_schedule_show",
                         name="nope", from_handle="h")
    assert result == {"error": "not found"}


@pytest.mark.asyncio
async def test_schedule_show_local_returns_payload(tmp_path):
    spec = {"workflow": "enqueue", "cron": "0 3 * * *",
            "args": {"queue": "impl", "payload": "x"}}
    write_atomic(tmp_path, "psh", spec, "agent:zion")
    sched = _build_scheduler(tmp_path, {"psh": spec})
    bridge = _FakeBridge(state_root=tmp_path, scheduler=sched)
    server = build_server(bridge)
    result = await _call(server, "aegis_schedule_show",
                         name="psh", from_handle="h")
    assert result["name"] == "psh"
    assert result["source"] == "pushed"
    assert result["spec"]["workflow"] == "enqueue"
    assert result["pushed_from"] == "agent:zion"


# ── remove ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schedule_remove_local_unlinks_pushed(tmp_path):
    spec = {"workflow": "enqueue", "cron": "0 3 * * *",
            "args": {"queue": "impl", "payload": "x"}}
    dest = write_atomic(tmp_path, "psh", spec, "agent:zion")
    sched = _build_scheduler(tmp_path, {"psh": spec})
    bridge = _FakeBridge(state_root=tmp_path, scheduler=sched)
    server = build_server(bridge)
    result = await _call(server, "aegis_schedule_remove",
                         name="psh", from_handle="h")
    assert result == {"ok": True}
    assert not dest.exists()


@pytest.mark.asyncio
async def test_schedule_remove_local_rejects_inline(tmp_path):
    spec = {"workflow": "enqueue", "cron": "0 1 * * *",
            "args": {"queue": "impl", "payload": "x"}}
    sched = _build_scheduler(tmp_path, {"inl": spec})
    bridge = _FakeBridge(state_root=tmp_path, scheduler=sched,
                         inline_names={"inl"})
    server = build_server(bridge)
    result = await _call(server, "aegis_schedule_remove",
                         name="inl", from_handle="h")
    assert "error" in result
    assert "inline" in result["error"]


# ── logs ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schedule_logs_local_tails_jsonl(tmp_path):
    bridge = _FakeBridge(state_root=tmp_path)
    log_path = tmp_path / ".aegis" / "state" / "schedules" / "psh.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    records = [{"event": "fire_started", "i": i} for i in range(5)]
    log_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    server = build_server(bridge)
    result = await _call(server, "aegis_schedule_logs",
                         name="psh", from_handle="h", tail=3)
    assert result["records"] == records[-3:]


# ── one remote-routed verb to prove the pattern ────────────────────────

@pytest.mark.asyncio
async def test_schedule_list_remote_routes_through_plane(tmp_path,
                                                          monkeypatch):
    # Stand up a remote plane backed by a real scheduler with one entry.
    spec = {"workflow": "enqueue", "cron": "0 3 * * *",
            "args": {"queue": "impl", "payload": "x"}}
    write_atomic(tmp_path, "psh", spec, "peer:zion")
    sched = _build_scheduler(tmp_path, {"psh": spec})
    remote_bridge = SimpleNamespace(
        queue_manager=object(),
        inbox_router=None,
        state_root=tmp_path,
        workflow_registry=_registry({"enqueue"}),
        scheduler=sched,
        inline_schedule_names=lambda: set(),
    )
    plane_app = build_plane(
        remote_bridge, RemotePlaneSpec(bind="127.0.0.1:8557"))
    transport = ASGITransport(app=plane_app)

    async def _client_factory(s: RemoteSpec) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, base_url=s.url)
    monkeypatch.setattr(
        "aegis.remote.client._build_client", _client_factory)

    local_bridge = _FakeBridge(
        state_root=tmp_path,
        remotes={"vps": RemoteSpec(url="http://stub")})
    server = build_server(local_bridge)
    result = await _call(server, "aegis_schedule_list",
                         from_handle="h", target="vps")
    rows = {r["name"]: r for r in result["schedules"]}
    assert "psh" in rows
    assert rows["psh"]["source"] == "pushed"
