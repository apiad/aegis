# Aegis Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the Aegis Scheduler substrate end-to-end — declarative YAML config (with drop-in overlays), built-in workflows, scheduled workflow execution, TUI/CLI surfaces, hot reload, and VPS systemd unit — replacing the external `vault/+/jobs/` substrate.

**Architecture:** A new `Scheduler` class runs inside `aegis serve` as a peer to `QueueManager`/`InboxRouter`. Tick loop walks loaded schedules, dispatches eligible ones to `runner.run_workflow`, logs lifecycle events to JSONL. Config moves from `.aegis.py` to `.aegis.yaml` + per-section drop-in overlay folders; plugin discovery from `.aegis/plugins/*.py` stays as-is.

**Tech Stack:** Python 3.13, `ruamel.yaml` (preserves comments on `Space`-pause), `croniter` (cron math), `watchdog` (filesystem watcher), Textual 8.x (TUI tabs), existing aegis primitives (`AgentSession`, `QueueManager`, `WorkflowEngine`, `runner.run_workflow`).

**Spec:** `docs/superpowers/specs/2026-05-25-aegis-scheduler-design.md`.

---

## File Structure

**New files:**

```
src/aegis/config/yaml_loader.py        # YAML parse + overlay merge + plugin loader
src/aegis/workflows/builtins/__init__.py
src/aegis/workflows/builtins/prompt.py # prompt(agent, text) workflow
src/aegis/workflows/builtins/enqueue.py # enqueue(queue, payload, callback) workflow
src/aegis/scheduler/__init__.py
src/aegis/scheduler/scheduler.py       # Scheduler class, tick loop, dispatch
src/aegis/scheduler/cron.py            # cron → next_fire math (FakeClock-friendly)
src/aegis/scheduler/lifecycle.py       # lifecycle exhaustion logic
src/aegis/scheduler/overlap.py         # on_overlap policies
src/aegis/scheduler/persistence.py     # JSONL log + snapshot writer
src/aegis/scheduler/replay.py          # boot-time replay + backfill-once
src/aegis/scheduler/reload.py          # filesystem watcher + atomic reload
src/aegis/scheduler/notify.py          # Telegram notification bridge
src/aegis/cli/schedule.py              # `aegis schedule` subcommands
src/aegis/tui/dashboard_tabs.py        # tabbed Ctrl+D ops console
src/aegis/tui/schedules_panel.py       # Schedules tab bands + actions
scripts/aegis.service                  # systemd --user unit
scripts/install-vps-service.sh         # one-shot installer

tests/test_yaml_loader.py
tests/test_yaml_overlay.py
tests/test_builtin_prompt.py
tests/test_builtin_enqueue.py
tests/test_scheduler_cron.py
tests/test_scheduler_dispatch.py
tests/test_scheduler_lifecycle.py
tests/test_scheduler_overlap.py
tests/test_scheduler_persistence.py
tests/test_scheduler_replay.py
tests/test_scheduler_reload.py
tests/test_cli_schedule.py
tests/test_scheduler_live.py           # @pytest.mark.live — claude e2e
```

**Modified files:**

```
src/aegis/config.py                    # remove .aegis.py loader; delegate to yaml_loader
src/aegis/cli.py                       # wire `aegis schedule` subcommand
src/aegis/tui/app.py                   # mount tabbed dashboard instead of Ctrl+D modal
src/aegis/server/serve.py              # boot scheduler + reload watcher
pyproject.toml                         # add ruamel.yaml, croniter, watchdog deps
.aegis.yaml                            # NEW — repo's own config (replaces .aegis.py)
.aegis.py                              # DELETED
README.md                              # mention scheduler + .aegis.yaml
docs/roadmap.md                        # tick scheduler as shipped
CHANGELOG.md                           # add release entry
AGENTS.md                              # update Layout/Conventions for YAML config
```

---

## Vertical Slice 1 — Declarative YAML config + overlays

End-state: `aegis serve` boots from `.aegis.yaml` + `.aegis/{agents,queues,schedules}/*.yaml` overlays, with plugin auto-import unchanged. `.aegis.py` removed. Existing queue tests pass against YAML config.

### Task 1.1: Add dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add deps**

In `pyproject.toml`'s `dependencies` array (sorted), add:
```toml
"ruamel.yaml>=0.18",
"croniter>=2.0",
"watchdog>=4.0",
```

- [ ] **Step 2: Sync**

Run: `uv sync`
Expected: lockfile updates, no errors.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add ruamel.yaml, croniter, watchdog for scheduler"
```

### Task 1.2: YAML parse — inline-only entries

**Files:**
- Create: `src/aegis/config/__init__.py` (move existing config module under a package), `src/aegis/config/yaml_loader.py`
- Create: `tests/test_yaml_loader.py`

- [ ] **Step 1: Restructure config module into a package**

If `src/aegis/config.py` exists as a single file, move it to `src/aegis/config/__init__.py` first. Re-export all existing names. Run `uv run pytest -q -m "not live"` to confirm no breakage.

- [ ] **Step 2: Write failing test for inline-only YAML**

`tests/test_yaml_loader.py`:
```python
from pathlib import Path
import textwrap
from aegis.config.yaml_loader import load_config


def test_load_inline_only(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(textwrap.dedent("""
        default_agent: claude
        agents:
          claude:
            provider: claude-code
            model: opus
            effort: high
            permission: auto
        queues:
          tasks:
            agent: claude
            max_parallel: 2
    """))
    cfg = load_config(tmp_path)
    assert cfg.default_agent == "claude"
    assert "claude" in cfg.agents
    assert cfg.agents["claude"].provider.model == "opus"
    assert cfg.queues["tasks"].max_parallel == 2
```

- [ ] **Step 3: Run to verify failure**

`uv run pytest tests/test_yaml_loader.py::test_load_inline_only -v`
Expected: import error (`load_config` not defined).

- [ ] **Step 4: Implement `load_config`**

`src/aegis/config/yaml_loader.py`:
```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from ruamel.yaml import YAML

from aegis.config import Agent, ClaudeCode, GeminiCLI, OpenCode  # provider shims


@dataclass
class QueueSpec:
    agent: str
    max_parallel: int = 1


@dataclass
class AegisConfig:
    default_agent: str | None = None
    agents: dict[str, Agent] = field(default_factory=dict)
    queues: dict[str, QueueSpec] = field(default_factory=dict)
    schedules: dict[str, dict[str, Any]] = field(default_factory=dict)
    workflows: list[str] = field(default_factory=list)
    plugin_dirs: list[Path] = field(default_factory=list)
    scheduler: dict[str, Any] = field(default_factory=dict)


_PROVIDERS = {
    "claude-code": ClaudeCode,
    "gemini-cli": GeminiCLI,
    "opencode": OpenCode,
}


def _agent_from_dict(d: dict[str, Any]) -> Agent:
    provider_name = d.pop("provider")
    cls = _PROVIDERS[provider_name]
    return Agent(provider=cls(**d))


def load_config(root: Path) -> AegisConfig:
    yaml = YAML(typ="safe")
    base = root / ".aegis.yaml"
    raw = yaml.load(base.read_text()) or {}
    cfg = AegisConfig(
        default_agent=raw.get("default_agent"),
        agents={k: _agent_from_dict(dict(v)) for k, v in (raw.get("agents") or {}).items()},
        queues={k: QueueSpec(**v) for k, v in (raw.get("queues") or {}).items()},
        schedules=dict(raw.get("schedules") or {}),
        workflows=list(raw.get("workflows") or []),
        plugin_dirs=[root / Path(p) for p in (raw.get("plugin_dirs") or [".aegis/plugins"])],
        scheduler=dict(raw.get("scheduler") or {}),
    )
    return cfg
```

- [ ] **Step 5: Run test to verify pass**

`uv run pytest tests/test_yaml_loader.py::test_load_inline_only -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/config tests/test_yaml_loader.py
git commit -m "feat(config): YAML loader for inline agents/queues"
```

### Task 1.3: Overlay collection — drop-in YAML files

**Files:**
- Modify: `src/aegis/config/yaml_loader.py`
- Create: `tests/test_yaml_overlay.py`

- [ ] **Step 1: Write failing test for overlay-only schedules**

`tests/test_yaml_overlay.py`:
```python
from pathlib import Path
import textwrap
from aegis.config.yaml_loader import load_config


def test_overlay_only_schedules(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text("agents:\n  c:\n    provider: claude-code\n    model: opus\n")
    overlay = tmp_path / ".aegis" / "schedules"
    overlay.mkdir(parents=True)
    (overlay / "end-of-day.yaml").write_text(textwrap.dedent("""
        workflow: prompt
        args: {agent: c, text: hello}
        cron: "0 2 * * *"
        lifecycle: forever
    """))
    cfg = load_config(tmp_path)
    assert "end-of-day" in cfg.schedules
    assert cfg.schedules["end-of-day"]["cron"] == "0 2 * * *"
```

- [ ] **Step 2: Verify failure**

`uv run pytest tests/test_yaml_overlay.py::test_overlay_only_schedules -v`
Expected: assertion fail — `end-of-day` not in schedules.

- [ ] **Step 3: Implement overlay collection**

Append to `src/aegis/config/yaml_loader.py`:
```python
_SECTIONS = ("agents", "queues", "schedules")


def _collect_overlays(root: Path) -> dict[str, dict[str, Any]]:
    """Returns {section: {name: entry_body}} from .aegis/<section>/*.yaml files."""
    yaml = YAML(typ="safe")
    out: dict[str, dict[str, Any]] = {s: {} for s in _SECTIONS}
    for section in _SECTIONS:
        folder = root / ".aegis" / section
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.yaml")):
            name = path.stem
            body = yaml.load(path.read_text()) or {}
            if not isinstance(body, dict):
                raise ValueError(f"overlay {path} must be a mapping at top level")
            out[section][name] = body
    return out
```

And modify `load_config` to merge overlays after parsing the base:
```python
def load_config(root: Path) -> AegisConfig:
    yaml = YAML(typ="safe")
    base = root / ".aegis.yaml"
    raw = yaml.load(base.read_text()) or {}

    inline = {
        "agents": dict(raw.get("agents") or {}),
        "queues": dict(raw.get("queues") or {}),
        "schedules": dict(raw.get("schedules") or {}),
    }
    overlay = _collect_overlays(root)

    merged = {}
    for section in _SECTIONS:
        merged[section] = _merge_or_die(section, inline[section], overlay[section])

    cfg = AegisConfig(
        default_agent=raw.get("default_agent"),
        agents={k: _agent_from_dict(dict(v)) for k, v in merged["agents"].items()},
        queues={k: QueueSpec(**v) for k, v in merged["queues"].items()},
        schedules=merged["schedules"],
        workflows=list(raw.get("workflows") or []),
        plugin_dirs=[root / Path(p) for p in (raw.get("plugin_dirs") or [".aegis/plugins"])],
        scheduler=dict(raw.get("scheduler") or {}),
    )
    return cfg


def _merge_or_die(section: str, inline: dict, overlay: dict) -> dict:
    conflict = set(inline) & set(overlay)
    if conflict:
        raise ConfigError(
            f"{section}: keys appear in both .aegis.yaml and .aegis/{section}/*.yaml: "
            f"{sorted(conflict)}. One source of truth per entry.")
    return {**inline, **overlay}
```

- [ ] **Step 4: Run test to verify pass**

`uv run pytest tests/test_yaml_overlay.py -v`
Expected: PASS.

- [ ] **Step 5: Add conflict test**

Add to `tests/test_yaml_overlay.py`:
```python
import pytest
from aegis.config import ConfigError


def test_conflict_fails_loud(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text(textwrap.dedent("""
        schedules:
          foo: {workflow: prompt, cron: "* * * * *"}
    """))
    overlay = tmp_path / ".aegis" / "schedules"
    overlay.mkdir(parents=True)
    (overlay / "foo.yaml").write_text("workflow: prompt\ncron: '* * * * *'\n")
    with pytest.raises(ConfigError, match="schedules"):
        load_config(tmp_path)
```

Run: `uv run pytest tests/test_yaml_overlay.py -v`. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/config/yaml_loader.py tests/test_yaml_overlay.py
git commit -m "feat(config): drop-in overlay folders with fail-loud conflict"
```

### Task 1.4: Plugin auto-import

**Files:**
- Modify: `src/aegis/config/yaml_loader.py`
- Create: `tests/test_plugin_import.py`

- [ ] **Step 1: Failing test**

```python
from pathlib import Path
from aegis.config.yaml_loader import load_config, import_plugins


def test_plugin_registers_workflow(tmp_path: Path) -> None:
    (tmp_path / ".aegis.yaml").write_text("plugin_dirs: [.aegis/plugins]\n")
    plugins = tmp_path / ".aegis" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "myhook.py").write_text(
        "from aegis.workflow import workflow\n"
        "@workflow\nasync def my_test_wf(engine): return 'ok'\n")
    cfg = load_config(tmp_path)
    import_plugins(cfg)
    from aegis.workflow.decorator import _REGISTRY
    assert "my_test_wf" in _REGISTRY
```

- [ ] **Step 2: Verify fail**

`uv run pytest tests/test_plugin_import.py -v`. Expected: `import_plugins` not defined.

- [ ] **Step 3: Implement**

Append to `src/aegis/config/yaml_loader.py`:
```python
import importlib.util
import sys
from typing import Iterable


def import_plugins(cfg: AegisConfig) -> None:
    for d in cfg.plugin_dirs:
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.py")):
            spec = importlib.util.spec_from_file_location(
                f"aegis_plugin_{path.stem}", path)
            if spec is None or spec.loader is None:
                raise ImportError(f"could not load plugin {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
```

- [ ] **Step 4: Verify pass + commit**

```bash
uv run pytest tests/test_plugin_import.py -v
git add src/aegis/config/yaml_loader.py tests/test_plugin_import.py
git commit -m "feat(config): plugin auto-import from .aegis/plugins/*.py"
```

### Task 1.5: Workflow registration via YAML `workflows:` list

**Files:**
- Modify: `src/aegis/config/yaml_loader.py`
- Create: `src/aegis/workflows/builtins/__init__.py` (stub for now)

- [ ] **Step 1: Stub built-ins module**

`src/aegis/workflows/builtins/__init__.py`:
```python
"""Built-in aegis workflows. Names are registered by importing the
relevant submodule when listed in `.aegis.yaml`'s `workflows:` field."""
```

- [ ] **Step 2: Failing test**

`tests/test_yaml_loader.py` add:
```python
def test_workflows_list_imports_builtins(tmp_path: Path, monkeypatch) -> None:
    from aegis.workflow.decorator import _REGISTRY
    _REGISTRY.clear()
    (tmp_path / ".aegis.yaml").write_text("workflows: [prompt]\n")
    cfg = load_config(tmp_path)
    from aegis.config.yaml_loader import register_builtins
    register_builtins(cfg)
    assert "prompt" in _REGISTRY
```

- [ ] **Step 3: Implement**

Append to `src/aegis/config/yaml_loader.py`:
```python
def register_builtins(cfg: AegisConfig) -> None:
    """Import each name in cfg.workflows from aegis.workflows.builtins."""
    for name in cfg.workflows:
        try:
            importlib.import_module(f"aegis.workflows.builtins.{name}")
        except ModuleNotFoundError as e:
            raise ConfigError(
                f"workflows list references unknown built-in: {name!r}") from e
```

Test will still fail because `prompt` builtin doesn't exist yet — defer that to VS2. Mark this test with `pytest.mark.skip(reason="prompt builtin lands in VS2")` for now.

- [ ] **Step 4: Commit**

```bash
git add src/aegis/config/yaml_loader.py src/aegis/workflows/builtins/__init__.py tests/test_yaml_loader.py
git commit -m "feat(config): register_builtins() — wires workflows: list to imports"
```

### Task 1.6: Replace `.aegis.py` boot path with YAML loader

**Files:**
- Modify: `src/aegis/cli.py`, `src/aegis/server/serve.py` (or wherever `.aegis.py` is currently loaded)

- [ ] **Step 1: Find the current `.aegis.py` loader**

Run: `grep -rn "\.aegis\.py\|aegis_py\|find_project_root" src/aegis/ | head -20`. Identify the function that loads it (likely in `src/aegis/config/__init__.py` or `src/aegis/cli.py`).

- [ ] **Step 2: Replace with YAML loader**

In the identified file, replace the `.aegis.py` loader call with:
```python
from aegis.config.yaml_loader import load_config, import_plugins, register_builtins

def load_project_config(root: Path) -> AegisConfig:
    cfg = load_config(root)
    register_builtins(cfg)
    import_plugins(cfg)
    return cfg
```

Update `find_project_root()` to look for `.aegis.yaml` instead of `.aegis.py`.

- [ ] **Step 3: Migrate this repo's own config**

Write `/.aegis.yaml` (in the aegis repo root) matching the current `.aegis.py`:
```yaml
default_agent: claude
agents:
  claude:
    provider: claude-code
    model: opus
    effort: high
    permission: auto
  glm:
    provider: opencode
    model: opencode/glm-5.1
    permission: full
  gemini-flash:
    provider: gemini-cli
    model: gemini-3-flash-preview
    permission: full
  gemini:
    provider: gemini-cli
    model: gemini-3.1-pro-preview
    permission: full
queues:
  tasks:
    agent: gemini-flash
    max_parallel: 2
plugin_dirs: [.aegis/plugins]
workflows: []     # populated in VS2
```

Delete `.aegis.py`.

- [ ] **Step 4: Verify boot still works**

Run: `uv run aegis --help` and `uv run aegis init --dry-run` (or equivalent smoke). Expected: no traceback.

- [ ] **Step 5: Update tests that mocked .aegis.py**

Run: `grep -rn '\.aegis\.py' tests/`. For each test that wrote a fixture `.aegis.py`, port it to write `.aegis.yaml` instead, using the YAML shape from `.aegis.yaml` above.

Run: `uv run pytest -q -m "not live"`. Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(config): boot from .aegis.yaml; remove .aegis.py shim"
```

---

## Vertical Slice 2 — Built-in workflows

End-state: `prompt(agent, text)` and `enqueue(queue, payload, callback)` registered, runnable via `aegis workflow run`.

### Task 2.1: `prompt` built-in

**Files:**
- Create: `src/aegis/workflows/builtins/prompt.py`
- Create: `tests/test_builtin_prompt.py`

- [ ] **Step 1: Failing test**

`tests/test_builtin_prompt.py`:
```python
import pytest
from unittest.mock import AsyncMock
from aegis.workflows.builtins.prompt import prompt


@pytest.mark.asyncio
async def test_prompt_spawns_sends_drains_returns(monkeypatch):
    engine = AsyncMock()
    engine.spawn.return_value = "fake-handle"
    engine.last_assistant_text.return_value = "final result"
    result = await prompt(engine, agent="claude", text="hi")
    assert result == "final result"
    engine.spawn.assert_awaited_with("claude")
    engine.send.assert_awaited_with("fake-handle", "hi")
    engine.drain.assert_awaited_with("fake-handle")
    engine.close.assert_awaited_with("fake-handle")
```

- [ ] **Step 2: Verify fail**

`uv run pytest tests/test_builtin_prompt.py -v`. Expected: import error.

- [ ] **Step 3: Implement**

`src/aegis/workflows/builtins/prompt.py`:
```python
from __future__ import annotations
from aegis.workflow import workflow


@workflow
async def prompt(engine, *, agent: str, text: str) -> str:
    """Spawn an agent of the named profile, send `text` as the opening
    message, drain to completion, return the final assistant text."""
    handle = await engine.spawn(agent)
    try:
        await engine.send(handle, text)
        await engine.drain(handle)
        return await engine.last_assistant_text(handle)
    finally:
        await engine.close(handle)
```

If `engine.last_assistant_text` doesn't exist, add it to `WorkflowEngine` in `src/aegis/workflow/engine.py`: look up the agent's `AgentSession`, return its last `AssistantText` event payload.

- [ ] **Step 4: Verify pass + un-skip Task 1.5 test**

```bash
uv run pytest tests/test_builtin_prompt.py tests/test_yaml_loader.py::test_workflows_list_imports_builtins -v
```
Both should PASS now (the Task 1.5 skip can be removed).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/workflows/builtins/prompt.py src/aegis/workflow tests/
git commit -m "feat(workflows): prompt builtin — spawn → send → drain → text → close"
```

### Task 2.2: `enqueue` built-in

**Files:**
- Create: `src/aegis/workflows/builtins/enqueue.py`
- Create: `tests/test_builtin_enqueue.py`

- [ ] **Step 1: Failing test**

```python
import pytest
from unittest.mock import AsyncMock
from aegis.workflows.builtins.enqueue import enqueue


@pytest.mark.asyncio
async def test_enqueue_fire_and_forget():
    engine = AsyncMock()
    engine.enqueue.return_value = "task-id-123"
    result = await enqueue(engine, queue="tasks", payload="do thing", callback=False)
    assert result == "task-id-123"
    engine.enqueue.assert_awaited_with("tasks", "do thing", callback=False)


@pytest.mark.asyncio
async def test_enqueue_callback_awaits_result():
    engine = AsyncMock()
    engine.enqueue.return_value = "task-id-123"
    engine.await_callback.return_value = "worker said hi"
    result = await enqueue(engine, queue="tasks", payload="do thing", callback=True)
    assert result == "worker said hi"
    engine.await_callback.assert_awaited_with("task-id-123")
```

- [ ] **Step 2: Implement**

`src/aegis/workflows/builtins/enqueue.py`:
```python
from __future__ import annotations
from aegis.workflow import workflow


@workflow
async def enqueue(engine, *, queue: str, payload: str, callback: bool = False) -> str:
    """Drop a task onto a queue. Returns task_id (fire-and-forget) or
    the worker's final text (callback=True)."""
    task_id = await engine.enqueue(queue, payload, callback=callback)
    if not callback:
        return task_id
    return await engine.await_callback(task_id)
```

Wire `engine.enqueue` and `engine.await_callback` onto `WorkflowEngine` in `src/aegis/workflow/engine.py` if not already there. They delegate to `QueueManager.enqueue` and a new `await_callback(task_id)` that blocks until the task hits a terminal state and returns its `result` (or raises on failure).

- [ ] **Step 3: Verify pass**

`uv run pytest tests/test_builtin_enqueue.py -v`. Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/aegis/workflows/builtins/enqueue.py src/aegis/workflow tests/test_builtin_enqueue.py
git commit -m "feat(workflows): enqueue builtin — fire-and-forget or await callback"
```

### Task 2.3: Update repo's `.aegis.yaml` to register built-ins

**Files:**
- Modify: `.aegis.yaml`

- [ ] **Step 1: Add `workflows:` list**

In `.aegis.yaml`, set:
```yaml
workflows: [prompt, enqueue]
```

- [ ] **Step 2: Smoke**

Run: `uv run aegis workflow list`. Expected: `prompt`, `enqueue` appear.

- [ ] **Step 3: Commit**

```bash
git add .aegis.yaml
git commit -m "config: register prompt + enqueue built-in workflows"
```

---

## Vertical Slice 3 — Minimum-viable scheduler

End-state: cron-triggered schedules with `lifecycle: forever` and `on_overlap: skip` fire the `prompt` workflow. JSONL log + snapshot persisted. Live test against `claude`.

### Task 3.1: `Scheduler` skeleton + FakeClock

**Files:**
- Create: `src/aegis/scheduler/__init__.py`, `src/aegis/scheduler/scheduler.py`, `src/aegis/scheduler/clock.py`
- Create: `tests/conftest.py` (or extend existing)

- [ ] **Step 1: FakeClock fixture**

`src/aegis/scheduler/clock.py`:
```python
from __future__ import annotations
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        from datetime import timedelta
        self._now += timedelta(**kwargs)
```

- [ ] **Step 2: `Scheduler` class skeleton**

`src/aegis/scheduler/scheduler.py`:
```python
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Awaitable
from aegis.scheduler.clock import Clock, SystemClock


@dataclass
class SchedulerConfig:
    tick_seconds: int = 60
    default_timezone: str = "UTC"


class Scheduler:
    def __init__(
        self,
        *,
        schedules: dict[str, dict[str, Any]],
        state_dir: Path,
        run_workflow: Callable[..., Awaitable[Any]],
        clock: Clock | None = None,
        cfg: SchedulerConfig | None = None,
    ) -> None:
        self.schedules = schedules
        self.state_dir = state_dir
        self.run_workflow = run_workflow
        self.clock = clock or SystemClock()
        self.cfg = cfg or SchedulerConfig()
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    async def tick(self) -> None:
        """One tick — walk schedules, dispatch eligible. Public for tests."""
        # implemented in Task 3.3

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopped.set()
        if self._task:
            await self._task

    async def _run(self) -> None:
        while not self._stopped.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(
                    self._stopped.wait(), timeout=self.cfg.tick_seconds)
            except asyncio.TimeoutError:
                pass
```

- [ ] **Step 3: Commit**

```bash
git add src/aegis/scheduler
git commit -m "feat(scheduler): Scheduler class skeleton + FakeClock"
```

### Task 3.2: Cron → `next_fire`

**Files:**
- Create: `src/aegis/scheduler/cron.py`
- Create: `tests/test_scheduler_cron.py`

- [ ] **Step 1: Failing test**

```python
from datetime import datetime, timezone
from aegis.scheduler.cron import next_fire


def test_next_fire_simple():
    now = datetime(2026, 5, 25, 1, 30, tzinfo=timezone.utc)
    # cron "0 2 * * *" — next 02:00 same day
    nxt = next_fire("0 2 * * *", "UTC", now)
    assert nxt == datetime(2026, 5, 25, 2, 0, tzinfo=timezone.utc)


def test_next_fire_timezone():
    now = datetime(2026, 5, 25, 1, 30, tzinfo=timezone.utc)
    # 02:00 America/Havana (UTC-4) = 06:00 UTC
    nxt = next_fire("0 2 * * *", "America/Havana", now)
    assert nxt == datetime(2026, 5, 25, 6, 0, tzinfo=timezone.utc)
```

- [ ] **Step 2: Implement**

`src/aegis/scheduler/cron.py`:
```python
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
from croniter import croniter


def next_fire(cron_expr: str, tz: str, after: datetime) -> datetime:
    """Return next fire time (UTC) for a cron expression evaluated in tz."""
    local = after.astimezone(ZoneInfo(tz))
    it = croniter(cron_expr, local)
    nxt_local = it.get_next(datetime)
    return nxt_local.astimezone(after.tzinfo)
```

- [ ] **Step 3: Verify pass + commit**

```bash
uv run pytest tests/test_scheduler_cron.py -v
git add src/aegis/scheduler/cron.py tests/test_scheduler_cron.py
git commit -m "feat(scheduler): cron → next_fire with timezone support"
```

### Task 3.3: Tick — fire-eligibility + dispatch

**Files:**
- Modify: `src/aegis/scheduler/scheduler.py`
- Create: `tests/test_scheduler_dispatch.py`

- [ ] **Step 1: Failing test**

```python
import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from pathlib import Path
from aegis.scheduler.scheduler import Scheduler
from aegis.scheduler.clock import FakeClock


@pytest.mark.asyncio
async def test_tick_dispatches_eligible_schedule(tmp_path: Path):
    run_workflow = AsyncMock(return_value="ok")
    clock = FakeClock(datetime(2026, 5, 25, 2, 0, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={
            "eod": {
                "workflow": "prompt",
                "args": {"agent": "c", "text": "hi"},
                "cron": "0 2 * * *",
                "timezone": "UTC",
                "lifecycle": "forever",
                "on_overlap": "skip",
                "timeout": 60,
                "enabled": True,
            }
        },
        state_dir=tmp_path,
        run_workflow=run_workflow,
        clock=clock,
    )
    await sched.tick()
    # First tick computes next_fire = 02:00; now == 02:00 → fire eligible.
    await asyncio.sleep(0.05)  # let dispatched task complete
    run_workflow.assert_awaited_with("prompt", {"agent": "c", "text": "hi"})
```

- [ ] **Step 2: Implement tick**

In `src/aegis/scheduler/scheduler.py`, replace `tick` and add helpers:
```python
import json
from datetime import datetime
from aegis.scheduler.cron import next_fire as compute_next_fire
from ulid import ULID  # already used by queue substrate; if not, switch to uuid.uuid4().hex


class Scheduler:
    # ... existing __init__ ...

    def __post_init_state(self):
        self._next_fire: dict[str, datetime] = {}
        self._in_flight: set[str] = set()
        for name, entry in self.schedules.items():
            tz = entry.get("timezone", self.cfg.default_timezone)
            self._next_fire[name] = compute_next_fire(
                entry["cron"], tz, self.clock.now())

    async def tick(self) -> None:
        if not hasattr(self, "_next_fire"):
            self.__post_init_state()
        now = self.clock.now()
        for name, entry in self.schedules.items():
            if not entry.get("enabled", True):
                continue
            if name in self._in_flight and entry.get("on_overlap", "skip") == "skip":
                continue
            if self._next_fire[name] > now:
                continue
            asyncio.create_task(self._fire(name, entry))
            tz = entry.get("timezone", self.cfg.default_timezone)
            self._next_fire[name] = compute_next_fire(entry["cron"], tz, now)

    async def _fire(self, name: str, entry: dict) -> None:
        task_id = str(ULID())
        self._in_flight.add(name)
        self._append_jsonl(name, {
            "ts": self.clock.now().isoformat(),
            "schedule": name,
            "event": "fire_requested",
            "task_id": task_id,
            "manual": False,
            "backfilled": False,
        })
        try:
            timeout = entry.get("timeout", 1800)
            result = await asyncio.wait_for(
                self.run_workflow(entry["workflow"], entry.get("args", {})),
                timeout=timeout)
            self._append_jsonl(name, {
                "ts": self.clock.now().isoformat(),
                "schedule": name, "event": "fire_completed",
                "task_id": task_id, "status": "ok",
                "result_excerpt": str(result)[:500],
            })
        except asyncio.TimeoutError:
            self._append_jsonl(name, {
                "ts": self.clock.now().isoformat(),
                "schedule": name, "event": "fire_failed",
                "task_id": task_id, "status": "failed:timeout",
            })
        except Exception as e:
            self._append_jsonl(name, {
                "ts": self.clock.now().isoformat(),
                "schedule": name, "event": "fire_failed",
                "task_id": task_id, "status": "failed:crash",
                "error": repr(e),
            })
        finally:
            self._in_flight.discard(name)

    def _append_jsonl(self, name: str, record: dict) -> None:
        path = self.state_dir / "schedules" / f"{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(record) + "\n")
```

- [ ] **Step 3: Verify pass + commit**

```bash
uv run pytest tests/test_scheduler_dispatch.py -v
git add src/aegis/scheduler tests/test_scheduler_dispatch.py
git commit -m "feat(scheduler): tick — fire-eligibility + dispatch + JSONL log"
```

### Task 3.4: Snapshot writer

**Files:**
- Modify: `src/aegis/scheduler/scheduler.py`
- Create: `tests/test_scheduler_persistence.py`

- [ ] **Step 1: Failing test for atomic snapshot write**

```python
import json
from pathlib import Path
import pytest
from datetime import datetime, timezone
from aegis.scheduler.scheduler import Scheduler
from aegis.scheduler.clock import FakeClock


@pytest.mark.asyncio
async def test_snapshot_written_after_tick(tmp_path: Path):
    async def noop(*a, **kw): return "ok"
    sched = Scheduler(
        schedules={"eod": {"workflow": "prompt", "args": {},
                            "cron": "0 2 * * *", "lifecycle": "forever",
                            "on_overlap": "skip", "timeout": 60, "enabled": True}},
        state_dir=tmp_path,
        run_workflow=noop,
        clock=FakeClock(datetime(2026, 5, 25, 2, 0, tzinfo=timezone.utc)),
    )
    await sched.tick()
    snap_path = tmp_path / "schedules.snapshot.json"
    assert snap_path.exists()
    snap = json.loads(snap_path.read_text())
    assert "eod" in snap
    assert "next_fire" in snap["eod"]
```

- [ ] **Step 2: Implement**

In `Scheduler.tick`, after the dispatch loop:
```python
self._write_snapshot()
```

And add:
```python
def _write_snapshot(self) -> None:
    snap = {
        name: {
            "next_fire": self._next_fire[name].isoformat(),
            "in_flight": name in self._in_flight,
        }
        for name in self.schedules
    }
    path = self.state_dir / "schedules.snapshot.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, indent=2))
    tmp.replace(path)  # atomic on POSIX
```

- [ ] **Step 3: Verify + commit**

```bash
uv run pytest tests/test_scheduler_persistence.py -v
git add src/aegis/scheduler tests/test_scheduler_persistence.py
git commit -m "feat(scheduler): atomic snapshot writer"
```

### Task 3.5: Wire scheduler into `aegis serve`

**Files:**
- Modify: `src/aegis/server/serve.py` (or wherever `aegis serve` lives)

- [ ] **Step 1: Locate serve entrypoint**

`grep -rn "aegis serve\|def serve\|serve.py" src/aegis/`. Identify the function that wires up `QueueManager`, `MCP server`, Telegram bot.

- [ ] **Step 2: Wire Scheduler**

In the serve entrypoint, after the QueueManager is wired:
```python
from aegis.scheduler.scheduler import Scheduler, SchedulerConfig
from aegis.workflow.runner import run_workflow

scheduler = Scheduler(
    schedules=cfg.schedules,
    state_dir=root / ".aegis" / "state",
    run_workflow=lambda name, args: run_workflow(name, args, caller_handle=None),
    cfg=SchedulerConfig(
        tick_seconds=cfg.scheduler.get("tick_seconds", 60),
        default_timezone=cfg.scheduler.get("default_timezone", "UTC"),
    ),
)
await scheduler.start()
try:
    # ... existing serve loop ...
    pass
finally:
    await scheduler.stop()
```

- [ ] **Step 3: Smoke**

`uv run aegis serve --help` — should still work.

- [ ] **Step 4: Commit**

```bash
git add src/aegis/server
git commit -m "feat(scheduler): wire Scheduler into aegis serve"
```

### Task 3.6: Live e2e test

**Files:**
- Create: `tests/test_scheduler_live.py`

- [ ] **Step 1: Write live test**

```python
import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
import pytest
import textwrap


pytestmark = pytest.mark.live


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude not on PATH")
def test_scheduler_fires_prompt_workflow_live(tmp_path: Path):
    (tmp_path / ".aegis.yaml").write_text(textwrap.dedent("""
        default_agent: c
        agents:
          c: {provider: claude-code, model: opus, effort: low, permission: auto}
        workflows: [prompt]
        scheduler: {tick_seconds: 5}
        schedules:
          ping:
            workflow: prompt
            args: {agent: c, text: "Reply with the single word PONG and nothing else."}
            cron: "* * * * *"
            timezone: UTC
            lifecycle: forever
            on_overlap: skip
            timeout: 60
    """))
    proc = subprocess.Popen(
        ["uv", "run", "aegis", "serve"],
        cwd=tmp_path,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        # Wait up to 120s for a fire_completed
        import time
        log = tmp_path / ".aegis" / "state" / "schedules" / "ping.jsonl"
        deadline = time.time() + 120
        while time.time() < deadline:
            if log.exists():
                lines = log.read_text().splitlines()
                if any(json.loads(l).get("event") == "fire_completed" for l in lines):
                    return  # success
            time.sleep(2)
        pytest.fail("no fire_completed within 120s")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
```

- [ ] **Step 2: Run live test**

`uv run pytest tests/test_scheduler_live.py -v -m live`
Expected: PASS within 120s.

- [ ] **Step 3: Commit**

```bash
git add tests/test_scheduler_live.py
git commit -m "test(scheduler): live e2e — cron fires prompt workflow against claude"
```

---

## Vertical Slice 4 — Full schedule semantics

End-state: `fire_at`, `lifecycle: once|{fires:N}|{until:<iso>}`, `on_overlap: queue|kill`, `timeout`, `notify`, backfill-once, on-boot replay all work.

### Task 4.1: `fire_at` one-shot trigger

**Files:**
- Modify: `src/aegis/scheduler/scheduler.py`

- [ ] **Step 1: Failing test**

In `tests/test_scheduler_dispatch.py`:
```python
@pytest.mark.asyncio
async def test_fire_at_one_shot(tmp_path: Path):
    run_workflow = AsyncMock(return_value="ok")
    clock = FakeClock(datetime(2026, 5, 25, 14, 0, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={
            "oneoff": {
                "workflow": "prompt", "args": {},
                "fire_at": "2026-05-25T14:00:00+00:00",
                "lifecycle": "once", "on_overlap": "skip",
                "timeout": 60, "enabled": True,
            }
        },
        state_dir=tmp_path, run_workflow=run_workflow, clock=clock,
    )
    await sched.tick()
    await asyncio.sleep(0.05)
    run_workflow.assert_awaited_once()
    # Second tick at later time should not fire again (lifecycle: once exhausted).
    clock.advance(hours=1)
    await sched.tick()
    await asyncio.sleep(0.05)
    assert run_workflow.await_count == 1
```

- [ ] **Step 2: Implement**

In `Scheduler.__post_init_state`, handle `fire_at`:
```python
for name, entry in self.schedules.items():
    if "fire_at" in entry:
        self._next_fire[name] = datetime.fromisoformat(entry["fire_at"])
    else:
        tz = entry.get("timezone", self.cfg.default_timezone)
        self._next_fire[name] = compute_next_fire(
            entry["cron"], tz, self.clock.now())
```

In `Scheduler.tick`, after fire dispatch, only recompute `next_fire` if `cron` is present:
```python
if "cron" in entry:
    tz = entry.get("timezone", self.cfg.default_timezone)
    self._next_fire[name] = compute_next_fire(entry["cron"], tz, now)
else:
    self._next_fire[name] = datetime.max.replace(tzinfo=now.tzinfo)
```

And handle `lifecycle` (Task 4.2 covers this fully — for now, stub `once` exhaustion).

- [ ] **Step 3: Verify + commit**

```bash
uv run pytest tests/test_scheduler_dispatch.py::test_fire_at_one_shot -v
git add src/aegis/scheduler tests/
git commit -m "feat(scheduler): fire_at one-shot trigger"
```

### Task 4.2: Lifecycle exhaustion — `once`, `{fires: N}`, `{until: <iso>}`

**Files:**
- Create: `src/aegis/scheduler/lifecycle.py`
- Create: `tests/test_scheduler_lifecycle.py`

- [ ] **Step 1: Failing tests for each lifecycle form**

```python
from datetime import datetime, timezone
from aegis.scheduler.lifecycle import is_exhausted


def test_lifecycle_forever():
    assert not is_exhausted("forever", fire_count=1000, now=datetime.now(timezone.utc))


def test_lifecycle_once():
    assert not is_exhausted("once", fire_count=0, now=datetime.now(timezone.utc))
    assert is_exhausted("once", fire_count=1, now=datetime.now(timezone.utc))


def test_lifecycle_fires_n():
    assert not is_exhausted({"fires": 3}, fire_count=2, now=datetime.now(timezone.utc))
    assert is_exhausted({"fires": 3}, fire_count=3, now=datetime.now(timezone.utc))


def test_lifecycle_until():
    until = "2026-05-25T12:00:00+00:00"
    before = datetime(2026, 5, 25, 11, 0, tzinfo=timezone.utc)
    after = datetime(2026, 5, 25, 13, 0, tzinfo=timezone.utc)
    assert not is_exhausted({"until": until}, fire_count=0, now=before)
    assert is_exhausted({"until": until}, fire_count=0, now=after)
```

- [ ] **Step 2: Implement**

`src/aegis/scheduler/lifecycle.py`:
```python
from __future__ import annotations
from datetime import datetime
from typing import Any


def is_exhausted(lifecycle: Any, *, fire_count: int, now: datetime) -> bool:
    if lifecycle == "forever":
        return False
    if lifecycle == "once":
        return fire_count >= 1
    if isinstance(lifecycle, dict):
        if "fires" in lifecycle:
            return fire_count >= int(lifecycle["fires"])
        if "until" in lifecycle:
            return now > datetime.fromisoformat(lifecycle["until"])
    raise ValueError(f"invalid lifecycle: {lifecycle!r}")
```

- [ ] **Step 3: Wire into tick**

In `Scheduler.tick`, before dispatch:
```python
from aegis.scheduler.lifecycle import is_exhausted
if is_exhausted(entry.get("lifecycle", "forever"),
                fire_count=self._fire_count.get(name, 0),
                now=now):
    continue
```

Add `self._fire_count: dict[str, int] = {}` to `__post_init_state` and increment in `_fire` after the terminal JSONL record.

- [ ] **Step 4: Verify + commit**

```bash
uv run pytest tests/test_scheduler_lifecycle.py tests/test_scheduler_dispatch.py -v
git add src/aegis/scheduler tests/test_scheduler_lifecycle.py
git commit -m "feat(scheduler): lifecycle exhaustion (forever|once|fires:N|until:<iso>)"
```

### Task 4.3: `on_overlap: queue` — per-schedule deferral

**Files:**
- Modify: `src/aegis/scheduler/scheduler.py`
- Create: `tests/test_scheduler_overlap.py`

- [ ] **Step 1: Failing test**

```python
import asyncio
import pytest
from datetime import datetime, timezone
from pathlib import Path
from aegis.scheduler.scheduler import Scheduler
from aegis.scheduler.clock import FakeClock


@pytest.mark.asyncio
async def test_on_overlap_queue_runs_after_first(tmp_path: Path):
    started = []
    completed = []
    async def slow(name, args):
        started.append(name)
        await asyncio.sleep(0.1)
        completed.append(name)
        return "ok"
    clock = FakeClock(datetime(2026, 5, 25, 2, 0, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={
            "eod": {
                "workflow": "prompt", "args": {},
                "cron": "* * * * *", "timezone": "UTC",
                "lifecycle": "forever", "on_overlap": "queue",
                "timeout": 60, "enabled": True,
            }
        },
        state_dir=tmp_path, run_workflow=slow, clock=clock,
    )
    await sched.tick()  # fire 1
    clock.advance(minutes=1)
    await sched.tick()  # fire 2 — first still running; queue it
    await asyncio.sleep(0.3)
    assert started == ["prompt", "prompt"]
    assert completed == ["prompt", "prompt"]
```

- [ ] **Step 2: Implement**

In `Scheduler`, add per-schedule deferral list:
```python
self._deferred: dict[str, list[dict]] = {}
```

In `tick`, when overlapping with `on_overlap: queue`:
```python
if name in self._in_flight:
    policy = entry.get("on_overlap", "skip")
    if policy == "skip":
        self._append_jsonl(name, {..., "event": "skipped:overlap"})
        continue
    elif policy == "queue":
        self._deferred.setdefault(name, []).append(entry)
        continue
    elif policy == "kill":
        # handled in Task 4.4
        pass
```

In `_fire`'s `finally`, after `self._in_flight.discard(name)`:
```python
if self._deferred.get(name):
    next_entry = self._deferred[name].pop(0)
    asyncio.create_task(self._fire(name, next_entry))
```

- [ ] **Step 3: Verify + commit**

```bash
uv run pytest tests/test_scheduler_overlap.py::test_on_overlap_queue_runs_after_first -v
git add src/aegis/scheduler tests/test_scheduler_overlap.py
git commit -m "feat(scheduler): on_overlap=queue per-schedule deferral"
```

### Task 4.4: `on_overlap: kill`

**Files:**
- Modify: `src/aegis/scheduler/scheduler.py`
- Modify: `tests/test_scheduler_overlap.py`

- [ ] **Step 1: Failing test**

```python
@pytest.mark.asyncio
async def test_on_overlap_kill_cancels_prior(tmp_path: Path):
    cancelled = []
    async def slow(name, args):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(name)
            raise
        return "ok"
    clock = FakeClock(datetime(2026, 5, 25, 2, 0, tzinfo=timezone.utc))
    sched = Scheduler(
        schedules={"x": {
            "workflow": "prompt", "args": {}, "cron": "* * * * *",
            "timezone": "UTC", "lifecycle": "forever",
            "on_overlap": "kill", "timeout": 60, "enabled": True,
        }},
        state_dir=tmp_path, run_workflow=slow, clock=clock,
    )
    await sched.tick()
    await asyncio.sleep(0.05)
    clock.advance(minutes=1)
    await sched.tick()
    await asyncio.sleep(0.1)
    assert cancelled == ["prompt"]
```

- [ ] **Step 2: Implement**

Track per-schedule fire tasks: `self._fire_tasks: dict[str, asyncio.Task] = {}`. On `kill`, `self._fire_tasks[name].cancel()`.

- [ ] **Step 3: Verify + commit**

```bash
uv run pytest tests/test_scheduler_overlap.py -v
git add src/aegis/scheduler tests/test_scheduler_overlap.py
git commit -m "feat(scheduler): on_overlap=kill cancels prior fire"
```

### Task 4.5: Notifications (`notify.on_failure`, `notify.on_success`)

**Files:**
- Create: `src/aegis/scheduler/notify.py`
- Modify: `src/aegis/scheduler/scheduler.py`
- Create: `tests/test_scheduler_notify.py`

- [ ] **Step 1: Failing test**

```python
import pytest
from unittest.mock import MagicMock
from aegis.scheduler.notify import maybe_notify


def test_notify_on_failure():
    notifier = MagicMock()
    entry = {"notify": {"on_failure": True, "on_success": False}}
    maybe_notify(notifier, entry, schedule="eod", status="failed:timeout")
    notifier.send.assert_called_once()
    msg = notifier.send.call_args[0][0]
    assert "eod" in msg and "failed:timeout" in msg


def test_no_notify_on_success_when_disabled():
    notifier = MagicMock()
    entry = {"notify": {"on_failure": True, "on_success": False}}
    maybe_notify(notifier, entry, schedule="eod", status="ok")
    notifier.send.assert_not_called()
```

- [ ] **Step 2: Implement**

`src/aegis/scheduler/notify.py`:
```python
from __future__ import annotations


class Notifier:
    """Thin wrapper around bin/notify-telegram.sh or aegis Telegram frontend."""
    def __init__(self, send_fn=None):
        self.send = send_fn or (lambda msg: None)


def maybe_notify(notifier, entry: dict, *, schedule: str, status: str) -> None:
    nf = entry.get("notify", {})
    is_failure = not status.startswith("ok") and status != "ok"
    if is_failure and not nf.get("on_failure", True):
        return
    if not is_failure and not nf.get("on_success", False):
        return
    prefix = "⚠️" if is_failure else "✅"
    notifier.send(f"{prefix} schedule {schedule} — {status}")
```

In `_fire`, after writing the terminal JSONL record:
```python
from aegis.scheduler.notify import maybe_notify
maybe_notify(self.notifier, entry, schedule=name, status=terminal_status)
```

Add `notifier: Notifier | None = None` to `Scheduler.__init__`.

- [ ] **Step 3: Verify + commit**

```bash
uv run pytest tests/test_scheduler_notify.py -v
git add src/aegis/scheduler tests/test_scheduler_notify.py
git commit -m "feat(scheduler): notify.on_failure / on_success Telegram bridge"
```

### Task 4.6: On-boot replay + backfill-once

**Files:**
- Create: `src/aegis/scheduler/replay.py`
- Modify: `src/aegis/scheduler/scheduler.py`
- Create: `tests/test_scheduler_replay.py`

- [ ] **Step 1: Failing test**

```python
import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
from aegis.scheduler.replay import replay_state
from aegis.scheduler.clock import FakeClock


def test_dangling_fire_requested_marked_interrupted(tmp_path: Path):
    log = tmp_path / "schedules" / "eod.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({
        "ts": "2026-05-24T02:00:00+00:00", "schedule": "eod",
        "event": "fire_requested", "task_id": "abc"
    }) + "\n")
    state = replay_state(tmp_path, schedules={"eod": {}})
    # A synthetic fire_failed record is appended.
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    last = json.loads(lines[-1])
    assert last["event"] == "fire_failed"
    assert last["status"] == "failed:interrupted"
    assert state["eod"]["fire_count"] == 1


def test_backfill_once_when_next_fire_past(tmp_path: Path):
    log = tmp_path / "schedules" / "eod.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({
        "ts": "2026-05-23T02:00:00+00:00", "schedule": "eod",
        "event": "fire_completed", "task_id": "abc", "status": "ok",
    }) + "\n")
    schedules = {"eod": {"cron": "0 2 * * *", "timezone": "UTC"}}
    now = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)
    state = replay_state(tmp_path, schedules=schedules, now=now)
    # next_fire would be 2026-05-25T02:00 UTC — in the past.
    # So backfill-once: schedule is eligible on first tick.
    assert state["eod"]["next_fire"] <= now
    assert state["eod"]["backfill"] is True
```

- [ ] **Step 2: Implement**

`src/aegis/scheduler/replay.py`:
```python
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from aegis.scheduler.cron import next_fire as compute_next_fire


def replay_state(state_dir: Path, *, schedules: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    state = {}
    sched_dir = state_dir / "schedules"
    sched_dir.mkdir(parents=True, exist_ok=True)
    for name, entry in schedules.items():
        log = sched_dir / f"{name}.jsonl"
        fire_count = 0
        dangling: str | None = None
        if log.exists():
            for line in log.read_text().splitlines():
                rec = json.loads(line)
                if rec["event"] == "fire_requested":
                    dangling = rec["task_id"]
                elif rec["event"] in ("fire_completed", "fire_failed"):
                    fire_count += 1
                    dangling = None
        if dangling:
            interrupted = {
                "ts": now.isoformat(), "schedule": name,
                "event": "fire_failed", "task_id": dangling,
                "status": "failed:interrupted",
            }
            with log.open("a") as f:
                f.write(json.dumps(interrupted) + "\n")
            fire_count += 1

        if "fire_at" in entry:
            nf = datetime.fromisoformat(entry["fire_at"])
        elif "cron" in entry:
            tz = entry.get("timezone", "UTC")
            nf = compute_next_fire(entry["cron"], tz, now)
        else:
            nf = now

        state[name] = {
            "fire_count": fire_count,
            "next_fire": nf,
            "backfill": nf <= now,
        }
    return state
```

In `Scheduler`, replace `__post_init_state` to call `replay_state`.

- [ ] **Step 3: Verify + commit**

```bash
uv run pytest tests/test_scheduler_replay.py -v
git add src/aegis/scheduler tests/test_scheduler_replay.py
git commit -m "feat(scheduler): on-boot replay + backfill-once"
```

---

## Vertical Slice 5 — TUI integration

End-state: `Ctrl+D` is a tabbed ops console with `Queues | Schedules`; schedules tab has SCHEDULES / IN-FLIGHT / RECENT bands; DetailPanel; `Space`/`F`/`E`/`>` actions.

### Task 5.1: Tabbed `Ctrl+D` dashboard shell

**Files:**
- Create: `src/aegis/tui/dashboard_tabs.py`
- Modify: `src/aegis/tui/app.py`

- [ ] **Step 1: Implement `DashboardTabs` modal**

`src/aegis/tui/dashboard_tabs.py`:
```python
from __future__ import annotations
from textual.app import ComposeResult
from textual.widgets import Tabs, Tab, ContentSwitcher
from textual.screen import ModalScreen
from textual.binding import Binding


class DashboardModal(ModalScreen):
    BINDINGS = [
        Binding("escape", "dismiss", "close"),
        Binding("shift+tab", "next_tab", "switch tab"),
    ]

    def compose(self) -> ComposeResult:
        yield Tabs(
            Tab("Queues", id="tab-queues"),
            Tab("Schedules", id="tab-schedules"),
            active="tab-queues",
        )
        # Two children: the existing queues dashboard widget and the new
        # schedules panel widget. Use ContentSwitcher keyed off active tab.
        yield ContentSwitcher(
            self.app.queue_dashboard_widget(),   # existing
            self.app.schedules_panel_widget(),   # new (Task 5.2)
            initial="queue_dashboard",
        )

    def action_next_tab(self) -> None:
        tabs = self.query_one(Tabs)
        tabs.action_next_tab()

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        switcher = self.query_one(ContentSwitcher)
        switcher.current = (
            "queue_dashboard" if event.tab.id == "tab-queues"
            else "schedules_panel"
        )
```

- [ ] **Step 2: Replace Ctrl+D binding in `app.py`**

In `src/aegis/tui/app.py`, change the `ctrl+d` binding to push `DashboardModal` instead of the bare queue dashboard.

- [ ] **Step 3: Commit**

```bash
git add src/aegis/tui/dashboard_tabs.py src/aegis/tui/app.py
git commit -m "feat(tui): tabbed Ctrl+D dashboard (Queues | Schedules)"
```

### Task 5.2: Schedules panel — bands + DetailPanel

**Files:**
- Create: `src/aegis/tui/schedules_panel.py`

- [ ] **Step 1: Build `SchedulesPanel` widget**

`src/aegis/tui/schedules_panel.py`:
```python
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Static, ListView, ListItem, Label
from textual.binding import Binding


class SchedulesPanel(Vertical):
    """SCHEDULES + IN-FLIGHT + RECENT bands on the left ⅔;
    DetailPanel on the right ⅓."""

    BINDINGS = [
        Binding("space", "toggle_enabled", "pause/resume"),
        Binding("f", "fire_now", "fire-now"),
        Binding("e", "open_editor", "edit YAML"),
        Binding("greater_than", "jump_worker", "jump to worker"),
    ]

    def __init__(self, *, scheduler, **kw):
        super().__init__(**kw)
        self.scheduler = scheduler

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="bands"):
                yield Static("SCHEDULES", id="schedules-header")
                yield DataTable(id="schedules-table")
                yield Static("IN-FLIGHT", id="inflight-header")
                yield DataTable(id="inflight-table")
                yield Static("RECENT (last 10)", id="recent-header")
                yield DataTable(id="recent-table")
            yield Static(id="detail-panel")

    def on_mount(self) -> None:
        self._populate()
        self.set_interval(2.0, self._populate)

    def _populate(self) -> None:
        snap_path = self.scheduler.state_dir / "schedules.snapshot.json"
        snap = json.loads(snap_path.read_text()) if snap_path.exists() else {}
        table = self.query_one("#schedules-table", DataTable)
        table.clear()
        if not table.columns:
            table.add_columns("name", "trigger", "next", "status")
        for name, entry in self.scheduler.schedules.items():
            trigger = entry.get("cron") or entry.get("fire_at", "?")
            next_fire = snap.get(name, {}).get("next_fire", "?")
            status = "paused" if not entry.get("enabled", True) else "armed"
            table.add_row(name, trigger, next_fire, status)
        # IN-FLIGHT and RECENT tables populated similarly from snap + JSONL tail
        # (kept brief here; pattern is the same as queue dashboard).

    def action_toggle_enabled(self) -> None:
        # Find the cursored row, flip enabled in .aegis.yaml via ruamel.
        # Implementation: see Task 5.3.
        pass

    def action_fire_now(self) -> None:
        table = self.query_one("#schedules-table", DataTable)
        row = table.get_row_at(table.cursor_row)
        name = row[0]
        self.scheduler.fire_now(name)

    def action_open_editor(self) -> None:
        import os, subprocess
        subprocess.Popen([os.environ.get("EDITOR", "vi"), ".aegis.yaml"])

    def action_jump_worker(self) -> None:
        # Implementation: lookup in-flight task_id → tab handle → app.switch_tab
        pass
```

- [ ] **Step 2: Add `fire_now(name)` method to `Scheduler`**

In `Scheduler`:
```python
def fire_now(self, name: str) -> None:
    entry = self.schedules[name]
    asyncio.create_task(self._fire(name, entry, manual=True))
```

Update `_fire` signature to accept `manual=False` and tag the `fire_requested` record accordingly.

- [ ] **Step 3: Commit**

```bash
git add src/aegis/tui/schedules_panel.py src/aegis/scheduler/scheduler.py
git commit -m "feat(tui): schedules panel with bands + F/E actions"
```

### Task 5.3: `Space` — toggle `enabled` via ruamel.yaml

**Files:**
- Modify: `src/aegis/tui/schedules_panel.py`
- Create: `src/aegis/config/edit.py`

- [ ] **Step 1: Implement comment-preserving edit**

`src/aegis/config/edit.py`:
```python
from __future__ import annotations
from pathlib import Path
from ruamel.yaml import YAML


def toggle_schedule_enabled(root: Path, name: str) -> bool:
    """Flip enabled: true|false for schedule `name`.
    Checks .aegis/schedules/<name>.yaml first, falls back to .aegis.yaml.
    Returns the new enabled state."""
    yaml = YAML()
    yaml.preserve_quotes = True
    overlay = root / ".aegis" / "schedules" / f"{name}.yaml"
    if overlay.exists():
        data = yaml.load(overlay)
        data["enabled"] = not data.get("enabled", True)
        with overlay.open("w") as f:
            yaml.dump(data, f)
        return data["enabled"]
    base = root / ".aegis.yaml"
    data = yaml.load(base)
    entry = data["schedules"][name]
    entry["enabled"] = not entry.get("enabled", True)
    with base.open("w") as f:
        yaml.dump(data, f)
    return entry["enabled"]
```

- [ ] **Step 2: Wire `action_toggle_enabled`**

In `SchedulesPanel.action_toggle_enabled`:
```python
from aegis.config.edit import toggle_schedule_enabled
table = self.query_one("#schedules-table", DataTable)
row = table.get_row_at(table.cursor_row)
name = row[0]
new_state = toggle_schedule_enabled(self.scheduler.root, name)
# Filesystem watcher (VS7) picks up the change and reloads.
```

- [ ] **Step 3: Test**

`tests/test_config_edit.py`:
```python
from pathlib import Path
from aegis.config.edit import toggle_schedule_enabled


def test_toggle_overlay(tmp_path: Path):
    overlay = tmp_path / ".aegis" / "schedules"
    overlay.mkdir(parents=True)
    (overlay / "eod.yaml").write_text("workflow: prompt\nenabled: true\n")
    new = toggle_schedule_enabled(tmp_path, "eod")
    assert new is False
    assert "enabled: false" in (overlay / "eod.yaml").read_text()
```

- [ ] **Step 4: Verify + commit**

```bash
uv run pytest tests/test_config_edit.py -v
git add src/aegis/config/edit.py src/aegis/tui/schedules_panel.py tests/test_config_edit.py
git commit -m "feat(tui): Space toggles enabled in YAML (comment-preserving)"
```

---

## Vertical Slice 6 — CLI

End-state: `aegis schedule list/show/run/enable/disable/logs` works.

### Task 6.1: CLI scaffold + `list` + `show`

**Files:**
- Create: `src/aegis/cli/schedule.py`
- Modify: `src/aegis/cli.py`
- Create: `tests/test_cli_schedule.py`

- [ ] **Step 1: CLI module**

`src/aegis/cli/schedule.py`:
```python
from __future__ import annotations
import json
from pathlib import Path
import typer
from rich.console import Console
from rich.table import Table
from aegis.config.yaml_loader import load_config

app = typer.Typer(help="Manage scheduled tasks.")
console = Console()


def _cfg() -> tuple[Path, "AegisConfig"]:
    root = Path.cwd()
    return root, load_config(root)


@app.command("list")
def list_schedules():
    """Tabular view of all schedules."""
    root, cfg = _cfg()
    snap_path = root / ".aegis" / "state" / "schedules.snapshot.json"
    snap = json.loads(snap_path.read_text()) if snap_path.exists() else {}
    table = Table()
    for col in ("name", "trigger", "next", "fires", "status"):
        table.add_column(col)
    for name, entry in cfg.schedules.items():
        trigger = entry.get("cron") or entry.get("fire_at", "?")
        nxt = snap.get(name, {}).get("next_fire", "—")
        fires = snap.get(name, {}).get("fire_count", 0)
        status = "paused" if not entry.get("enabled", True) else "armed"
        table.add_row(name, trigger, nxt, str(fires), status)
    console.print(table)


@app.command("show")
def show_schedule(name: str):
    """Full config + last 10 fires."""
    root, cfg = _cfg()
    if name not in cfg.schedules:
        typer.echo(f"unknown schedule: {name}", err=True)
        raise typer.Exit(1)
    entry = cfg.schedules[name]
    console.print(entry)
    log = root / ".aegis" / "state" / "schedules" / f"{name}.jsonl"
    if log.exists():
        lines = log.read_text().splitlines()[-10:]
        console.print("Last 10 events:")
        for line in lines:
            console.print(json.loads(line))
```

- [ ] **Step 2: Wire into root `cli.py`**

In `src/aegis/cli.py`:
```python
from aegis.cli.schedule import app as schedule_app
app.add_typer(schedule_app, name="schedule")
```

- [ ] **Step 3: Smoke + commit**

```bash
uv run aegis schedule list
git add src/aegis/cli src/aegis/cli.py tests/test_cli_schedule.py
git commit -m "feat(cli): aegis schedule list / show"
```

### Task 6.2: `aegis schedule run / enable / disable / logs`

**Files:**
- Modify: `src/aegis/cli/schedule.py`

- [ ] **Step 1: Add subcommands**

Append to `src/aegis/cli/schedule.py`:
```python
@app.command("run")
def run_schedule(name: str):
    """Fire-now via the manual-fire path."""
    # When aegis serve isn't running, run the workflow directly via runner.
    # When it is, send a control message via the MCP server's `aegis_schedule_run`
    # tool (added next slice). v1: direct path, no IPC.
    from aegis.workflow.runner import run_workflow
    import asyncio
    root, cfg = _cfg()
    entry = cfg.schedules[name]
    result = asyncio.run(run_workflow(entry["workflow"], entry.get("args", {})))
    console.print(result)


@app.command("enable")
def enable_schedule(name: str):
    from aegis.config.edit import set_schedule_enabled
    root, _ = _cfg()
    set_schedule_enabled(root, name, True)
    console.print(f"{name}: enabled")


@app.command("disable")
def disable_schedule(name: str):
    from aegis.config.edit import set_schedule_enabled
    root, _ = _cfg()
    set_schedule_enabled(root, name, False)
    console.print(f"{name}: disabled")


@app.command("logs")
def schedule_logs(name: str, tail: int = 20):
    root, _ = _cfg()
    log = root / ".aegis" / "state" / "schedules" / f"{name}.jsonl"
    if not log.exists():
        typer.echo(f"no log for {name}")
        raise typer.Exit(1)
    for line in log.read_text().splitlines()[-tail:]:
        console.print(json.loads(line))
```

Add `set_schedule_enabled(root, name, value)` to `src/aegis/config/edit.py` (factored from `toggle_schedule_enabled`).

- [ ] **Step 2: Smoke + commit**

```bash
uv run aegis schedule list
git add src/aegis/cli src/aegis/config/edit.py
git commit -m "feat(cli): aegis schedule run / enable / disable / logs"
```

---

## Vertical Slice 7 — Hot reload

End-state: filesystem watcher on `.aegis.yaml` + overlay folders; atomic-swap-or-reject reload.

### Task 7.1: Watcher + reload coordinator

**Files:**
- Create: `src/aegis/scheduler/reload.py`
- Create: `tests/test_scheduler_reload.py`

- [ ] **Step 1: Failing test**

```python
import asyncio
import textwrap
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from aegis.scheduler.reload import ReloadWatcher


@pytest.mark.asyncio
async def test_add_schedule_overlay_triggers_reload(tmp_path: Path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    on_reload = MagicMock()
    watcher = ReloadWatcher(tmp_path, on_reload=on_reload)
    await watcher.start()
    overlay = tmp_path / ".aegis" / "schedules"
    overlay.mkdir(parents=True)
    (overlay / "new.yaml").write_text("workflow: prompt\ncron: '* * * * *'\n")
    await asyncio.sleep(1.0)
    on_reload.assert_called()
    await watcher.stop()
```

- [ ] **Step 2: Implement**

`src/aegis/scheduler/reload.py`:
```python
from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Callable
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class _Handler(FileSystemEventHandler):
    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self.queue = queue
        self.loop = loop

    def on_any_event(self, event):
        if event.src_path.endswith((".yaml", ".yml")):
            asyncio.run_coroutine_threadsafe(self.queue.put(event), self.loop)


class ReloadWatcher:
    def __init__(self, root: Path, *, on_reload: Callable[[], None]):
        self.root = root
        self.on_reload = on_reload
        self._observer = Observer()
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        handler = _Handler(self._queue, loop)
        self._observer.schedule(handler, str(self.root / ".aegis.yaml"), recursive=False)
        for section in ("agents", "queues", "schedules"):
            folder = self.root / ".aegis" / section
            folder.mkdir(parents=True, exist_ok=True)
            self._observer.schedule(handler, str(folder), recursive=False)
        self._observer.start()
        self._task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        debounce_s = 0.5
        while True:
            await self._queue.get()
            # Drain any backlog within debounce window.
            try:
                while True:
                    await asyncio.wait_for(self._queue.get(), timeout=debounce_s)
            except asyncio.TimeoutError:
                pass
            try:
                self.on_reload()
            except Exception as e:
                # Log + beep; never crash the watcher loop.
                import sys
                print(f"reload_failed: {e!r}", file=sys.stderr)

    async def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
        if self._task:
            self._task.cancel()
```

- [ ] **Step 3: Wire into `aegis serve`**

In serve entrypoint:
```python
from aegis.scheduler.reload import ReloadWatcher

def on_reload():
    try:
        new_cfg = load_config(root)
    except Exception as e:
        # Reject — keep old in-memory state.
        # Append to .aegis/state/aegis_events.jsonl.
        return
    # Atomic swap: replace scheduler.schedules, queue config, agents.
    scheduler.replace_schedules(new_cfg.schedules)

watcher = ReloadWatcher(root, on_reload=on_reload)
await watcher.start()
```

Add `Scheduler.replace_schedules(schedules)` that updates `self.schedules`, re-computes `next_fire` for new entries, drops state for removed ones.

- [ ] **Step 4: Verify + commit**

```bash
uv run pytest tests/test_scheduler_reload.py -v
git add src/aegis/scheduler/reload.py src/aegis/server tests/test_scheduler_reload.py
git commit -m "feat(scheduler): filesystem watcher + atomic-swap-or-reject reload"
```

---

## Vertical Slice 8 — Rollout polish

End-state: systemd unit + installer script shipped; docs updated; CHANGELOG entry.

### Task 8.1: systemd unit + installer

**Files:**
- Create: `scripts/aegis.service`
- Create: `scripts/install-vps-service.sh`

- [ ] **Step 1: Unit file**

`scripts/aegis.service`:
```ini
[Unit]
Description=Aegis serve — long-running scheduler + MCP plane
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
ExecStart=%h/.local/bin/uv run --directory %h/Workspace/repos/aegis aegis serve
Restart=on-failure
RestartSec=10s
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
StandardOutput=append:%h/.aegis/aegis.log
StandardError=append:%h/.aegis/aegis.log

[Install]
WantedBy=default.target
```

- [ ] **Step 2: Installer**

`scripts/install-vps-service.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
mkdir -p ~/.config/systemd/user ~/.aegis
cp "$(dirname "$0")/aegis.service" ~/.config/systemd/user/aegis.service
loginctl enable-linger "$USER" || true
systemctl --user daemon-reload
systemctl --user enable --now aegis.service
systemctl --user status aegis.service --no-pager
```

Make executable: `chmod +x scripts/install-vps-service.sh`.

- [ ] **Step 3: Commit**

```bash
git add scripts/aegis.service scripts/install-vps-service.sh
chmod +x scripts/install-vps-service.sh
git commit -m "feat(rollout): systemd --user unit + installer for aegis serve"
```

### Task 8.2: Docs + CHANGELOG

**Files:**
- Modify: `README.md`, `docs/roadmap.md`, `CHANGELOG.md`, `AGENTS.md`

- [ ] **Step 1: README — scheduler section**

Add a "Scheduler" section to `README.md` covering `.aegis.yaml` schedules, drop-in overlays, `aegis schedule` CLI, `Ctrl+D` dashboard.

- [ ] **Step 2: roadmap tick**

In `docs/roadmap.md`, mark scheduler as shipped.

- [ ] **Step 3: CHANGELOG**

Add a new release entry summarizing: declarative YAML config + overlays, plugin auto-import refinement, built-in workflows (prompt, enqueue), Scheduler substrate, `Ctrl+D` tabbed dashboard, `aegis schedule` CLI, systemd unit.

- [ ] **Step 4: AGENTS.md update**

Replace the `.aegis.py` references with `.aegis.yaml`; document the overlay folders + plugin dir; note the scheduler module layout.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/roadmap.md CHANGELOG.md AGENTS.md
git commit -m "docs(scheduler): README + roadmap + CHANGELOG + AGENTS.md"
```

---

## Final verification

- [ ] **Step 1: Full hermetic suite passes**

```bash
uv run pytest -q -m "not live"
```
Expected: all green.

- [ ] **Step 2: Live suite passes**

```bash
uv run pytest -m live
```
Expected: all green (`test_scheduler_live.py` fires a real cron-triggered prompt workflow against `claude`).

- [ ] **Step 3: Final commit + tag**

```bash
git log --oneline -30
git tag v0.6.0 -m "Scheduler substrate"
git push origin main --tags
```

- [ ] **Step 4: VPS install**

On the VPS:
```bash
cd ~/Workspace/repos/aegis
git pull
uv sync
bash scripts/install-vps-service.sh
```

Verify with `systemctl --user status aegis.service`.

- [ ] **Step 5: Author daily routines as overlays**

Translate the routines in `vault/+/jobs/` (end-of-day, briefing, weekly, claude-private-tick, etc.) into one file each at `repos/aegis/.aegis/schedules/<name>.yaml`. Commit them.

- [ ] **Step 6: Parallel-run for 3 days, then disarm old substrate**

After 3 days of clean parallel operation, set `status: cancelled` on the old `vault/+/jobs/*.md` files and stop `job-crawler.timer` on the VPS.
