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
