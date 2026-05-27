# MCP Config-Edit Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an MCP layer that lets spawned agents mutate `.aegis.yaml` (add/remove agents, queues, plugin dirs; toggle schedules) through the existing `aegis.config.edit` helpers, with additive paths hot-registered on the live `QueueManager` / agent map / plugin loader.

**Architecture:** 12 new `@server.tool()` entries in `src/aegis/mcp/server.py`, each thin-wrapping one `aegis.config.edit` helper. Additive writes also call new `bridge.register_*` methods that mutate the live `SessionManager._agents` dict, `QueueManager._queues` map, or re-import plugins via `import_plugins(cfg)`. One `asyncio.Lock` per built server serializes all writes. Reads re-parse `.aegis.yaml` via `load_config`.

**Tech Stack:** Python 3.13, `fastmcp` (existing), `ruamel.yaml` via `aegis.config.edit` (existing), `pytest`, `uv`.

**Spec:** `docs/superpowers/specs/2026-05-27-mcp-config-edit-design.md`.

---

## File Structure

**Create:**
- `tests/test_mcp_config_tools.py` — unit tests for the 12 new MCP tools.
- `tests/test_queue_manager_register.py` — unit test for `QueueManager.register_queue`.
- `tests/test_session_manager_register.py` — unit test for `SessionManager.register_agent`.

**Modify:**
- `src/aegis/mcp/bridge.py` — extend `AppBridge` Protocol with `register_agent`, `register_queue`, `reload_plugins`.
- `src/aegis/queue/manager.py` — add `register_queue(queue)` public method.
- `src/aegis/core/manager.py` — implement `register_agent`, `register_queue`, `reload_plugins` on `SessionManager`.
- `src/aegis/tui/app.py` — forward those three methods to `SessionManager`.
- `src/aegis/mcp/server.py` — add 12 `@server.tool` definitions and one `asyncio.Lock` shared across writes; extend `BRIEFING` with a `Config edit` block.
- `tests/test_mcp_live.py` — append one `live`-marked round-trip test (agent calls `aegis_config_add_queue` then `aegis_enqueue` to it).

---

## Task 1: Extend `AppBridge` Protocol with config-edit hooks

**Files:**
- Modify: `src/aegis/mcp/bridge.py`

- [ ] **Step 1: Open `src/aegis/mcp/bridge.py`. Add three Protocol methods immediately after `async def close(self, handle: str) -> None: ...`**

```python
    def register_agent(self, slug: str, agent: object) -> None:
        """Add a freshly-validated Agent to the live agent map. Idempotent
        on identical (slug, agent) pairs; raises ValueError on slug
        collision with a different agent."""
        ...

    def register_queue(self, queue: object) -> None:
        """Add a freshly-validated Queue to the live QueueManager.
        Raises ValueError on name collision."""
        ...

    def reload_plugins(self) -> None:
        """Re-run import_plugins(load_config(state_root)) so newly-added
        plugin_dirs entries register their @workflow functions."""
        ...
```

- [ ] **Step 2: No tests yet — Protocol changes verified by Task 2/3 tests that implement them. Commit.**

```bash
git add src/aegis/mcp/bridge.py
git commit -m "feat(mcp/bridge): protocol gains register_agent / register_queue / reload_plugins"
```

---

## Task 2: Add `QueueManager.register_queue()`

**Files:**
- Modify: `src/aegis/queue/manager.py`
- Test: `tests/test_queue_manager_register.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_queue_manager_register.py
from unittest.mock import MagicMock

import pytest

from aegis.queue.manager import QueueManager
from aegis.queue.schema import Queue


def _q(name: str) -> Queue:
    return Queue(name=name, agent_profile="researcher", max_parallel=1,
                 provider="claude-code", model="opus", budgets=[])


def test_register_queue_adds_to_live_map_and_state():
    qm = QueueManager({}, session_manager=MagicMock(),
                      inbox_router=MagicMock())
    qm.register_queue(_q("designs"))
    assert "designs" in qm._queues
    # New queue starts with empty pending + inflight slots so dispatch
    # checks don't KeyError on first enqueue.
    assert qm._pending["designs"] == []
    assert qm._inflight["designs"] == []
    assert "designs" in qm.list_queues()


def test_register_queue_rejects_duplicate_name():
    qm = QueueManager({"designs": _q("designs")},
                      session_manager=MagicMock(),
                      inbox_router=MagicMock())
    with pytest.raises(ValueError, match="already registered"):
        qm.register_queue(_q("designs"))
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_queue_manager_register.py -v
```

Expected: FAIL — `AttributeError: 'QueueManager' object has no attribute 'register_queue'`. (`list_queues` may also fail if the helper doesn't exist; check the actual error.)

- [ ] **Step 3: Find the right place in `src/aegis/queue/manager.py` — immediately after the `__init__` method (around line 89) — and add:**

```python
    def register_queue(self, queue: Queue) -> None:
        """Add a queue to the live map. Idempotent if (name, queue) match;
        raises ValueError on name collision with a different queue."""
        existing = self._queues.get(queue.name)
        if existing is not None:
            if existing == queue:
                return
            raise ValueError(
                f"queue {queue.name!r} already registered")
        self._queues[queue.name] = queue
        self._pending[queue.name] = []
        self._inflight[queue.name] = []

    def list_queues(self) -> list[str]:
        return sorted(self._queues)
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_queue_manager_register.py -v
```

Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/queue/manager.py tests/test_queue_manager_register.py
git commit -m "feat(queue): QueueManager.register_queue for live queue registration"
```

---

## Task 3: Implement `register_agent`, `register_queue`, `reload_plugins` on `SessionManager`

**Files:**
- Modify: `src/aegis/core/manager.py`
- Test: `tests/test_session_manager_register.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session_manager_register.py
from unittest.mock import MagicMock

import pytest

from aegis.config import Agent
from aegis.core.manager import SessionManager


def _agent(model: str = "opus") -> Agent:
    return Agent(harness="claude-code", model=model)


def _sm():
    sm = SessionManager(
        agents={"r": _agent()},
        default_agent="r",
        make_session=lambda *a, **kw: MagicMock(),
        mcp=None,
        inbox=MagicMock())
    return sm


def test_register_agent_adds_to_live_map():
    sm = _sm()
    sm.register_agent("designer", _agent(model="sonnet"))
    assert "designer" in sm.list_agents()
    assert sm._agents["designer"].model == "sonnet"


def test_register_agent_duplicate_slug_raises():
    sm = _sm()
    with pytest.raises(ValueError, match="already registered"):
        sm.register_agent("r", _agent(model="sonnet"))


def test_register_agent_idempotent_on_identical():
    sm = _sm()
    sm.register_agent("designer", _agent(model="sonnet"))
    sm.register_agent("designer", _agent(model="sonnet"))   # no raise
    assert sm._agents["designer"].model == "sonnet"


def test_register_queue_forwards_to_queue_manager():
    sm = _sm()
    qm = MagicMock()
    sm.attach_queue_manager(qm)
    queue = MagicMock()
    sm.register_queue(queue)
    qm.register_queue.assert_called_once_with(queue)


def test_register_queue_without_queue_manager_raises():
    sm = _sm()
    with pytest.raises(RuntimeError, match="no queue_manager attached"):
        sm.register_queue(MagicMock())


def test_reload_plugins_invokes_import_plugins(monkeypatch):
    sm = _sm()
    from pathlib import Path
    sm.state_root = Path("/tmp")
    calls = []
    def _fake_load(root):
        return MagicMock(plugin_dirs=[])
    def _fake_import(cfg):
        calls.append(cfg)
    monkeypatch.setattr(
        "aegis.config.yaml_loader.load_config", _fake_load)
    monkeypatch.setattr(
        "aegis.config.yaml_loader.import_plugins", _fake_import)
    sm.reload_plugins()
    assert len(calls) == 1
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_session_manager_register.py -v
```

Expected: FAIL — `AttributeError: 'SessionManager' object has no attribute 'register_agent'`.

- [ ] **Step 3: In `src/aegis/core/manager.py`, immediately after `attach_scheduler_context` (around line 67), add:**

```python
    def register_agent(self, slug: str, agent) -> None:
        existing = self._agents.get(slug)
        if existing is not None:
            if existing == agent:
                return
            raise ValueError(f"agent {slug!r} already registered")
        self._agents[slug] = agent

    def register_queue(self, queue) -> None:
        if self.queue_manager is None:
            raise RuntimeError(
                "no queue_manager attached; cannot register queue")
        self.queue_manager.register_queue(queue)

    def reload_plugins(self) -> None:
        from pathlib import Path

        from aegis.config.yaml_loader import (
            import_plugins, load_config as _load_yaml,
        )
        root = self.state_root or Path.cwd()
        cfg = _load_yaml(root)
        import_plugins(cfg)
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_session_manager_register.py -v
```

Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/core/manager.py tests/test_session_manager_register.py
git commit -m "feat(core/manager): register_agent / register_queue / reload_plugins"
```

---

## Task 4: Forward the three register hooks on `AegisApp`

**Files:**
- Modify: `src/aegis/tui/app.py`

- [ ] **Step 1: Open `src/aegis/tui/app.py`. Find the section where the AegisApp class implements bridge methods (right after `list_agents` near line 618). Add:**

```python
    def register_agent(self, slug: str, agent) -> None:
        self._session_manager.register_agent(slug, agent)

    def register_queue(self, queue) -> None:
        self._session_manager.register_queue(queue)

    def reload_plugins(self) -> None:
        self._session_manager.reload_plugins()
```

- [ ] **Step 2: Confirm `self._session_manager` exists by skimming `AegisApp.__init__` (around line 178).** If the manager is stored under a different attribute name (e.g. `self._sm`), use that — keep grep honest:

```bash
grep -n "session_manager\|self\._sm" src/aegis/tui/app.py | head
```

Use the actual attribute name in the three forwarders above.

- [ ] **Step 3: Run the existing TUI tests to confirm nothing regressed**

```bash
uv run pytest tests/test_tui_app.py tests/test_pane*.py -q
```

Expected: existing tests still pass (the new methods are pure additions; no behavior change yet).

- [ ] **Step 4: Commit**

```bash
git add src/aegis/tui/app.py
git commit -m "feat(tui/app): forward register_agent/register_queue/reload_plugins to SessionManager"
```

---

## Task 5: Add a write lock to `build_server`

**Files:**
- Modify: `src/aegis/mcp/server.py`

- [ ] **Step 1: In `src/aegis/mcp/server.py`, inside `build_server(bridge)` (around line 354), immediately after `server = FastMCP("aegis")`, add:**

```python
    config_write_lock = asyncio.Lock()
```

- [ ] **Step 2: No standalone test yet — the lock is exercised by Task 18's concurrency test. Commit.**

```bash
git add src/aegis/mcp/server.py
git commit -m "feat(mcp/server): asyncio.Lock to serialize config-edit tool writes"
```

---

## Task 6: `aegis_config_show` read tool

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_mcp_config_tools.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_config_tools.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aegis.mcp.server import build_server


@pytest.fixture
def root_with_yaml(tmp_path: Path, monkeypatch) -> Path:
    """A tmp_path with a minimal .aegis.yaml; chdir'd-into."""
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        "  researcher:\n"
        "    provider: claude-code\n"
        "    model: opus\n"
        "default_agent: researcher\n"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _bridge() -> MagicMock:
    b = MagicMock()
    b.list_sessions.return_value = []
    b.list_agents.return_value = []
    return b


async def _call(server, tool_name: str, **kwargs):
    """Helper: invoke an MCP tool by name through the FastMCP client."""
    from fastmcp.client import Client
    async with Client(server) as client:
        result = await client.call_tool(tool_name, kwargs)
        return result.data


@pytest.mark.asyncio
async def test_config_show_returns_parsed_yaml(root_with_yaml):
    server = build_server(_bridge())
    data = await _call(server, "aegis_config_show")
    assert data["default_agent"] == "researcher"
    assert "researcher" in data["agents"]
    assert data["agents"]["researcher"]["model"] == "opus"
    assert data["agents"]["researcher"]["harness"] == "claude-code"


@pytest.mark.asyncio
async def test_config_show_redacts_telegram_token(tmp_path, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  r:\n    provider: claude-code\n    model: opus\n"
        "default_agent: r\n"
        "telegram:\n  token: SECRET_TOKEN\n  chat_id: 42\n"
    )
    monkeypatch.chdir(tmp_path)
    server = build_server(_bridge())
    data = await _call(server, "aegis_config_show")
    assert data["telegram"]["token"] == "<set>"
    assert data["telegram"]["chat_id"] == 42


@pytest.mark.asyncio
async def test_config_show_no_root_returns_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .aegis.yaml
    server = build_server(_bridge())
    data = await _call(server, "aegis_config_show")
    assert "error" in data
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_mcp_config_tools.py::test_config_show_returns_parsed_yaml -v
```

Expected: FAIL — tool `aegis_config_show` not found.

- [ ] **Step 3: In `src/aegis/mcp/server.py`, after the `config_write_lock` line from Task 5, add:**

```python
    @server.tool
    async def aegis_config_show() -> dict:
        """Full parsed .aegis.yaml view. Telegram token redacted."""
        from aegis.config import ConfigError, find_project_root
        from aegis.config.yaml_loader import load_config as _load_yaml

        root = find_project_root()
        if root is None:
            return {"error": "no .aegis.yaml found"}
        try:
            cfg = _load_yaml(root)
        except ConfigError as e:
            return {"error": str(e)}
        out = {
            "agents": {
                slug: {
                    "harness": a.harness, "model": a.model,
                    "effort": a.effort.value if a.effort else None,
                    "permission": a.permission.value,
                } for slug, a in cfg.agents.items()
            },
            "queues": {
                name: {"agent": q.agent, "max_parallel": q.max_parallel,
                       "budgets": list(q.budgets or [])}
                for name, q in cfg.queues.items()
            },
            "schedules": {
                name: {"cron": s.cron, "enabled": s.enabled,
                       "workflow": s.workflow}
                for name, s in (cfg.schedules or {}).items()
            },
            "plugin_dirs": list(cfg.plugin_dirs or []),
            "default_agent": cfg.default_agent,
        }
        if cfg.telegram is not None:
            out["telegram"] = {
                "token": "<set>" if cfg.telegram.token else "<unset>",
                "chat_id": cfg.telegram.chat_id,
                "auto_prompt": cfg.telegram.auto_prompt,
            }
        return out
```

- [ ] **Step 4: Verify the field names on `cfg.queues[*]` and `cfg.schedules[*]` match by grepping:**

```bash
grep -n "agent_profile\|class Queue\|class Schedule" src/aegis/queue/schema.py src/aegis/scheduler/*.py | head
```

If the parsed `Queue` exposes `agent_profile` instead of `agent` (it does — see `aegis.config.load_queues` at `src/aegis/config/__init__.py:177`), change `q.agent` to `q.agent_profile` in the dict-comprehension above. Similarly check `schedule.cron` / `schedule.workflow` — adapt to the real attribute names.

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/test_mcp_config_tools.py -v
```

Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_mcp_config_tools.py
git commit -m "feat(mcp): aegis_config_show — full .aegis.yaml view with redacted telegram"
```

---

## Task 7: `aegis_config_list_agents` read tool

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_mcp_config_tools.py`

- [ ] **Step 1: Append test**

```python
@pytest.mark.asyncio
async def test_config_list_agents_returns_full_metadata(root_with_yaml):
    server = build_server(_bridge())
    data = await _call(server, "aegis_config_list_agents")
    assert isinstance(data, list)
    by_slug = {row["slug"]: row for row in data}
    assert "researcher" in by_slug
    r = by_slug["researcher"]
    assert r["harness"] == "claude-code"
    assert r["model"] == "opus"
    assert "effort" in r and "permission" in r
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_mcp_config_tools.py::test_config_list_agents_returns_full_metadata -v
```

Expected: FAIL — tool not found.

- [ ] **Step 3: Add the tool to `build_server`, immediately after `aegis_config_show`:**

```python
    @server.tool
    async def aegis_config_list_agents() -> list[dict]:
        """[{slug, harness, model, effort, permission}, …] from .aegis.yaml."""
        from aegis.config import ConfigError, find_project_root
        from aegis.config.yaml_loader import load_config as _load_yaml

        root = find_project_root()
        if root is None:
            return []
        try:
            cfg = _load_yaml(root)
        except ConfigError:
            return []
        return [
            {"slug": slug, "harness": a.harness, "model": a.model,
             "effort": a.effort.value if a.effort else None,
             "permission": a.permission.value}
            for slug, a in cfg.agents.items()
        ]
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/test_mcp_config_tools.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_mcp_config_tools.py
git commit -m "feat(mcp): aegis_config_list_agents — slug + full agent metadata"
```

---

## Task 8: `aegis_config_list_queues` read tool

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_mcp_config_tools.py`

- [ ] **Step 1: Append test**

```python
@pytest.mark.asyncio
async def test_config_list_queues_returns_metadata(tmp_path, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  r:\n    provider: claude-code\n    model: opus\n"
        "default_agent: r\n"
        "queues:\n  designs:\n    agent: r\n    max_parallel: 2\n"
    )
    monkeypatch.chdir(tmp_path)
    server = build_server(_bridge())
    data = await _call(server, "aegis_config_list_queues")
    assert any(row["name"] == "designs" and row["agent"] == "r"
               and row["max_parallel"] == 2 for row in data)
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_mcp_config_tools.py::test_config_list_queues_returns_metadata -v
```

- [ ] **Step 3: Add tool right after `aegis_config_list_agents`:**

```python
    @server.tool
    async def aegis_config_list_queues() -> list[dict]:
        """[{name, agent, max_parallel, budgets}, …] from .aegis.yaml."""
        from aegis.config import ConfigError, find_project_root
        from aegis.config.yaml_loader import load_config as _load_yaml

        root = find_project_root()
        if root is None:
            return []
        try:
            cfg = _load_yaml(root)
        except ConfigError:
            return []
        return [
            {"name": name, "agent": q.agent,
             "max_parallel": q.max_parallel,
             "budgets": list(getattr(q, "budgets", None) or [])}
            for name, q in (cfg.queues or {}).items()
        ]
```

Note: `cfg.queues` here is the raw YAML-loader queue spec object, not the resolved `Queue` from `load_queues`. Verify the field is `q.agent` (not `q.agent_profile`) — `cfg.queues` items are `QueueSpec` instances; grep to confirm:

```bash
grep -n "class QueueSpec\|^@dataclass\|@dataclass" src/aegis/config/yaml_loader.py | head
```

If the field is named differently in `QueueSpec`, use the actual name (e.g. `q.agent` if it's the raw YAML key).

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/test_mcp_config_tools.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_mcp_config_tools.py
git commit -m "feat(mcp): aegis_config_list_queues"
```

---

## Task 9: `aegis_config_list_schedules` read tool

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_mcp_config_tools.py`

- [ ] **Step 1: Append test**

```python
@pytest.mark.asyncio
async def test_config_list_schedules_empty_when_none(root_with_yaml):
    server = build_server(_bridge())
    data = await _call(server, "aegis_config_list_schedules")
    assert data == []
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_mcp_config_tools.py::test_config_list_schedules_empty_when_none -v
```

- [ ] **Step 3: Add tool right after `aegis_config_list_queues`:**

```python
    @server.tool
    async def aegis_config_list_schedules() -> list[dict]:
        """[{name, cron, enabled, workflow}, …] from .aegis.yaml."""
        from aegis.config import ConfigError, find_project_root
        from aegis.config.yaml_loader import load_config as _load_yaml

        root = find_project_root()
        if root is None:
            return []
        try:
            cfg = _load_yaml(root)
        except ConfigError:
            return []
        return [
            {"name": name,
             "cron": getattr(s, "cron", None),
             "enabled": getattr(s, "enabled", True),
             "workflow": getattr(s, "workflow", None)}
            for name, s in (cfg.schedules or {}).items()
        ]
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/test_mcp_config_tools.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_mcp_config_tools.py
git commit -m "feat(mcp): aegis_config_list_schedules"
```

---

## Task 10: `aegis_config_add_agent` write tool (live)

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_mcp_config_tools.py`

- [ ] **Step 1: Append tests**

```python
@pytest.mark.asyncio
async def test_config_add_agent_persists_and_live_registers(root_with_yaml):
    bridge = _bridge()
    server = build_server(bridge)
    out = await _call(server, "aegis_config_add_agent",
                      slug="designer", harness="claude-code",
                      model="sonnet")
    assert out == {"ok": True, "live": True, "restart_required_for": []}
    bridge.register_agent.assert_called_once()
    slug_arg, agent_arg = bridge.register_agent.call_args.args
    assert slug_arg == "designer"
    assert agent_arg.harness == "claude-code"
    assert agent_arg.model == "sonnet"
    # On-disk:
    yml = (root_with_yaml / ".aegis.yaml").read_text()
    assert "designer:" in yml
    assert "sonnet" in yml


@pytest.mark.asyncio
async def test_config_add_agent_duplicate_returns_error(root_with_yaml):
    server = build_server(_bridge())
    out = await _call(server, "aegis_config_add_agent",
                      slug="researcher", harness="claude-code",
                      model="opus")
    assert "error" in out
    assert "already exists" in out["error"]


@pytest.mark.asyncio
async def test_config_add_agent_unknown_harness_returns_error(
        root_with_yaml):
    server = build_server(_bridge())
    out = await _call(server, "aegis_config_add_agent",
                      slug="weird", harness="madeup", model="x")
    assert "error" in out
    assert "unknown" in out["error"].lower()


@pytest.mark.asyncio
async def test_config_add_agent_live_registration_failure_returns_persisted(
        root_with_yaml):
    bridge = _bridge()
    bridge.register_agent.side_effect = ValueError("hot-register oopsie")
    server = build_server(bridge)
    out = await _call(server, "aegis_config_add_agent",
                      slug="designer", harness="claude-code", model="sonnet")
    assert out["ok"] is True
    assert out["live"] is False
    assert "agents" in out["restart_required_for"]
    assert "note" in out
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_mcp_config_tools.py -v -k add_agent
```

- [ ] **Step 3: Add the tool to `build_server`, immediately after `aegis_config_list_schedules`:**

```python
    @server.tool
    async def aegis_config_add_agent(
        slug: str, harness: str, model: str,
        effort: str | None = None, permission: str | None = None,
    ) -> dict:
        """Add an agent profile to .aegis.yaml. Hot-registers on the live
        agent map so the next spawn can use the new slug."""
        from aegis.config import (
            Agent, ConfigError, find_project_root,
        )
        from aegis.config.edit import add_agent as _add

        root = find_project_root()
        if root is None:
            return {"error": "no .aegis.yaml found"}
        async with config_write_lock:
            try:
                _add(root, slug, provider=harness, model=model,
                     effort=effort, permission=permission)
            except ConfigError as e:
                return {"error": str(e)}
            # Build the Agent for live registration. Use the flat shape so
            # the pydantic validator runs and we get back-compat defaults.
            kw = {"harness": harness, "model": model}
            if effort is not None:
                kw["effort"] = effort
            if permission is not None:
                kw["permission"] = permission
            try:
                agent = Agent(**kw)
                bridge.register_agent(slug, agent)
            except Exception as e:                       # noqa: BLE001
                return {"ok": True, "live": False,
                        "restart_required_for": ["agents"],
                        "note": f"persisted but live-register failed: {e}"}
            return {"ok": True, "live": True, "restart_required_for": []}
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/test_mcp_config_tools.py -v -k add_agent
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_mcp_config_tools.py
git commit -m "feat(mcp): aegis_config_add_agent — write + live-register"
```

---

## Task 11: `aegis_config_remove_agent` write tool (persist-only)

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_mcp_config_tools.py`

- [ ] **Step 1: Append tests**

```python
@pytest.mark.asyncio
async def test_config_remove_agent_persists_restart_required(tmp_path,
                                                              monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        "  researcher:\n    provider: claude-code\n    model: opus\n"
        "  designer:\n    provider: claude-code\n    model: sonnet\n"
        "default_agent: researcher\n"
    )
    monkeypatch.chdir(tmp_path)
    server = build_server(_bridge())
    out = await _call(server, "aegis_config_remove_agent",
                      slug="designer")
    assert out == {"ok": True, "live": False,
                   "restart_required_for": ["agents"]}
    yml = (tmp_path / ".aegis.yaml").read_text()
    assert "designer:" not in yml


@pytest.mark.asyncio
async def test_config_remove_agent_unknown_returns_error(root_with_yaml):
    server = build_server(_bridge())
    out = await _call(server, "aegis_config_remove_agent", slug="nope")
    assert "error" in out
    assert "not in" in out["error"]
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_mcp_config_tools.py -v -k remove_agent
```

- [ ] **Step 3: Add the tool right after `aegis_config_add_agent`:**

```python
    @server.tool
    async def aegis_config_remove_agent(slug: str) -> dict:
        """Drop an agent profile from .aegis.yaml. Restart required."""
        from aegis.config import ConfigError, find_project_root
        from aegis.config.edit import remove_agent as _rm
        root = find_project_root()
        if root is None:
            return {"error": "no .aegis.yaml found"}
        async with config_write_lock:
            try:
                _rm(root, slug)
            except ConfigError as e:
                return {"error": str(e)}
        return {"ok": True, "live": False,
                "restart_required_for": ["agents"]}
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/test_mcp_config_tools.py -v -k remove_agent
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_mcp_config_tools.py
git commit -m "feat(mcp): aegis_config_remove_agent — persist; restart needed"
```

---

## Task 12: `aegis_config_add_queue` write tool (live)

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_mcp_config_tools.py`

- [ ] **Step 1: Append tests**

```python
@pytest.mark.asyncio
async def test_config_add_queue_persists_and_live_registers(
        root_with_yaml):
    bridge = _bridge()
    server = build_server(bridge)
    out = await _call(server, "aegis_config_add_queue",
                      name="designs", agent="researcher",
                      max_parallel=2)
    assert out == {"ok": True, "live": True, "restart_required_for": []}
    bridge.register_queue.assert_called_once()
    queue_arg = bridge.register_queue.call_args.args[0]
    assert queue_arg.name == "designs"
    assert queue_arg.agent_profile == "researcher"
    assert queue_arg.max_parallel == 2
    yml = (root_with_yaml / ".aegis.yaml").read_text()
    assert "designs:" in yml


@pytest.mark.asyncio
async def test_config_add_queue_unknown_agent_returns_error(
        root_with_yaml):
    server = build_server(_bridge())
    out = await _call(server, "aegis_config_add_queue",
                      name="designs", agent="nope", max_parallel=1)
    assert "error" in out


@pytest.mark.asyncio
async def test_config_add_queue_duplicate_returns_error(tmp_path,
                                                        monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  r:\n    provider: claude-code\n    model: opus\n"
        "default_agent: r\n"
        "queues:\n  designs:\n    agent: r\n    max_parallel: 1\n"
    )
    monkeypatch.chdir(tmp_path)
    server = build_server(_bridge())
    out = await _call(server, "aegis_config_add_queue",
                      name="designs", agent="r", max_parallel=2)
    assert "error" in out
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_mcp_config_tools.py -v -k add_queue
```

- [ ] **Step 3: Add the tool right after `aegis_config_remove_agent`:**

```python
    @server.tool
    async def aegis_config_add_queue(
        name: str, agent: str, max_parallel: int,
        budgets: list[dict] | None = None,
    ) -> dict:
        """Add a queue to .aegis.yaml. Hot-registers on the live
        QueueManager so subsequent aegis_enqueue calls can target it."""
        from aegis.config import ConfigError, find_project_root, load_queues
        from aegis.config.edit import add_queue as _add

        root = find_project_root()
        if root is None:
            return {"error": "no .aegis.yaml found"}
        async with config_write_lock:
            try:
                _add(root, name, agent=agent, max_parallel=max_parallel,
                     budgets=budgets)
            except ConfigError as e:
                return {"error": str(e)}
            try:
                fresh_queues = load_queues(root)
                queue = fresh_queues[name]
                bridge.register_queue(queue)
            except Exception as e:                       # noqa: BLE001
                return {"ok": True, "live": False,
                        "restart_required_for": ["queues"],
                        "note": f"persisted but live-register failed: {e}"}
            return {"ok": True, "live": True, "restart_required_for": []}
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/test_mcp_config_tools.py -v -k add_queue
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_mcp_config_tools.py
git commit -m "feat(mcp): aegis_config_add_queue — write + live-register on QueueManager"
```

---

## Task 13: `aegis_config_remove_queue` write tool (persist-only)

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_mcp_config_tools.py`

- [ ] **Step 1: Append tests**

```python
@pytest.mark.asyncio
async def test_config_remove_queue_persists_restart_required(tmp_path,
                                                              monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  r:\n    provider: claude-code\n    model: opus\n"
        "default_agent: r\n"
        "queues:\n  designs:\n    agent: r\n    max_parallel: 1\n"
    )
    monkeypatch.chdir(tmp_path)
    server = build_server(_bridge())
    out = await _call(server, "aegis_config_remove_queue", name="designs")
    assert out == {"ok": True, "live": False,
                   "restart_required_for": ["queues"]}


@pytest.mark.asyncio
async def test_config_remove_queue_unknown_returns_error(root_with_yaml):
    server = build_server(_bridge())
    out = await _call(server, "aegis_config_remove_queue", name="nope")
    assert "error" in out
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_mcp_config_tools.py -v -k remove_queue
```

- [ ] **Step 3: Add the tool right after `aegis_config_add_queue`:**

```python
    @server.tool
    async def aegis_config_remove_queue(name: str) -> dict:
        """Drop a queue from .aegis.yaml. Restart required."""
        from aegis.config import ConfigError, find_project_root
        from aegis.config.edit import remove_queue as _rm
        root = find_project_root()
        if root is None:
            return {"error": "no .aegis.yaml found"}
        async with config_write_lock:
            try:
                _rm(root, name)
            except ConfigError as e:
                return {"error": str(e)}
        return {"ok": True, "live": False,
                "restart_required_for": ["queues"]}
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/test_mcp_config_tools.py -v -k remove_queue
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_mcp_config_tools.py
git commit -m "feat(mcp): aegis_config_remove_queue — persist; restart needed"
```

---

## Task 14: `aegis_config_add_plugin_dir` write tool (live)

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_mcp_config_tools.py`

- [ ] **Step 1: Append tests**

```python
@pytest.mark.asyncio
async def test_config_add_plugin_dir_persists_and_reloads(
        root_with_yaml):
    bridge = _bridge()
    server = build_server(bridge)
    (root_with_yaml / ".aegis" / "plugins").mkdir(parents=True)
    out = await _call(server, "aegis_config_add_plugin_dir",
                      path=".aegis/plugins")
    assert out == {"ok": True, "live": True, "restart_required_for": []}
    bridge.reload_plugins.assert_called_once()


@pytest.mark.asyncio
async def test_config_add_plugin_dir_idempotent(root_with_yaml):
    bridge = _bridge()
    server = build_server(bridge)
    (root_with_yaml / ".aegis" / "plugins").mkdir(parents=True)
    await _call(server, "aegis_config_add_plugin_dir",
                path=".aegis/plugins")
    bridge.reset_mock()
    out = await _call(server, "aegis_config_add_plugin_dir",
                      path=".aegis/plugins")
    # Second add is a no-op on disk; reload still fires (cheap).
    assert out["ok"] is True
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_mcp_config_tools.py -v -k add_plugin_dir
```

- [ ] **Step 3: Add the tool right after `aegis_config_remove_queue`:**

```python
    @server.tool
    async def aegis_config_add_plugin_dir(path: str) -> dict:
        """Register a plugin directory; reloads plugins so any new
        @workflow functions register immediately."""
        from aegis.config import ConfigError, find_project_root
        from aegis.config.edit import add_plugin_dir as _add
        root = find_project_root()
        if root is None:
            return {"error": "no .aegis.yaml found"}
        async with config_write_lock:
            try:
                _add(root, path)
            except ConfigError as e:
                return {"error": str(e)}
            try:
                bridge.reload_plugins()
            except Exception as e:                       # noqa: BLE001
                return {"ok": True, "live": False,
                        "restart_required_for": ["plugins"],
                        "note": f"persisted but reload failed: {e}"}
            return {"ok": True, "live": True, "restart_required_for": []}
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/test_mcp_config_tools.py -v -k add_plugin_dir
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_mcp_config_tools.py
git commit -m "feat(mcp): aegis_config_add_plugin_dir — write + reload_plugins"
```

---

## Task 15: `aegis_config_remove_plugin_dir` write tool (persist-only)

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_mcp_config_tools.py`

- [ ] **Step 1: Append test**

```python
@pytest.mark.asyncio
async def test_config_remove_plugin_dir_persists(tmp_path, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  r:\n    provider: claude-code\n    model: opus\n"
        "default_agent: r\n"
        "plugin_dirs:\n  - .aegis/plugins\n"
    )
    monkeypatch.chdir(tmp_path)
    server = build_server(_bridge())
    out = await _call(server, "aegis_config_remove_plugin_dir",
                      path=".aegis/plugins")
    assert out == {"ok": True, "live": False,
                   "restart_required_for": ["plugins"]}
    yml = (tmp_path / ".aegis.yaml").read_text()
    assert ".aegis/plugins" not in yml
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_mcp_config_tools.py -v -k remove_plugin_dir
```

- [ ] **Step 3: Add the tool right after `aegis_config_add_plugin_dir`:**

```python
    @server.tool
    async def aegis_config_remove_plugin_dir(path: str) -> dict:
        """Drop a plugin_dirs entry. Restart required to fully
        deregister @workflow functions imported from that dir."""
        from aegis.config import ConfigError, find_project_root
        from aegis.config.edit import remove_plugin_dir as _rm
        root = find_project_root()
        if root is None:
            return {"error": "no .aegis.yaml found"}
        async with config_write_lock:
            try:
                _rm(root, path)
            except ConfigError as e:
                return {"error": str(e)}
        return {"ok": True, "live": False,
                "restart_required_for": ["plugins"]}
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/test_mcp_config_tools.py -v -k remove_plugin_dir
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_mcp_config_tools.py
git commit -m "feat(mcp): aegis_config_remove_plugin_dir — persist; restart needed"
```

---

## Task 16: `aegis_config_set_schedule_enabled` write tool (live via ReloadWatcher)

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_mcp_config_tools.py`

- [ ] **Step 1: Append test**

```python
@pytest.mark.asyncio
async def test_config_set_schedule_enabled_toggles(tmp_path, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  r:\n    provider: claude-code\n    model: opus\n"
        "default_agent: r\n"
        "schedules:\n"
        "  morning:\n    cron: '0 6 * * *'\n    workflow: prompt\n"
        "    payload: hi\n    enabled: true\n"
    )
    monkeypatch.chdir(tmp_path)
    server = build_server(_bridge())
    out = await _call(server, "aegis_config_set_schedule_enabled",
                      name="morning", enabled=False)
    assert out == {"ok": True, "live": True, "restart_required_for": [],
                   "enabled": False}
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_mcp_config_tools.py -v -k set_schedule
```

- [ ] **Step 3: Add the tool right after `aegis_config_remove_plugin_dir`:**

```python
    @server.tool
    async def aegis_config_set_schedule_enabled(
        name: str, enabled: bool,
    ) -> dict:
        """Set the enabled flag on a schedule. ReloadWatcher picks the
        change up automatically — no bridge call needed."""
        from aegis.config import find_project_root
        from aegis.config.edit import set_schedule_enabled as _set
        root = find_project_root()
        if root is None:
            return {"error": "no .aegis.yaml found"}
        async with config_write_lock:
            try:
                new_state = _set(root, name, enabled)
            except (FileNotFoundError, KeyError, ValueError) as e:
                return {"error": str(e)}
        return {"ok": True, "live": True, "restart_required_for": [],
                "enabled": new_state}
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/test_mcp_config_tools.py -v -k set_schedule
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_mcp_config_tools.py
git commit -m "feat(mcp): aegis_config_set_schedule_enabled"
```

---

## Task 17: `aegis_config_toggle_schedule_enabled` write tool (live)

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_mcp_config_tools.py`

- [ ] **Step 1: Append test**

```python
@pytest.mark.asyncio
async def test_config_toggle_schedule_enabled_flips(tmp_path, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  r:\n    provider: claude-code\n    model: opus\n"
        "default_agent: r\n"
        "schedules:\n"
        "  morning:\n    cron: '0 6 * * *'\n    workflow: prompt\n"
        "    payload: hi\n    enabled: true\n"
    )
    monkeypatch.chdir(tmp_path)
    server = build_server(_bridge())
    out = await _call(server, "aegis_config_toggle_schedule_enabled",
                      name="morning")
    assert out["ok"] is True
    assert out["enabled"] is False
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_mcp_config_tools.py -v -k toggle_schedule
```

- [ ] **Step 3: Add the tool right after `aegis_config_set_schedule_enabled`:**

```python
    @server.tool
    async def aegis_config_toggle_schedule_enabled(name: str) -> dict:
        """Flip the enabled flag on a schedule. Returns new state."""
        from aegis.config import find_project_root
        from aegis.config.edit import toggle_schedule_enabled as _tog
        root = find_project_root()
        if root is None:
            return {"error": "no .aegis.yaml found"}
        async with config_write_lock:
            try:
                new_state = _tog(root, name)
            except (FileNotFoundError, KeyError, ValueError) as e:
                return {"error": str(e)}
        return {"ok": True, "live": True, "restart_required_for": [],
                "enabled": new_state}
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/test_mcp_config_tools.py -v -k toggle_schedule
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_mcp_config_tools.py
git commit -m "feat(mcp): aegis_config_toggle_schedule_enabled"
```

---

## Task 18: Concurrency test — two writes against one server

**Files:**
- Test: `tests/test_mcp_config_tools.py`

- [ ] **Step 1: Append test**

```python
@pytest.mark.asyncio
async def test_two_add_queue_calls_serialize_safely(root_with_yaml):
    """Two concurrent add_queue calls — both end up in YAML; the file
    parses; no torn state. The asyncio.Lock + _atomic_write together
    guarantee this."""
    import asyncio as _asyncio
    bridge = _bridge()
    server = build_server(bridge)
    async with __import__('fastmcp.client',
                          fromlist=['Client']).Client(server) as client:
        await _asyncio.gather(
            client.call_tool("aegis_config_add_queue",
                             {"name": "a", "agent": "researcher",
                              "max_parallel": 1}),
            client.call_tool("aegis_config_add_queue",
                             {"name": "b", "agent": "researcher",
                              "max_parallel": 1}),
        )
    yml = (root_with_yaml / ".aegis.yaml").read_text()
    assert "a:" in yml and "b:" in yml
    # File still parses:
    from aegis.config.yaml_loader import load_config as _load_yaml
    cfg = _load_yaml(root_with_yaml)
    assert {"a", "b"} <= set(cfg.queues)
```

- [ ] **Step 2: Run to verify pass**

```bash
uv run pytest tests/test_mcp_config_tools.py::test_two_add_queue_calls_serialize_safely -v
```

Expected: PASS. (No new code; this is a check that Task 5's lock + the existing `_atomic_write` give the guarantee the spec promises.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_mcp_config_tools.py
git commit -m "test(mcp): concurrent add_queue calls serialize safely"
```

---

## Task 19: Live integration test — agent creates a queue, enqueues to it

**Files:**
- Modify: `tests/test_mcp_live.py`

- [ ] **Step 1: Inspect the existing live-test pattern**

```bash
grep -n "^def \|^async def \|pytest.mark.live\|@pytest.mark.live" tests/test_mcp_live.py | head -20
```

Read the file end-to-end. The new test must mirror its fixture style (subprocess spawn of `claude -p`, skip-if-missing, etc.).

- [ ] **Step 2: Append a `live`-marked test**

```python
# tests/test_mcp_live.py — appended at the end

@pytest.mark.live
@pytest.mark.asyncio
async def test_agent_creates_queue_then_enqueues_to_it(tmp_path,
                                                       monkeypatch):
    """End-to-end: a claude -p worker calls aegis_config_add_queue, then
    aegis_enqueue against the new queue. Skips when `claude` is off PATH.
    """
    import shutil
    if shutil.which("claude") is None:
        pytest.skip("`claude` CLI not on PATH")
    # … set up tmp_path/.aegis.yaml, build_server, spawn a worker that
    # follows the protocol used by the other tests in this file.
    # Assert: bridge.register_queue was called, the worker's
    # aegis_enqueue returned a task_id (not an error), and the YAML now
    # contains the new queue.
```

The body should follow whatever the existing live tests do for spawning + driving a `claude -p` worker. **Do not invent a new harness pattern.** Read `test_mcp_live.py` and replicate.

- [ ] **Step 3: Run with the live marker**

```bash
uv run pytest tests/test_mcp_live.py::test_agent_creates_queue_then_enqueues_to_it -v
```

Expected: PASS if `claude` is on PATH; SKIP otherwise.

- [ ] **Step 4: Commit**

```bash
git add tests/test_mcp_live.py
git commit -m "test(mcp): live — agent creates queue + enqueues to it"
```

---

## Task 20: Extend `BRIEFING` so the agent knows the new tools exist

**Files:**
- Modify: `src/aegis/mcp/server.py`

- [ ] **Step 1: In `src/aegis/mcp/server.py`, find the `BRIEFING = (...)` block. Add a section after the existing tool descriptions (right before the `INBOX — how messages reach you.` block), inserted as additional string fragments inside the parenthesised expression:**

```python
    "\nCONFIG EDIT — extend the substrate from inside.\n"
    "  - aegis_config_show() : full parsed .aegis.yaml (telegram token redacted).\n"
    "  - aegis_config_list_agents() : configured agent profiles with full metadata "
    "(harness, model, effort, permission).\n"
    "  - aegis_config_list_queues() : configured queues with agent + max_parallel + budgets.\n"
    "  - aegis_config_list_schedules() : configured schedules with cron + enabled + workflow.\n"
    "  - aegis_config_add_agent(slug, harness, model, effort?, permission?) : add a new agent "
    "profile. Hot-registers — the new slug is available to subsequent enqueue / spawn calls "
    "without restart.\n"
    "  - aegis_config_remove_agent(slug) : drop a profile. Persisted but takes effect on next "
    "aegis serve restart.\n"
    "  - aegis_config_add_queue(name, agent, max_parallel, budgets?) : add a queue. "
    "Hot-registers — aegis_enqueue(queue=<name>) works immediately.\n"
    "  - aegis_config_remove_queue(name) : drop a queue. Persisted; restart needed.\n"
    "  - aegis_config_add_plugin_dir(path) : register a directory of @workflow plugins. "
    "Re-imports immediately so new workflows are callable via aegis_run_workflow.\n"
    "  - aegis_config_remove_plugin_dir(path) : drop a plugin dir. Persisted; restart needed.\n"
    "  - aegis_config_set_schedule_enabled(name, enabled) / "
    "aegis_config_toggle_schedule_enabled(name) : flip a schedule on/off. The ReloadWatcher "
    "picks up the change.\n"
    "Every write tool returns {ok, live, restart_required_for} so you know whether the "
    "change is in effect now or needs a restart. Validation failures (unknown harness, "
    "duplicate slug, queue referencing missing agent) come back as {error: ...} — same "
    "wording the human CLI prints at `aegis config …`.\n\n"
```

- [ ] **Step 2: Sanity-check: run the existing `aegis_meta` tests to ensure the briefing still parses cleanly**

```bash
uv run pytest tests/ -q -k meta 2>&1 | tail
```

- [ ] **Step 3: Commit**

```bash
git add src/aegis/mcp/server.py
git commit -m "docs(mcp): extend BRIEFING with the config-edit tool surface"
```

---

## Task 21: Full hermetic suite + push

- [ ] **Step 1: Run the full hermetic suite**

```bash
uv run pytest -q -m "not live"
```

Expected: all pass. If any pre-existing test fails after Task 4's TUI change, it'll be because a test fake of `AppBridge` is missing the new methods — add no-op `register_agent` / `register_queue` / `reload_plugins` stubs to those fakes.

- [ ] **Step 2: Push**

```bash
git push origin main
```

---

## Self-Review

**Spec coverage:**
- ✅ Reads (`aegis_config_show`, `_list_agents`, `_list_queues`, `_list_schedules`) — Tasks 6–9.
- ✅ Writes (`add_agent`, `remove_agent`, `add_queue`, `remove_queue`, `add_plugin_dir`, `remove_plugin_dir`, `set_schedule_enabled`, `toggle_schedule_enabled`) — Tasks 10–17.
- ✅ Live registration via bridge — Tasks 1, 3, 4 + threaded through Tasks 10, 12, 14.
- ✅ `QueueManager.register_queue` — Task 2.
- ✅ Concurrency lock — Task 5 + Task 18 test.
- ✅ `find_project_root` resolution; no `root` parameter — every tool.
- ✅ `{ok, live, restart_required_for}` return shape — verified in every write-tool test.
- ✅ Validation errors bubble as `{error: ...}` — explicit tests in Tasks 10, 11, 12, 13.
- ✅ Live registration failure surfaces as `live: false` + note — Task 10 test.
- ✅ `BRIEFING` extension so agents discover the surface — Task 20.
- ✅ Live integration test — Task 19.
- ❌ Out of scope per spec: `set_telegram`, `set_default_agent`, live removes, dry-run, groups/remotes. Confirmed not in plan.

**Placeholder scan:** Tasks 19 step 2 has a `# …` body comment because the live-harness fixture pattern lives entirely in the existing `test_mcp_live.py` file — replicating it verbatim here would duplicate ~50 lines that already exist in-repo. Step 1 of Task 19 explicitly directs the engineer to that file and forbids inventing a new pattern. This is the one acceptable case of "follow the existing example" since pasting the example would put it out of date the moment the file's harness boilerplate is touched.

**Type consistency:**
- `register_agent(slug, agent)` — same shape in Protocol (Task 1), impl (Task 3), tests (Task 3), and call sites (Tasks 10, 12, 14).
- `register_queue(queue)` — single-arg Queue, consistent across Tasks 2, 3, 12.
- `reload_plugins()` — no-arg, consistent in Tasks 1, 3, 14.
- `Agent(harness=, model=, effort=, permission=)` — flat-shape constructor used in Task 10 matches the existing back-compat path documented in `src/aegis/config/__init__.py:72-109`.
- `Queue` object exposes `agent_profile` (not `agent`) — Task 12's assertion uses `agent_profile`, matching the resolved `Queue` shape from `load_queues` (`src/aegis/config/__init__.py:177-184`).

**Scope:** ~20 tasks, all touching the same subsystem, each producing a single tested commit. Single plan is appropriate; no decomposition needed.
