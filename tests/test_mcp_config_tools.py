"""End-to-end MCP-tool tests for the config-edit surface.

Drives the FastMCP server through its tool registry — same path agents
hit at runtime — against a stub AppBridge.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.mcp.server import build_server


class _StubBridge:
    """Minimal AppBridge stub for config-tool tests. Records register
    calls so the live-registration tests can assert against them."""

    def __init__(self) -> None:
        self.queue_manager = None
        self.inbox_router = None
        self.canvas_manager = None
        self.terminal_manager = None
        self.groups = None
        self.remotes: dict = {}
        self.scheduler = None
        self.state_root = Path.cwd()
        self.workflow_registry = type("R", (), {"get": lambda self, _: None})()
        self.registered_agents: list[tuple[str, object]] = []
        self.registered_queues: list[object] = []
        self.reload_plugins_calls = 0
        self.register_agent_error: Exception | None = None
        self.register_queue_error: Exception | None = None
        self.reload_plugins_error: Exception | None = None

    def list_sessions(self): return []
    def list_agents(self): return []
    def inline_schedule_names(self): return set()
    async def handoff(self, *a, **kw): return "noop"
    async def spawn(self, *a, **kw): return "noop"
    async def close(self, handle): return None

    def register_agent(self, slug, agent):
        if self.register_agent_error is not None:
            raise self.register_agent_error
        self.registered_agents.append((slug, agent))

    def register_queue(self, queue):
        if self.register_queue_error is not None:
            raise self.register_queue_error
        self.registered_queues.append(queue)

    def reload_plugins(self):
        if self.reload_plugins_error is not None:
            raise self.reload_plugins_error
        self.reload_plugins_calls += 1


async def _call(server, tool_name: str, **kwargs):
    """Invoke an MCP tool by name, unwrap the FastMCP ToolResult."""
    res = await server.call_tool(tool_name, kwargs)
    if hasattr(res, "structured_content") and res.structured_content is not None:
        sc = res.structured_content
        if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
            return sc["result"]
        return sc
    if hasattr(res, "data"):
        return res.data
    return res


@pytest.fixture
def root_with_yaml(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        "  researcher:\n"
        "    provider: claude-code\n"
        "    model: opus\n"
        "default_agent: researcher\n"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- aegis_config_show -------------------------------------------------

async def test_config_show_returns_parsed_yaml(root_with_yaml):
    server = build_server(_StubBridge())
    data = await _call(server, "aegis_config_show")
    assert data["default_agent"] == "researcher"
    assert "researcher" in data["agents"]
    assert data["agents"]["researcher"]["model"] == "opus"
    assert data["agents"]["researcher"]["harness"] == "claude-code"


async def test_config_show_redacts_telegram_token(tmp_path, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  r:\n    provider: claude-code\n    model: opus\n"
        "default_agent: r\n"
        "telegram:\n  token: SECRET_TOKEN\n  chat_id: 42\n"
    )
    monkeypatch.chdir(tmp_path)
    server = build_server(_StubBridge())
    data = await _call(server, "aegis_config_show")
    assert data["telegram"]["token"] == "<set>"
    assert data["telegram"]["chat_id"] == 42


async def test_config_show_no_root_returns_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    server = build_server(_StubBridge())
    data = await _call(server, "aegis_config_show")
    assert "error" in data


# --- aegis_config_list_agents ------------------------------------------

async def test_config_list_agents_returns_full_metadata(root_with_yaml):
    server = build_server(_StubBridge())
    data = await _call(server, "aegis_config_list_agents")
    assert isinstance(data, list)
    by_slug = {row["slug"]: row for row in data}
    assert "researcher" in by_slug
    r = by_slug["researcher"]
    assert r["harness"] == "claude-code"
    assert r["model"] == "opus"
    assert "effort" in r and "permission" in r


# --- aegis_config_list_queues ------------------------------------------

async def test_config_list_queues_returns_metadata(tmp_path, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  r:\n    provider: claude-code\n    model: opus\n"
        "default_agent: r\n"
        "queues:\n  designs:\n    agent: r\n    max_parallel: 2\n"
    )
    monkeypatch.chdir(tmp_path)
    server = build_server(_StubBridge())
    data = await _call(server, "aegis_config_list_queues")
    assert any(row["name"] == "designs" and row["agent"] == "r"
               and row["max_parallel"] == 2 for row in data)


# --- aegis_config_list_schedules ---------------------------------------

async def test_config_list_schedules_empty_when_none(root_with_yaml):
    server = build_server(_StubBridge())
    data = await _call(server, "aegis_config_list_schedules")
    assert data == []


# --- aegis_config_add_agent --------------------------------------------

async def test_config_add_agent_persists_and_live_registers(root_with_yaml):
    bridge = _StubBridge()
    server = build_server(bridge)
    out = await _call(server, "aegis_config_add_agent",
                      slug="designer", harness="claude-code",
                      model="sonnet")
    assert out == {"ok": True, "live": True, "restart_required_for": []}
    assert len(bridge.registered_agents) == 1
    slug, agent = bridge.registered_agents[0]
    assert slug == "designer"
    assert agent.harness == "claude-code"
    assert agent.model == "sonnet"
    yml = (root_with_yaml / ".aegis.yaml").read_text()
    assert "designer:" in yml
    assert "sonnet" in yml


async def test_config_add_agent_duplicate_returns_error(root_with_yaml):
    server = build_server(_StubBridge())
    out = await _call(server, "aegis_config_add_agent",
                      slug="researcher", harness="claude-code",
                      model="opus")
    assert "error" in out
    assert "already exists" in out["error"]


async def test_config_add_agent_unknown_harness_returns_error(root_with_yaml):
    server = build_server(_StubBridge())
    out = await _call(server, "aegis_config_add_agent",
                      slug="weird", harness="madeup", model="x")
    assert "error" in out
    assert "unknown" in out["error"].lower()


async def test_config_add_agent_live_failure_returns_persisted_not_live(
        root_with_yaml):
    bridge = _StubBridge()
    bridge.register_agent_error = ValueError("hot-register oopsie")
    server = build_server(bridge)
    out = await _call(server, "aegis_config_add_agent",
                      slug="designer", harness="claude-code", model="sonnet")
    assert out["ok"] is True
    assert out["live"] is False
    assert "agents" in out["restart_required_for"]
    assert "note" in out


# --- aegis_config_remove_agent -----------------------------------------

async def test_config_remove_agent_persists_restart_required(tmp_path,
                                                              monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        "  researcher:\n    provider: claude-code\n    model: opus\n"
        "  designer:\n    provider: claude-code\n    model: sonnet\n"
        "default_agent: researcher\n"
    )
    monkeypatch.chdir(tmp_path)
    server = build_server(_StubBridge())
    out = await _call(server, "aegis_config_remove_agent", slug="designer")
    assert out == {"ok": True, "live": False,
                   "restart_required_for": ["agents"]}
    yml = (tmp_path / ".aegis.yaml").read_text()
    assert "designer:" not in yml


async def test_config_remove_agent_unknown_returns_error(root_with_yaml):
    server = build_server(_StubBridge())
    out = await _call(server, "aegis_config_remove_agent", slug="nope")
    assert "error" in out


# --- aegis_config_add_queue --------------------------------------------

async def test_config_add_queue_persists_and_live_registers(root_with_yaml):
    bridge = _StubBridge()
    server = build_server(bridge)
    out = await _call(server, "aegis_config_add_queue",
                      name="designs", agent="researcher",
                      max_parallel=2)
    assert out == {"ok": True, "live": True, "restart_required_for": []}
    assert len(bridge.registered_queues) == 1
    queue = bridge.registered_queues[0]
    assert queue.name == "designs"
    assert queue.agent_profile == "researcher"
    assert queue.max_parallel == 2
    yml = (root_with_yaml / ".aegis.yaml").read_text()
    assert "designs:" in yml


async def test_config_add_queue_unknown_agent_returns_error(root_with_yaml):
    server = build_server(_StubBridge())
    out = await _call(server, "aegis_config_add_queue",
                      name="designs", agent="nope", max_parallel=1)
    assert "error" in out


async def test_config_add_queue_duplicate_returns_error(tmp_path,
                                                        monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  r:\n    provider: claude-code\n    model: opus\n"
        "default_agent: r\n"
        "queues:\n  designs:\n    agent: r\n    max_parallel: 1\n"
    )
    monkeypatch.chdir(tmp_path)
    server = build_server(_StubBridge())
    out = await _call(server, "aegis_config_add_queue",
                      name="designs", agent="r", max_parallel=2)
    assert "error" in out


# --- aegis_config_remove_queue -----------------------------------------

async def test_config_remove_queue_persists_restart_required(tmp_path,
                                                              monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  r:\n    provider: claude-code\n    model: opus\n"
        "default_agent: r\n"
        "queues:\n  designs:\n    agent: r\n    max_parallel: 1\n"
    )
    monkeypatch.chdir(tmp_path)
    server = build_server(_StubBridge())
    out = await _call(server, "aegis_config_remove_queue", name="designs")
    assert out == {"ok": True, "live": False,
                   "restart_required_for": ["queues"]}


async def test_config_remove_queue_unknown_returns_error(root_with_yaml):
    server = build_server(_StubBridge())
    out = await _call(server, "aegis_config_remove_queue", name="nope")
    assert "error" in out


# --- aegis_config_add_plugin_dir ---------------------------------------

async def test_config_add_plugin_dir_persists_and_reloads(root_with_yaml):
    bridge = _StubBridge()
    server = build_server(bridge)
    (root_with_yaml / ".aegis" / "plugins").mkdir(parents=True)
    out = await _call(server, "aegis_config_add_plugin_dir",
                      path=".aegis/plugins")
    assert out == {"ok": True, "live": True, "restart_required_for": []}
    assert bridge.reload_plugins_calls == 1


# --- aegis_config_remove_plugin_dir ------------------------------------

async def test_config_remove_plugin_dir_persists(tmp_path, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  r:\n    provider: claude-code\n    model: opus\n"
        "default_agent: r\n"
        "plugin_dirs:\n  - .aegis/plugins\n"
    )
    monkeypatch.chdir(tmp_path)
    server = build_server(_StubBridge())
    out = await _call(server, "aegis_config_remove_plugin_dir",
                      path=".aegis/plugins")
    assert out == {"ok": True, "live": False,
                   "restart_required_for": ["plugins"]}
    yml = (tmp_path / ".aegis.yaml").read_text()
    assert ".aegis/plugins" not in yml


# --- aegis_config_set_schedule_enabled ---------------------------------

async def test_config_set_schedule_enabled_toggles(tmp_path, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  r:\n    provider: claude-code\n    model: opus\n"
        "default_agent: r\n"
        "schedules:\n"
        "  morning:\n    cron: '0 6 * * *'\n    workflow: prompt\n"
        "    payload: hi\n    enabled: true\n"
    )
    monkeypatch.chdir(tmp_path)
    server = build_server(_StubBridge())
    out = await _call(server, "aegis_config_set_schedule_enabled",
                      name="morning", enabled=False)
    assert out == {"ok": True, "live": True, "restart_required_for": [],
                   "enabled": False}


# --- aegis_config_toggle_schedule_enabled ------------------------------

async def test_config_toggle_schedule_enabled_flips(tmp_path, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  r:\n    provider: claude-code\n    model: opus\n"
        "default_agent: r\n"
        "schedules:\n"
        "  morning:\n    cron: '0 6 * * *'\n    workflow: prompt\n"
        "    payload: hi\n    enabled: true\n"
    )
    monkeypatch.chdir(tmp_path)
    server = build_server(_StubBridge())
    out = await _call(server, "aegis_config_toggle_schedule_enabled",
                      name="morning")
    assert out["ok"] is True
    assert out["enabled"] is False
