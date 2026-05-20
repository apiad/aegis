---
kind: plan
title: Aegis Workflow Scaffold v1 — Implementation Plan
status: approved
date: 2026-05-20
spec: "[[2026-05-20-aegis-workflow-scaffold-design]]"
---

# Aegis Workflow Scaffold v1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL —
> `superpowers:subagent-driven-development` or
> `superpowers:executing-plans`. Commit to `main` (authorized for aegis).
> Every task keeps `uv run pytest -q -m "not live"` green; one logical
> change per commit; push after each task. **Gate as a separate step** —
> run `uv run pytest -q -m "not live"; rc=$?`, read the result, then
> commit. Do NOT pipe pytest into `tail` inside an `&&` chain (the
> exit code gets swallowed; see commit `b858fd2` for the marker fix
> and `fe9d15b → e674c56` for the regression that taught the lesson).

**Goal.** Ship the harness-runs-the-program workflow primitive end-to-end:
the `@workflow` decorator + auto-registry, the `WorkflowEngine` runtime
(delegate / send + drain / spawn + close / bash / log / caller_handle),
the `runner` that wires CLI + MCP onto the same path, the `aegis workflow`
typer subcommands, the `aegis_run_workflow` MCP tool, and one canonical
TDD workflow as the live smoke proof.

**Architecture.** New package `src/aegis/workflow/{decorator, engine,
runner}.py`; new typer subcommand group under `aegis workflow`; new MCP
tool registered in `mcp/server.py`; `AppBridge` extended with
`spawn(profile, *, handle=None)` and `close(handle)` so the engine can
spawn long-lived agents through the same seam the queue uses. Workflows
compose on the v1 task queue — `engine.delegate` calls
`queue_manager.enqueue` + awaits the callback via a transient inbox
binding; `engine.send` rides `inbox_router.deliver` (fire-and-forget,
the substrate's wake-on-idle / mid-turn-buffer logic handles everything);
`engine.drain` awaits `state == ready` on each touched handle.

**Tech Stack.** Python 3.13, `uv` (not pip), pytest with asyncio-auto,
typer for CLI, fastmcp for MCP. Hermetic tests: `uv run pytest -q -m
"not live"`. Live tests (real `claude` subprocess) carry the `live`
marker and are skipped by default; they run when `claude` is on PATH.

**Hard preconditions (fail loud if any miss).**

1. `cd /home/apiad/Workspace/repos/aegis && git pull --ff-only` —
   spec must be at commit `5f36cb2` or later (file exists at
   `docs/superpowers/specs/2026-05-20-aegis-workflow-scaffold-design.html`).
2. `uv run pytest -q -m "not live"` green on baseline (should be 196
   passed, 4 deselected as of `5f36cb2`).
3. The v1 task queue is shipped and integrated — the engine composes on
   `QueueManager`, `InboxRouter`, `AppBridge`. If `src/aegis/queue/` is
   missing or its tests are red, abort.

---

## File structure

| Path | Change |
|---|---|
| `src/aegis/workflow/__init__.py` | New — re-export `workflow`, `WorkflowEngine`, `WorkflowError`, `list_workflows`, `get_workflow`, `run_workflow`. |
| `src/aegis/workflow/decorator.py` | New — `@workflow` decorator + `_REGISTRY` + `list_workflows()` + `get_workflow()` + `WorkflowError`. Validates signature; raises on collision. |
| `src/aegis/workflow/engine.py` | New — `WorkflowEngine` class: `delegate`, `send`, `drain`, `spawn`, `close`, `bash`, `log`, `list_sessions`, `list_agents` + `_DelegationPromise` helper. |
| `src/aegis/workflow/runner.py` | New — `run_workflow(name, kwargs, *, bridge, queue_manager, inbox_router, caller_handle=None) -> dict`. Builds engine, calls workflow, auto-drains + auto-closes in finally. |
| `src/aegis/mcp/bridge.py` | Modify — `AppBridge` Protocol gains `async spawn(profile: str, *, handle: str \| None = None) -> str` and `async close(handle: str) -> None`. |
| `src/aegis/core/manager.py` | Modify — `SessionManager` gains async `spawn_for_bridge(profile, *, handle=None) -> str` returning the new handle (wraps existing sync `spawn`). Already has `close(handle)`. |
| `src/aegis/tui/app.py` | Modify — `AegisApp` gains `async spawn(profile, *, handle=None) -> str` (delegates to the `_SessionManagerAdapter`) and `async close(handle)` (delegates to `_close_pane`). |
| `src/aegis/mcp/server.py` | Modify — register `aegis_run_workflow` MCP tool. Extend `BRIEFING` to name it. |
| `src/aegis/cli.py` | Modify — add `aegis workflow` typer sub-group: `aegis workflow list`, `aegis workflow run <name> [--key=value...]`. |
| `examples/__init__.py` | New — empty marker file. |
| `examples/tdd_step.py` | New — the canonical TDD workflow; imported from a sample `.aegis.py` for the live smoke. |
| `tests/test_workflow_decorator.py` | New — `@workflow` registration + signature validation + collision detection. |
| `tests/test_workflow_engine.py` | New — engine methods, in-memory with stub bridge/queue/router. |
| `tests/test_workflow_runner.py` | New — runner shapes (`{status, result?, error?}`); auto-drain + auto-close in finally; `caller_handle` passthrough. |
| `tests/test_workflow_cli.py` | New — `aegis workflow list` + `aegis workflow run`. |
| `tests/test_workflow_mcp.py` | New — `aegis_run_workflow` tool shape; callback delivery into producer inbox. |
| `tests/test_workflow_live.py` | New (VS5) — live e2e with real `claude` via the TDD workflow. Marker `live`. |
| `tests/test_mcp_bridge.py` | Modify — `AppBridge` Protocol now requires `spawn` + `close`; positive/negative isinstance updates. |
| `tests/test_mcp_server.py` | Modify — `FakeBridge` gains `spawn`/`close` stubs; tool-list test adds `aegis_run_workflow`. |
| `tests/test_core_manager.py` | Modify — assert `SessionManager.spawn_for_bridge` returns a handle and the session is in `_sessions`. |
| `tests/test_tui.py` | Modify — assert `AegisApp.spawn` mounts a pane and returns its handle; `AegisApp.close(h)` removes it. |
| `AGENTS.md` | Modify (VS5) — add `src/aegis/workflow/` block. |

---

## Shared contracts (define once, reference from later tasks)

### Signatures

```python
# src/aegis/workflow/decorator.py
class WorkflowError(Exception): pass

WorkflowFn = Callable[..., Awaitable[Any]]   # async def fn(engine, **kwargs)

_REGISTRY: dict[str, WorkflowFn] = {}

def workflow(fn: WorkflowFn) -> WorkflowFn:
    """Register an async workflow under fn.__name__. Validates signature
    (async, first positional param named 'engine'). Raises ConfigError
    on name collision."""

def list_workflows() -> list[str]: ...
def get_workflow(name: str) -> WorkflowFn | None: ...
```

```python
# src/aegis/workflow/engine.py
class _DelegationPromise:
    """Session-shaped: async def deliver(msg) resolves a Future."""

class WorkflowEngine:
    def __init__(self, *, workflow_name: str, workflow_run_id: str,
                 bridge, queue_manager, inbox_router,
                 caller_handle: str | None = None,
                 state_dir: Path | None = None,
                 now: Callable[[], str] = now_iso,
                 drain_timeout: float = 30.0) -> None: ...
    workflow_name: str
    workflow_run_id: str
    caller_handle: str | None
    # public API:
    async def delegate(self, queue: str, payload: str) -> str: ...
    def send(self, handle: str, message: str) -> None: ...
    async def drain(self, handle: str | None = None) -> None: ...
    async def spawn(self, profile: str, *, handle: str | None = None) -> str: ...
    async def close(self, handle: str) -> None: ...
    async def bash(self, cmd: str, *, cwd: str | Path | None = None,
                   timeout: float | None = None,
                   env: dict | None = None) -> CompletedProcess: ...
    def log(self, message: str) -> None: ...
    def list_sessions(self) -> list[SessionInfo]: ...
    def list_agents(self) -> list[str]: ...
    # internal state:
    _spawned_handles: set[str]   # auto-close at runner exit
    _touched_handles: set[str]   # auto-drain at runner exit
```

```python
# src/aegis/workflow/runner.py
async def run_workflow(
    name: str, kwargs: dict, *,
    bridge, queue_manager, inbox_router,
    caller_handle: str | None = None,
    state_dir: Path | None = None,
) -> dict:
    """Returns {status: "ok"|"error", result?: ..., error?: str,
                workflow_run_id: str}."""
```

```python
# src/aegis/mcp/bridge.py — extended Protocol
class AppBridge(Protocol):
    queue_manager: object
    inbox_router: object
    def list_sessions(self) -> list[SessionInfo]: ...
    def list_agents(self) -> list[str]: ...
    async def handoff(self, from_handle: str, target_handle: str,
                      context: str) -> str: ...
    async def spawn(self, profile: str, *,
                    handle: str | None = None) -> str: ...
    async def close(self, handle: str) -> None: ...
```

### Header rendered by `send`

Same shape `aegis_handoff` already produces (per universal-tagging
principle):

```
> from workflow:<name> · <iso-timestamp>
<message body>
```

The substrate's `_render_batch` in `core/session.py` already prepends
`render_inbox_header(msg)` for every inbox message — workflow sends
inherit this for free; nothing to add.

---

## Vertical slice 1 — Scaffold + CLI + log + bash

Thinnest end-to-end: `aegis workflow run hello --name=Alex` prints
"Hi Alex!" via `engine.log` and `engine.bash`. Proves decorator, registry,
engine basics, runner, CLI.

### Task 1.1 — workflow/ package + @workflow + WorkflowError

**Files:**
- Create: `src/aegis/workflow/__init__.py`, `src/aegis/workflow/decorator.py`
- Test: `tests/test_workflow_decorator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_workflow_decorator.py
from __future__ import annotations

import pytest

from aegis.config import ConfigError
from aegis.workflow import (
    WorkflowError, get_workflow, list_workflows, workflow,
)
from aegis.workflow.decorator import _REGISTRY


@pytest.fixture(autouse=True)
def clean_registry():
    """Each test sees a fresh registry."""
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


def test_workflow_decorator_registers_under_function_name():
    @workflow
    async def my_flow(engine, *, x):
        return x
    assert "my_flow" in list_workflows()
    assert get_workflow("my_flow") is my_flow


def test_workflow_decorator_rejects_non_async():
    with pytest.raises(TypeError, match="must be async"):
        @workflow
        def sync_flow(engine):
            return None


def test_workflow_decorator_rejects_missing_engine_param():
    with pytest.raises(TypeError, match="first parameter must be 'engine'"):
        @workflow
        async def no_engine(x):
            return x


def test_workflow_decorator_rejects_name_collision():
    @workflow
    async def dup(engine):
        return None
    with pytest.raises(ConfigError, match="dup"):
        @workflow
        async def dup(engine):                          # noqa: F811
            return None


def test_get_workflow_unknown_returns_none():
    assert get_workflow("ghost") is None


def test_workflow_error_is_exception():
    assert issubclass(WorkflowError, Exception)
```

- [ ] **Step 2: Run, expect ImportError**

```
uv run pytest tests/test_workflow_decorator.py -q
# expected: ERROR — No module named 'aegis.workflow'
```

- [ ] **Step 3: Implement decorator**

```python
# src/aegis/workflow/decorator.py
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from aegis.config import ConfigError


class WorkflowError(Exception):
    """Expected failure inside a workflow (predicate violated, retry
    exhausted, etc.). Workflows raise this for clean failure reporting.
    Plain Exception is treated as an unexpected crash."""


WorkflowFn = Callable[..., Awaitable[Any]]
_REGISTRY: dict[str, WorkflowFn] = {}


def workflow(fn: WorkflowFn) -> WorkflowFn:
    """Register an async workflow under ``fn.__name__``."""
    if not inspect.iscoroutinefunction(fn):
        raise TypeError(
            f"@workflow on {fn.__name__}: must be async def")
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    if not params or params[0].name != "engine":
        raise TypeError(
            f"@workflow on {fn.__name__}: first parameter must be 'engine'")
    name = fn.__name__
    if name in _REGISTRY:
        raise ConfigError(
            f"workflow name collision: {name!r} already registered "
            f"(from {_REGISTRY[name].__module__}); cannot re-register "
            f"from {fn.__module__}")
    _REGISTRY[name] = fn
    return fn


def list_workflows() -> list[str]:
    return sorted(_REGISTRY)


def get_workflow(name: str) -> WorkflowFn | None:
    return _REGISTRY.get(name)
```

```python
# src/aegis/workflow/__init__.py
from aegis.workflow.decorator import (
    WorkflowError, get_workflow, list_workflows, workflow,
)

__all__ = ["WorkflowError", "get_workflow", "list_workflows", "workflow"]
```

- [ ] **Step 4: Run, expect pass**

```
uv run pytest tests/test_workflow_decorator.py -q   # 6 passed
uv run pytest -q -m "not live"
# Read rc=$? after pytest (do NOT pipe to tail).
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/workflow/__init__.py src/aegis/workflow/decorator.py \
  tests/test_workflow_decorator.py
git commit -m "feat(workflow): @workflow decorator + registry + WorkflowError"
git push
```

### Task 1.2 — WorkflowEngine skeleton: log + caller_handle + state slots

**Files:**
- Create: `src/aegis/workflow/engine.py`
- Modify: `src/aegis/workflow/__init__.py` (re-export `WorkflowEngine`)
- Test: `tests/test_workflow_engine.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_workflow_engine.py
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.workflow import WorkflowEngine


class _StubBridge:
    queue_manager = None
    inbox_router = None
    def list_sessions(self): return []
    def list_agents(self): return ["default"]


def _engine(tmp_path: Path, **kw):
    return WorkflowEngine(
        workflow_name="t", workflow_run_id="01TID",
        bridge=_StubBridge(), queue_manager=None, inbox_router=None,
        state_dir=tmp_path, **kw)


def test_engine_exposes_name_run_id_caller(tmp_path):
    e = _engine(tmp_path, caller_handle="lucid-knuth")
    assert e.workflow_name == "t"
    assert e.workflow_run_id == "01TID"
    assert e.caller_handle == "lucid-knuth"


def test_engine_caller_defaults_to_none(tmp_path):
    e = _engine(tmp_path)
    assert e.caller_handle is None


def test_engine_log_writes_jsonl_under_state_dir(tmp_path):
    e = _engine(tmp_path)
    e.log("hello")
    e.log("world")
    log_file = tmp_path / "workflows" / "01TID.jsonl"
    assert log_file.exists()
    lines = [line for line in log_file.read_text().splitlines() if line]
    assert len(lines) == 2
    import json
    assert json.loads(lines[0])["message"] == "hello"
    assert json.loads(lines[1])["message"] == "world"


def test_engine_log_no_state_dir_is_stderr_only(capfd):
    e = WorkflowEngine(workflow_name="t", workflow_run_id="01TID",
                       bridge=_StubBridge(), queue_manager=None,
                       inbox_router=None, state_dir=None)
    e.log("only-stderr")
    captured = capfd.readouterr()
    assert "only-stderr" in captured.err
    assert "[workflow:t]" in captured.err


def test_engine_initial_state_empty(tmp_path):
    e = _engine(tmp_path)
    assert e._spawned_handles == set()
    assert e._touched_handles == set()


def test_engine_list_passthroughs(tmp_path):
    e = _engine(tmp_path)
    assert e.list_sessions() == []
    assert e.list_agents() == ["default"]
```

- [ ] **Step 2: Run, expect ImportError**

```
uv run pytest tests/test_workflow_engine.py -q
# expected: ERROR — cannot import name 'WorkflowEngine'
```

- [ ] **Step 3: Implement engine skeleton**

```python
# src/aegis/workflow/engine.py
from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

from aegis.mcp.bridge import SessionInfo
from aegis.queue.schema import now_iso


class WorkflowEngine:
    """Runtime handle a workflow receives as its first positional argument.

    Constructed once per workflow run; bound to live aegis substrate
    (AppBridge, QueueManager, InboxRouter). Tracks _spawned_handles for
    auto-close and _touched_handles for auto-drain at runner exit.
    """

    def __init__(self, *, workflow_name: str, workflow_run_id: str,
                 bridge, queue_manager, inbox_router,
                 caller_handle: str | None = None,
                 state_dir: Path | None = None,
                 now: Callable[[], str] = now_iso,
                 drain_timeout: float = 30.0) -> None:
        self.workflow_name = workflow_name
        self.workflow_run_id = workflow_run_id
        self.caller_handle = caller_handle
        self._bridge = bridge
        self._queue = queue_manager
        self._inbox = inbox_router
        self._state_dir = state_dir
        self._now = now
        self._drain_timeout = drain_timeout
        self._spawned_handles: set[str] = set()
        self._touched_handles: set[str] = set()

    # ── read-only passthroughs ───────────────────────────────────────
    def list_sessions(self) -> list[SessionInfo]:
        return self._bridge.list_sessions()

    def list_agents(self) -> list[str]:
        return self._bridge.list_agents()

    # ── log ──────────────────────────────────────────────────────────
    def log(self, message: str) -> None:
        print(f"[workflow:{self.workflow_name}] {message}",
              file=sys.stderr, flush=True)
        if self._state_dir is None:
            return
        path = (Path(self._state_dir) / "workflows"
                / f"{self.workflow_run_id}.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": self._now(), "message": message}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
```

Update `__init__.py`:

```python
from aegis.workflow.decorator import (
    WorkflowError, get_workflow, list_workflows, workflow,
)
from aegis.workflow.engine import WorkflowEngine

__all__ = [
    "WorkflowEngine", "WorkflowError",
    "get_workflow", "list_workflows", "workflow",
]
```

- [ ] **Step 4: Run, expect pass**

```
uv run pytest tests/test_workflow_engine.py -q   # 6 passed
uv run pytest -q -m "not live"; echo "rc=$?"
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/workflow/engine.py src/aegis/workflow/__init__.py \
  tests/test_workflow_engine.py
git commit -m "feat(workflow): WorkflowEngine skeleton — log + caller_handle + state"
git push
```

### Task 1.3 — engine.bash

**Files:**
- Modify: `src/aegis/workflow/engine.py` (add `bash` method)
- Modify: `tests/test_workflow_engine.py` (append bash tests)

- [ ] **Step 1: Append failing tests to `tests/test_workflow_engine.py`**

```python
import asyncio
import subprocess

from aegis.workflow import WorkflowError


async def test_bash_returns_completed_process(tmp_path):
    e = _engine(tmp_path)
    proc = await e.bash("echo hi")
    assert isinstance(proc, subprocess.CompletedProcess)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "hi"
    assert proc.stderr == ""


async def test_bash_nonzero_returncode_not_raised(tmp_path):
    e = _engine(tmp_path)
    proc = await e.bash("false")
    assert proc.returncode != 0


async def test_bash_timeout_raises_workflow_error(tmp_path):
    e = _engine(tmp_path)
    with pytest.raises(WorkflowError, match="timed out"):
        await e.bash("sleep 5", timeout=0.1)


async def test_bash_default_cwd_is_project_root(tmp_path, monkeypatch):
    # Run from a tmp dir; bash() should still resolve to project root
    # (or fall back to tmp_path when no .aegis.py upstream).
    monkeypatch.chdir(tmp_path)
    e = _engine(tmp_path)
    proc = await e.bash("pwd")
    # We don't assert exact path (depends on find_project_root in test env)
    # — just that it executed and produced a string.
    assert proc.returncode == 0
    assert proc.stdout.strip()


async def test_bash_explicit_cwd_honored(tmp_path):
    e = _engine(tmp_path)
    proc = await e.bash("pwd", cwd=tmp_path)
    assert tmp_path.name in proc.stdout
```

- [ ] **Step 2: Run, expect fail**

```
uv run pytest tests/test_workflow_engine.py -q
# expected: AttributeError: 'WorkflowEngine' object has no attribute 'bash'
```

- [ ] **Step 3: Add `bash` to `engine.py`**

Add the import block near top:

```python
import asyncio
import os
import subprocess

from aegis.config import find_project_root
from aegis.workflow.decorator import WorkflowError
```

Append the method to `WorkflowEngine`:

```python
    async def bash(self, cmd: str, *,
                   cwd: str | Path | None = None,
                   timeout: float | None = None,
                   env: dict | None = None,
                   ) -> subprocess.CompletedProcess:
        """Async shell. cwd defaults to project root (find_project_root)
        or os.getcwd(); timeout=None means wait forever. On timeout,
        raises WorkflowError after killing the subprocess."""
        if cwd is None:
            cwd = str(find_project_root() or os.getcwd())
        proc = await asyncio.create_subprocess_shell(
            cmd, cwd=str(cwd), env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
            raise WorkflowError(
                f"bash timed out after {timeout}s: {cmd}")
        return subprocess.CompletedProcess(
            args=cmd, returncode=proc.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"))
```

- [ ] **Step 4: Run, expect pass**

```
uv run pytest tests/test_workflow_engine.py -q   # 11 passed
uv run pytest -q -m "not live"; echo "rc=$?"
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/workflow/engine.py tests/test_workflow_engine.py
git commit -m "feat(workflow): engine.bash — async shell + timeout + project-root cwd"
git push
```

### Task 1.4 — runner.run_workflow

**Files:**
- Create: `src/aegis/workflow/runner.py`
- Modify: `src/aegis/workflow/__init__.py` (re-export `run_workflow`)
- Test: `tests/test_workflow_runner.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_workflow_runner.py
from __future__ import annotations

import pytest

from aegis.workflow import run_workflow, workflow, WorkflowError
from aegis.workflow.decorator import _REGISTRY


@pytest.fixture(autouse=True)
def clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


class _StubBridge:
    queue_manager = None
    inbox_router = None
    def list_sessions(self): return []
    def list_agents(self): return []


async def _run(name, kwargs, **kw):
    return await run_workflow(
        name, kwargs,
        bridge=_StubBridge(), queue_manager=None, inbox_router=None,
        **kw)


async def test_runner_success_returns_status_ok(tmp_path):
    @workflow
    async def echo_back(engine, *, x):
        return x.upper()
    out = await _run("echo_back", {"x": "alex"}, state_dir=tmp_path)
    assert out["status"] == "ok"
    assert out["result"] == "ALEX"
    assert "workflow_run_id" in out


async def test_runner_workflow_error_returns_status_error(tmp_path):
    @workflow
    async def expected_fail(engine):
        raise WorkflowError("predicate violated: x")
    out = await _run("expected_fail", {}, state_dir=tmp_path)
    assert out["status"] == "error"
    assert "predicate violated: x" in out["error"]
    assert "workflow_run_id" in out


async def test_runner_unexpected_exception_tags_unexpected(tmp_path):
    @workflow
    async def crash(engine):
        raise ValueError("oh no")
    out = await _run("crash", {}, state_dir=tmp_path)
    assert out["status"] == "error"
    assert "unexpected: ValueError: oh no" in out["error"]


async def test_runner_unknown_workflow_returns_error_with_listing(tmp_path):
    @workflow
    async def alpha(engine): return None
    out = await _run("ghost", {}, state_dir=tmp_path)
    assert out["status"] == "error"
    assert "unknown workflow" in out["error"]
    assert "alpha" in out["error"]


async def test_runner_caller_handle_threaded_to_engine(tmp_path):
    captured = {}
    @workflow
    async def grab_caller(engine):
        captured["caller"] = engine.caller_handle
    out = await _run("grab_caller", {},
                     caller_handle="lucid-knuth", state_dir=tmp_path)
    assert out["status"] == "ok"
    assert captured["caller"] == "lucid-knuth"
```

- [ ] **Step 2: Run, expect ImportError**

```
uv run pytest tests/test_workflow_runner.py -q
# expected: ERROR — cannot import name 'run_workflow'
```

- [ ] **Step 3: Implement runner**

```python
# src/aegis/workflow/runner.py
from __future__ import annotations

from pathlib import Path
from typing import Any

from aegis.queue.schema import new_ulid
from aegis.workflow.decorator import (
    WorkflowError, get_workflow, list_workflows,
)
from aegis.workflow.engine import WorkflowEngine


async def run_workflow(
    name: str, kwargs: dict, *,
    bridge: Any, queue_manager: Any, inbox_router: Any,
    caller_handle: str | None = None,
    state_dir: Path | None = None,
) -> dict:
    """Build a WorkflowEngine, invoke the named workflow with kwargs,
    auto-drain touched handles + auto-close spawned handles in finally.
    Returns {status, result?, error?, workflow_run_id}.
    """
    run_id = new_ulid()
    fn = get_workflow(name)
    if fn is None:
        return {
            "status": "error",
            "error": (f"unknown workflow: {name!r}. "
                      f"Available: {list_workflows()}"),
            "workflow_run_id": run_id,
        }
    engine = WorkflowEngine(
        workflow_name=name, workflow_run_id=run_id,
        bridge=bridge, queue_manager=queue_manager,
        inbox_router=inbox_router,
        caller_handle=caller_handle, state_dir=state_dir)
    try:
        result = await fn(engine, **kwargs)
        return {"status": "ok", "result": result, "workflow_run_id": run_id}
    except WorkflowError as e:
        return {"status": "error", "error": str(e),
                "workflow_run_id": run_id}
    except Exception as e:  # noqa: BLE001 — unexpected crash → tagged
        import traceback
        traceback.print_exc()
        return {
            "status": "error",
            "error": f"unexpected: {type(e).__name__}: {e}",
            "workflow_run_id": run_id,
        }
    finally:
        # Auto-drain touched handles (best-effort) + auto-close spawned.
        # In VS1 these are no-ops; VS3 adds the real logic.
        await _runner_cleanup(engine)


async def _runner_cleanup(engine: WorkflowEngine) -> None:
    """Best-effort teardown. VS1 placeholder; VS3 fills in drain + close."""
    # touched-handles drain — VS3.
    # spawned-handles close — VS3.
    return
```

Update `__init__.py`:

```python
from aegis.workflow.decorator import (
    WorkflowError, get_workflow, list_workflows, workflow,
)
from aegis.workflow.engine import WorkflowEngine
from aegis.workflow.runner import run_workflow

__all__ = [
    "WorkflowEngine", "WorkflowError",
    "get_workflow", "list_workflows", "run_workflow", "workflow",
]
```

- [ ] **Step 4: Run, expect pass**

```
uv run pytest tests/test_workflow_runner.py -q   # 5 passed
uv run pytest -q -m "not live"; echo "rc=$?"
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/workflow/runner.py src/aegis/workflow/__init__.py \
  tests/test_workflow_runner.py
git commit -m "feat(workflow): runner.run_workflow — {status, result?, error?} shape"
git push
```

### Task 1.5 — CLI: `aegis workflow list` + `aegis workflow run <name>`

**Files:**
- Modify: `src/aegis/cli.py`
- Test: `tests/test_workflow_cli.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_workflow_cli.py
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from aegis.cli import app
from aegis.workflow import workflow
from aegis.workflow.decorator import _REGISTRY


@pytest.fixture(autouse=True)
def clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


@pytest.fixture
def sample_aegis_py(tmp_path, monkeypatch):
    """Write a minimal .aegis.py that registers one workflow."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.py").write_text("""
from aegis import Agent
from aegis.workflow import workflow

agents = {"default": Agent(harness="claude-code", model="opus",
                            effort="high", permission="auto")}
default_agent = "default"

@workflow
async def hello(engine, *, name="world"):
    engine.log(f"Hi {name}!")
    return f"greeted {name}"
""")
    return tmp_path


def test_workflow_list_enumerates_registry(sample_aegis_py):
    res = CliRunner().invoke(app, ["workflow", "list"])
    assert res.exit_code == 0
    assert "hello" in res.output


def test_workflow_run_known_succeeds(sample_aegis_py):
    res = CliRunner().invoke(
        app, ["workflow", "run", "hello", "--name=Alex"])
    assert res.exit_code == 0
    assert "ok" in res.output
    assert "greeted Alex" in res.output


def test_workflow_run_unknown_exits_nonzero_with_listing(sample_aegis_py):
    res = CliRunner().invoke(app, ["workflow", "run", "ghost"])
    assert res.exit_code != 0
    assert "ghost" in res.output
    assert "hello" in res.output    # available list


def test_workflow_list_empty_registry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.py").write_text("""
from aegis import Agent
agents = {"default": Agent(harness="claude-code", model="opus",
                            effort="high", permission="auto")}
default_agent = "default"
""")
    res = CliRunner().invoke(app, ["workflow", "list"])
    assert res.exit_code == 0
    assert ("no workflows" in res.output.lower()
            or res.output.strip() == "")
```

- [ ] **Step 2: Run, expect fail**

```
uv run pytest tests/test_workflow_cli.py -q
# expected: command not found / unknown subcommand
```

- [ ] **Step 3: Add typer sub-group to cli.py**

Append to `src/aegis/cli.py` (after `serve` command, before `def main()`):

```python
# Workflow subcommand group --------------------------------------------
workflow_app = typer.Typer(help="Author + run aegis workflows.")
app.add_typer(workflow_app, name="workflow")


@workflow_app.command("list")
def workflow_list_cmd() -> None:
    """List all @workflow-decorated functions discovered via .aegis.py."""
    try:
        load_config()    # loads .aegis.py → @workflow decorators fire
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    from aegis.workflow import list_workflows
    names = list_workflows()
    if not names:
        _console.print("[yellow]no workflows registered.[/yellow]")
        return
    for n in names:
        typer.echo(n)


@workflow_app.command("run")
def workflow_run_cmd(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="workflow name"),
) -> None:
    """Run a workflow by name. Pass kwargs as ``--key=value``.

    All kwargs arrive as strings; the workflow body coerces if needed.
    """
    try:
        agents, default_agent = load_config()
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    root = find_project_root() or Path.cwd()
    try:
        queues = load_queues(root / ".aegis.py")
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    # Parse trailing --key=value kwargs from ctx.args.
    kwargs: dict[str, str] = {}
    for tok in ctx.args:
        if not tok.startswith("--") or "=" not in tok:
            _console.print(f"[red]bad kwarg: {tok!r} (use --key=value)[/red]")
            raise typer.Exit(1)
        k, v = tok[2:].split("=", 1)
        kwargs[k.replace("-", "_")] = v

    from aegis.workflow import get_workflow, run_workflow

    if get_workflow(name) is None:
        from aegis.workflow import list_workflows
        _console.print(
            f"[red]unknown workflow: {name!r}. "
            f"Available: {list_workflows()}[/red]")
        raise typer.Exit(1)

    def make_session(profile, mcp_url, handle):
        return get_driver(profile.harness).session(
            profile, str(root), mcp_url, handle)

    async def main_async():
        from aegis.core.manager import SessionManager
        from aegis.queue import InboxRouter, QueueManager
        inbox = InboxRouter(state_dir=root / ".aegis" / "state")
        mgr = SessionManager(agents, default_agent, make_session,
                             AegisMCP(), inbox=inbox)
        qm = QueueManager(queues, mgr, inbox,
                          state_dir=root / ".aegis" / "state")
        mgr.attach_queue_manager(qm)
        mgr._mcp.bind(mgr)
        await mgr._mcp.start()
        await qm.start()
        try:
            out = await run_workflow(
                name, kwargs, bridge=mgr, queue_manager=qm,
                inbox_router=inbox,
                state_dir=root / ".aegis" / "state")
        finally:
            await qm.stop()
            await mgr.close_all()
            await mgr._mcp.stop()
        return out

    out = asyncio.run(main_async())
    typer.echo(out["status"])
    if out["status"] == "ok":
        typer.echo(out.get("result", ""))
    else:
        typer.echo(out.get("error", ""))
        raise typer.Exit(1)


# typer needs context_settings to accept unknown --key=value args:
workflow_run_cmd.__typer_context_settings__ = {  # set on the wrapped fn
    "allow_extra_args": True,
    "ignore_unknown_options": True,
}
```

Wait — typer's `context_settings` is set differently. Adjust the
decorator call:

Replace the `@workflow_app.command("run")` decoration with:

```python
@workflow_app.command(
    "run",
    context_settings={"allow_extra_args": True,
                      "ignore_unknown_options": True})
def workflow_run_cmd(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="workflow name"),
) -> None:
    ...
```

(Remove the trailing `workflow_run_cmd.__typer_context_settings__ = …`
line — the above is the correct typer API.)

- [ ] **Step 4: Run, expect pass**

```
uv run pytest tests/test_workflow_cli.py -q   # 4 passed
uv run pytest -q -m "not live"; echo "rc=$?"
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/cli.py tests/test_workflow_cli.py
git commit -m "feat(cli): aegis workflow list + run with --key=value kwargs"
git push
```

### Task 1.6 — VS1 end-to-end smoke: hello workflow runs via CLI

**Files:**
- Modify: `tests/test_workflow_cli.py` (add an integration test that
  exercises the full path including log → JSONL writethrough).

- [ ] **Step 1: Append failing test**

```python
async def test_workflow_run_writes_log_jsonl(sample_aegis_py, tmp_path_factory):
    # The sample_aegis_py fixture monkeypatches cwd; the runner writes
    # .aegis/state/workflows/<run_id>.jsonl under project root.
    res = CliRunner().invoke(
        app, ["workflow", "run", "hello", "--name=Alex"])
    assert res.exit_code == 0
    from pathlib import Path
    log_dir = Path.cwd() / ".aegis" / "state" / "workflows"
    assert log_dir.exists()
    log_files = list(log_dir.glob("*.jsonl"))
    assert len(log_files) == 1
    content = log_files[0].read_text()
    assert "Hi Alex!" in content
```

- [ ] **Step 2: Run, expect pass** (no implementation needed — wiring
  already complete from prior tasks)

```
uv run pytest tests/test_workflow_cli.py -q
uv run pytest -q -m "not live"; echo "rc=$?"
```

- [ ] **Step 3: Commit + slice marker**

```bash
git add tests/test_workflow_cli.py
git commit -m "test(workflow): VS1 e2e — hello workflow runs via CLI + logs to JSONL

VS1 GREEN — scaffold + CLI + log + bash."
git push
```

---

## Vertical slice 2 — engine.delegate (compose on queue)

Adds the substrate seam that lets a workflow spawn a one-shot queue
worker and receive its result text. Composes on the v1 queue.

### Task 2.1 — engine.delegate via _DelegationPromise

**Files:**
- Modify: `src/aegis/workflow/engine.py` (add `_DelegationPromise` +
  `delegate` method)
- Modify: `tests/test_workflow_engine.py` (append delegate tests)

- [ ] **Step 1: Append failing tests**

```python
from aegis.queue import (
    InboxMessage, InboxRouter, Queue, QueueManager, sender_agent,
)
from aegis.events import AssistantText, Result


class _StubSM:
    def __init__(self):
        self._sessions = []
        self._scripts: dict[str, list] = {}
        self.closed: list[str] = []
    def script(self, handle, events):
        self._scripts[handle] = events
    def spawn(self, slug, *, opening_prompt=None, handle=None):
        from aegis.core.session import AgentSession
        evs = self._scripts.get(
            handle,
            [AssistantText(text="DONE"),
             Result(duration_ms=1, is_error=False, usage=None)])
        class _H:
            def __init__(s, e): s._e = list(e); s.sent = []; s.started = s.closed = False
            async def start(s): s.started = True
            async def send(s, t): s.sent.append(t)
            async def close(s): s.closed = True
            async def events(s):
                import asyncio
                for e in s._e:
                    await asyncio.sleep(0)
                    yield e
        sess = AgentSession(_H(evs), None, slug, handle)
        self._sessions.append(sess)
        if opening_prompt is not None:
            import asyncio
            asyncio.create_task(sess.send(opening_prompt))
        return sess
    async def close(self, handle):
        self.closed.append(handle)
        self._sessions = [s for s in self._sessions if s.handle != handle]


def _engine_with_queue(tmp_path, *, sm=None, inbox=None, qm=None,
                       worker_handle="w1"):
    sm = sm or _StubSM()
    inbox = inbox or InboxRouter()
    qm = qm or QueueManager(
        {"impl": Queue(name="impl", agent_profile="default",
                       max_parallel=1)},
        sm, inbox, handle_factory=lambda used: worker_handle)
    return (WorkflowEngine(
        workflow_name="t", workflow_run_id="01TID",
        bridge=_StubBridge(), queue_manager=qm, inbox_router=inbox,
        state_dir=tmp_path), sm, qm, inbox)


async def test_delegate_returns_worker_result_text(tmp_path):
    e, sm, _qm, _inbox = _engine_with_queue(tmp_path)
    sm.script("w1", [AssistantText(text="hello from worker"),
                     Result(duration_ms=1, is_error=False, usage=None)])
    out = await e.delegate("impl", "do the thing")
    assert out == "hello from worker"


async def test_delegate_worker_failure_raises_workflow_error(tmp_path):
    e, sm, _qm, _inbox = _engine_with_queue(tmp_path)
    sm.script("w1", [Result(duration_ms=1, is_error=True, usage=None)])
    with pytest.raises(WorkflowError, match="task .* failed"):
        await e.delegate("impl", "fail me")


async def test_delegate_unknown_queue_raises_workflow_error(tmp_path):
    e, _sm, _qm, _inbox = _engine_with_queue(tmp_path)
    with pytest.raises(WorkflowError, match="unknown queue"):
        await e.delegate("ghost", "x")


async def test_concurrent_delegates_use_unique_inbox_handles(tmp_path):
    # Two workers in parallel; each callback resolves the correct promise.
    sm = _StubSM()
    inbox = InboxRouter()
    qm = QueueManager(
        {"impl": Queue(name="impl", agent_profile="default",
                       max_parallel=2)},
        sm, inbox,
        handle_factory=lambda used: f"w{len(used) + 1}")
    sm.script("w1", [AssistantText(text="ONE"),
                     Result(duration_ms=1, is_error=False, usage=None)])
    sm.script("w2", [AssistantText(text="TWO"),
                     Result(duration_ms=1, is_error=False, usage=None)])
    e = WorkflowEngine(
        workflow_name="t", workflow_run_id="01TID",
        bridge=_StubBridge(), queue_manager=qm, inbox_router=inbox,
        state_dir=tmp_path)
    a, b = await asyncio.gather(
        e.delegate("impl", "a"),
        e.delegate("impl", "b"))
    assert {a, b} == {"ONE", "TWO"}
```

- [ ] **Step 2: Run, expect fail (no `delegate`)**

```
uv run pytest tests/test_workflow_engine.py -q
# expected: AttributeError or NotImplementedError
```

- [ ] **Step 3: Implement `delegate` + `_DelegationPromise`**

Add to top of `engine.py`:

```python
import asyncio

from aegis.queue.schema import InboxMessage, new_ulid as _new_ulid
```

Add class above `WorkflowEngine`:

```python
class _DelegationPromise:
    """Inbox-binding shape used by delegate(): receives one InboxMessage
    and resolves a Future. Lives only for the duration of one delegate
    call."""

    def __init__(self) -> None:
        self._future: asyncio.Future[InboxMessage] = (
            asyncio.get_event_loop().create_future())

    async def deliver(self, msg: InboxMessage) -> None:
        if not self._future.done():
            self._future.set_result(msg)

    def __await__(self):
        return self._future.__await__()
```

Add method to `WorkflowEngine`:

```python
    async def delegate(self, queue: str, payload: str) -> str:
        """Enqueue a one-shot task on the named queue; await the worker's
        callback; return its final assistant text. Raises WorkflowError
        on unknown queue, worker failure, or substrate error."""
        handle = f"workflow:{self.workflow_name}:{_new_ulid()}"
        promise = _DelegationPromise()
        self._inbox.bind_session(handle, promise)
        try:
            try:
                task_id, _pos = self._queue.enqueue(
                    queue, payload,
                    enqueued_by=handle, callback=True)
            except KeyError as e:
                raise WorkflowError(
                    f"unknown queue: {e.args[0]!r}") from e
            msg = await promise
            if msg.status == "error":
                raise WorkflowError(
                    f"task {task_id} failed: {msg.body}")
            return msg.body
        finally:
            self._inbox.unbind_session(handle)
```

- [ ] **Step 4: Run, expect pass**

```
uv run pytest tests/test_workflow_engine.py -q   # 15 passed
uv run pytest -q -m "not live"; echo "rc=$?"
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/workflow/engine.py tests/test_workflow_engine.py
git commit -m "feat(workflow): engine.delegate — composes on queue + callback await

VS2 GREEN — workflow-to-queue substrate seam."
git push
```

---

## Vertical slice 3 — Long-lived agents: spawn + send + drain + close + caller_handle

Adds the team-of-agents primitives. Touches `AppBridge` protocol +
SessionManager + AegisApp; runner gains auto-drain + auto-close.

### Task 3.1 — AppBridge gains `spawn` + `close`

**Files:**
- Modify: `src/aegis/mcp/bridge.py` (Protocol)
- Modify: `src/aegis/core/manager.py` (add async `spawn(profile, *, handle)`)
- Modify: `src/aegis/tui/app.py` (add async `spawn` + async `close`)
- Modify: `tests/test_mcp_bridge.py`, `tests/test_core_manager.py`,
  `tests/test_tui.py`

- [ ] **Step 1: Extend `tests/test_mcp_bridge.py`**

Replace `test_appbridge_requires_queue_manager_and_inbox_router` with:

```python
def test_appbridge_requires_full_surface():
    from aegis.queue import InboxRouter

    class FullImpl:
        queue_manager = object()
        inbox_router = InboxRouter()
        def list_sessions(self): return []
        def list_agents(self): return []
        async def handoff(self, a, b, c): return "ok"
        async def spawn(self, profile, *, handle=None): return "h"
        async def close(self, handle): return None

    class MissingSpawn:
        queue_manager = object()
        inbox_router = InboxRouter()
        def list_sessions(self): return []
        def list_agents(self): return []
        async def handoff(self, a, b, c): return "ok"
        async def close(self, handle): return None

    assert isinstance(FullImpl(), AppBridge)
    assert not isinstance(MissingSpawn(), AppBridge)
```

- [ ] **Step 2: Modify `bridge.py`**

```python
@runtime_checkable
class AppBridge(Protocol):
    queue_manager: object
    inbox_router: object

    def list_sessions(self) -> list[SessionInfo]: ...
    def list_agents(self) -> list[str]: ...
    async def handoff(self, from_handle: str, target_handle: str,
                      context: str) -> str: ...
    async def spawn(self, profile: str, *,
                    handle: str | None = None) -> str: ...
    async def close(self, handle: str) -> None: ...
```

- [ ] **Step 3: Modify `core/manager.py`**

`SessionManager` already has sync `spawn(slug, ...)` and async
`close(handle)`. Add a bridge-shaped async `spawn`:

```python
    async def spawn(self, profile: str, *,
                    handle: str | None = None) -> str:
        """AppBridge-shaped spawn. Wraps the existing sync spawn and
        returns the handle. profile is the agent slug from .aegis.py."""
        session = super().__getattribute__("spawn") if False else None
        # ^ guard against the name collision: use the sync spawn directly
        sess = SessionManager._sync_spawn(self, profile, handle=handle)
        return sess.handle
```

That's awkward — let me restructure. The cleanest approach: rename the
sync method to `_sync_spawn`, keep a compat alias `spawn` that's the
async bridge method but also callable as sync by the queue (which uses
positional + kwargs).

Actually simpler: **add a separate async method** with a different name
internally; have the protocol's `spawn` route to it. But the Protocol
defines `spawn` exactly. Use the same name — Python allows shadowing:

```python
    # Rename the existing sync spawn:
    def _sync_spawn(self, slug: str | None = None, *,
                    opening_prompt: str | None = None,
                    handle: str | None = None) -> AgentSession:
        # ... existing body ...

    async def spawn(self, profile: str, *,
                    handle: str | None = None) -> str:
        """AppBridge-shaped async spawn. Returns the new handle."""
        sess = self._sync_spawn(profile, handle=handle)
        return sess.handle
```

But `QueueManager._try_dispatch` calls `self._sm.spawn(q.agent_profile,
opening_prompt=..., handle=...)` — that's sync. We need the sync method
to stay reachable.

Best fix: rename the sync spawn to `_sync_spawn`, update
`QueueManager._try_dispatch` to call `_sync_spawn` instead. Add `async
spawn` for the AppBridge protocol.

In `src/aegis/queue/manager.py`, replace the existing line in
`_try_dispatch`:

```python
            session = self._sm.spawn(q.agent_profile,
                                     opening_prompt=task.payload,
                                     handle=worker_handle)
```

with:

```python
            # Use the sync seam — async AppBridge.spawn is for workflow.
            sync_spawn = getattr(self._sm, "_sync_spawn", self._sm.spawn)
            session = sync_spawn(q.agent_profile,
                                 opening_prompt=task.payload,
                                 handle=worker_handle)
```

(The `getattr` fallback keeps the existing tests' stub `StubSessionManager`
working — they implement `spawn` directly, not `_sync_spawn`.)

In `src/aegis/core/manager.py`, rename `def spawn(self, slug=None, *,
opening_prompt=None, handle=None) -> AgentSession:` → `def _sync_spawn(
self, slug=None, *, ...)`. Add the async `spawn` per above.

- [ ] **Step 4: Modify `tui/app.py`**

`AegisApp` currently has the `_SessionManagerAdapter` for queue spawn.
Add an async `spawn` + async `close` on the App itself that route to
the adapter:

```python
    async def spawn(self, profile: str, *,
                    handle: str | None = None) -> str:
        """AppBridge-shaped: spawn a long-lived agent as a TUI pane."""
        sm_adapter = _SessionManagerAdapter(self)
        sess = sm_adapter.spawn(profile, handle=handle)
        return sess.handle

    async def close(self, handle: str) -> None:
        """AppBridge-shaped: close a pane by handle."""
        pane = next((p for p in self._panes if p.handle == handle), None)
        if pane is not None:
            await self._close_pane(pane)
            self._refresh_tabbar()
```

Note: `_SessionManagerAdapter.spawn` schedules the async mount via
`App.run_worker` and returns `pane._core` (an AgentSession). `pane._core.handle == handle`.

- [ ] **Step 5: Extend `tests/test_core_manager.py`**

Add:

```python
@pytest.mark.asyncio
async def test_sessionmanager_async_spawn_returns_handle():
    m = make_mgr()
    handle = await m.spawn("default", handle="vivid-laplace")
    assert handle == "vivid-laplace"
    assert any(s.handle == "vivid-laplace" for s in m._sessions)


@pytest.mark.asyncio
async def test_sync_spawn_still_works_for_queue():
    m = make_mgr()
    s = m._sync_spawn("default", handle="w1")
    assert s.handle == "w1"
```

- [ ] **Step 6: Extend `tests/test_tui.py`**

```python
@pytest.mark.asyncio
async def test_aegisapp_spawn_mounts_pane_and_returns_handle():
    app = _app(_factory(FakeSession()))
    async with app.run_test() as pilot:
        h = await app.spawn("default", handle="vivid-laplace")
        await pilot.pause()
        assert h == "vivid-laplace"
        assert any(p.handle == "vivid-laplace" for p in app._panes)


@pytest.mark.asyncio
async def test_aegisapp_close_removes_pane():
    app = _app(_factory(FakeSession(), FakeSession()))
    async with app.run_test() as pilot:
        h = await app.spawn("default", handle="vivid-laplace")
        await pilot.pause()
        await app.close(h)
        await pilot.pause()
        assert not any(p.handle == "vivid-laplace" for p in app._panes)
```

- [ ] **Step 7: Run, expect pass**

```
uv run pytest tests/test_mcp_bridge.py tests/test_core_manager.py \
  tests/test_tui.py tests/test_queue_manager.py -q
uv run pytest -q -m "not live"; echo "rc=$?"
```

- [ ] **Step 8: Commit**

```bash
git add src/aegis/mcp/bridge.py src/aegis/core/manager.py \
  src/aegis/tui/app.py src/aegis/queue/manager.py \
  tests/test_mcp_bridge.py tests/test_core_manager.py tests/test_tui.py
git commit -m "feat(bridge): AppBridge.spawn + close — long-lived agent seam

SessionManager.spawn renamed to _sync_spawn (kept reachable for queue's
_try_dispatch); AppBridge async spawn/close added for workflow engine."
git push
```

### Task 3.2 — engine.spawn + engine.close

**Files:**
- Modify: `src/aegis/workflow/engine.py`
- Modify: `tests/test_workflow_engine.py`

- [ ] **Step 1: Append failing tests**

```python
class _SpawningStubBridge:
    def __init__(self):
        self._spawned = []
        self._closed = []
        self.queue_manager = None
        self.inbox_router = None
    def list_sessions(self): return []
    def list_agents(self): return []
    async def handoff(self, a, b, c): return "ok"
    async def spawn(self, profile, *, handle=None):
        h = handle or f"auto-{len(self._spawned) + 1}"
        self._spawned.append((profile, h))
        return h
    async def close(self, handle):
        self._closed.append(handle)


async def test_engine_spawn_tracks_handle_and_returns_it(tmp_path):
    br = _SpawningStubBridge()
    e = WorkflowEngine(workflow_name="t", workflow_run_id="01",
                       bridge=br, queue_manager=None, inbox_router=None,
                       state_dir=tmp_path)
    h = await e.spawn("reviewer", handle="r1")
    assert h == "r1"
    assert "r1" in e._spawned_handles
    assert ("reviewer", "r1") in br._spawned


async def test_engine_close_removes_handle_and_is_idempotent(tmp_path):
    br = _SpawningStubBridge()
    e = WorkflowEngine(workflow_name="t", workflow_run_id="01",
                       bridge=br, queue_manager=None, inbox_router=None,
                       state_dir=tmp_path)
    h = await e.spawn("reviewer", handle="r1")
    await e.close(h)
    assert "r1" not in e._spawned_handles
    assert "r1" in br._closed
    # Idempotent: closing again is a no-op
    await e.close(h)
    assert br._closed == ["r1"]    # not appended twice
```

- [ ] **Step 2: Run, expect fail**

```
uv run pytest tests/test_workflow_engine.py -q
# AttributeError: 'WorkflowEngine' object has no attribute 'spawn'
```

- [ ] **Step 3: Add `spawn` + `close` to `WorkflowEngine`**

```python
    async def spawn(self, profile: str, *,
                    handle: str | None = None) -> str:
        """Spawn a long-lived agent through the bridge. Tracks handle for
        auto-close on workflow exit. Returns the handle."""
        h = await self._bridge.spawn(profile, handle=handle)
        self._spawned_handles.add(h)
        return h

    async def close(self, handle: str) -> None:
        """Close a long-lived agent. Idempotent — silent no-op if the
        handle is unknown to this engine."""
        if handle not in self._spawned_handles:
            return
        self._spawned_handles.discard(handle)
        try:
            await self._bridge.close(handle)
        except Exception:  # noqa: BLE001 — close is best-effort
            pass
```

- [ ] **Step 4: Run, expect pass**

```
uv run pytest tests/test_workflow_engine.py -q   # 17 passed
uv run pytest -q -m "not live"; echo "rc=$?"
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/workflow/engine.py tests/test_workflow_engine.py
git commit -m "feat(workflow): engine.spawn + engine.close (tracked + idempotent)"
git push
```

### Task 3.3 — engine.send (sync fire-and-forget)

**Files:**
- Modify: `src/aegis/workflow/engine.py`
- Modify: `tests/test_workflow_engine.py`

- [ ] **Step 1: Append failing tests**

```python
class _RecordingInbox:
    """Captures every deliver call; doesn't actually route."""
    def __init__(self):
        self.delivered: list[tuple[str, InboxMessage]] = []
        self._sessions = {}
    def bind_session(self, handle, session):
        self._sessions[handle] = session
    def unbind_session(self, handle):
        self._sessions.pop(handle, None)
    async def deliver(self, handle, msg):
        self.delivered.append((handle, msg))


async def test_engine_send_queues_tagged_message(tmp_path):
    inbox = _RecordingInbox()
    e = WorkflowEngine(workflow_name="tdd_step", workflow_run_id="01",
                       bridge=_StubBridge(), queue_manager=None,
                       inbox_router=inbox, state_dir=tmp_path)
    e.send("lucid-knuth", "do the thing")
    # send schedules an asyncio task; let it run.
    await asyncio.sleep(0)
    assert len(inbox.delivered) == 1
    handle, msg = inbox.delivered[0]
    assert handle == "lucid-knuth"
    assert msg.sender == "workflow:tdd_step"
    assert msg.body == "do the thing"
    assert msg.timestamp        # ISO string
    assert "lucid-knuth" in e._touched_handles


async def test_engine_send_does_not_await(tmp_path):
    inbox = _RecordingInbox()
    e = WorkflowEngine(workflow_name="t", workflow_run_id="01",
                       bridge=_StubBridge(), queue_manager=None,
                       inbox_router=inbox, state_dir=tmp_path)
    # send is sync (no await); should return immediately
    e.send("h", "msg")
    # touched_handles populated synchronously
    assert "h" in e._touched_handles
```

- [ ] **Step 2: Run, expect fail**

```
uv run pytest tests/test_workflow_engine.py -q
# AttributeError: 'WorkflowEngine' object has no attribute 'send'
```

- [ ] **Step 3: Add `send` to `WorkflowEngine`**

```python
    def send(self, handle: str, message: str) -> None:
        """Enqueue a substrate-tagged message in handle's inbox.
        Sync, fire-and-forget. Returns immediately; the actual delivery
        is scheduled as an asyncio task (which inherits the calling
        context — workflows always run on the aegis event loop)."""
        msg = InboxMessage(
            sender=f"workflow:{self.workflow_name}",
            timestamp=self._now(),
            body=message)
        self._touched_handles.add(handle)
        asyncio.create_task(self._inbox.deliver(handle, msg))
```

- [ ] **Step 4: Run, expect pass**

```
uv run pytest tests/test_workflow_engine.py -q   # 19 passed
uv run pytest -q -m "not live"; echo "rc=$?"
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/workflow/engine.py tests/test_workflow_engine.py
git commit -m "feat(workflow): engine.send — sync fire-and-forget through InboxRouter"
git push
```

### Task 3.4 — engine.drain with per-handle ceiling

**Files:**
- Modify: `src/aegis/workflow/engine.py`
- Modify: `tests/test_workflow_engine.py`

- [ ] **Step 1: Append failing tests**

```python
from aegis.core.session import AgentSession
from aegis.tui.state import AgentState


class _LiveFakeHarness:
    """Fake harness whose events generator finishes one short turn."""
    def __init__(self):
        self.sent = []
        self.started = self.closed = False
    async def start(self): self.started = True
    async def send(self, t): self.sent.append(t)
    async def close(self): self.closed = True
    async def events(self):
        import asyncio as _a
        await _a.sleep(0)
        yield AssistantText("ok")
        yield Result(duration_ms=1, is_error=False, usage=None)


async def test_drain_returns_when_target_idle(tmp_path):
    inbox = InboxRouter()
    h = "lucid-knuth"
    sess = AgentSession(_LiveFakeHarness(), None, "default", h, inbox=inbox)
    inbox.bind_session(h, sess)
    e = WorkflowEngine(workflow_name="t", workflow_run_id="01",
                       bridge=_StubBridge(), queue_manager=None,
                       inbox_router=inbox, state_dir=tmp_path,
                       drain_timeout=2.0)
    e.send(h, "go")
    await e.drain(h)
    assert sess.state is AgentState.ready


async def test_drain_no_touched_handle_is_noop(tmp_path):
    e = WorkflowEngine(workflow_name="t", workflow_run_id="01",
                       bridge=_StubBridge(), queue_manager=None,
                       inbox_router=InboxRouter(), state_dir=tmp_path)
    # Touched-handle set is empty; drain returns immediately.
    await e.drain()


async def test_drain_timeout_logs_warning_and_returns(tmp_path, capfd):
    inbox = InboxRouter()
    h = "stuck"
    class _NeverFinishes:
        sent = []; started = closed = False
        async def start(self): pass
        async def send(self, t): self.sent.append(t)
        async def close(self): pass
        async def events(self):
            import asyncio as _a
            await _a.Event().wait()
            if False: yield  # pragma: no cover
    sess = AgentSession(_NeverFinishes(), None, "default", h, inbox=inbox)
    inbox.bind_session(h, sess)
    e = WorkflowEngine(workflow_name="t", workflow_run_id="01",
                       bridge=_StubBridge(), queue_manager=None,
                       inbox_router=inbox, state_dir=tmp_path,
                       drain_timeout=0.05)
    e.send(h, "go")
    await e.drain(h)
    out = capfd.readouterr().err
    assert "drain timed out" in out
    assert h in out
```

- [ ] **Step 2: Run, expect fail**

- [ ] **Step 3: Add `drain` to `WorkflowEngine`**

```python
    async def drain(self, handle: str | None = None) -> None:
        """Await each touched handle's session to reach state == ready.
        If handle is None, drain all touched handles. Per-handle ceiling
        of self._drain_timeout; on timeout, log a warning and continue
        (don't trap workflow shutdown on a hung agent)."""
        targets = [handle] if handle is not None else list(self._touched_handles)
        for h in targets:
            await self._drain_one(h)

    async def _drain_one(self, handle: str) -> None:
        # Get the session via the inbox router's binding (the only
        # place handles are mapped to AgentSession objects from the
        # engine's vantage).
        session = self._inbox._sessions.get(handle) if self._inbox else None
        if session is None:
            return
        from aegis.tui.state import AgentState
        # Already idle? Done.
        if session.state is AgentState.ready and not getattr(
                session, "_inbox_buffer", []):
            return
        # Wait for next transition to ready, polling at 20Hz with the
        # configured timeout ceiling.
        deadline = asyncio.get_event_loop().time() + self._drain_timeout
        while True:
            if session.state is AgentState.ready and not getattr(
                    session, "_inbox_buffer", []):
                return
            if asyncio.get_event_loop().time() >= deadline:
                self.log(
                    f"drain timed out after {self._drain_timeout}s for "
                    f"handle={handle!r} (state={session.state.value})")
                return
            await asyncio.sleep(0.05)
```

- [ ] **Step 4: Run, expect pass**

```
uv run pytest tests/test_workflow_engine.py -q   # 22 passed
uv run pytest -q -m "not live"; echo "rc=$?"
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/workflow/engine.py tests/test_workflow_engine.py
git commit -m "feat(workflow): engine.drain — await idle with per-handle ceiling"
git push
```

### Task 3.5 — Runner auto-drain + auto-close

**Files:**
- Modify: `src/aegis/workflow/runner.py` (replace `_runner_cleanup`)
- Modify: `tests/test_workflow_runner.py`

- [ ] **Step 1: Append failing tests**

```python
async def test_runner_auto_closes_spawned_handles(tmp_path):
    closed: list[str] = []

    class _Br:
        queue_manager = None
        inbox_router = None
        def list_sessions(self): return []
        def list_agents(self): return []
        async def handoff(self, a, b, c): return "ok"
        async def spawn(self, profile, *, handle=None):
            return handle or "auto"
        async def close(self, handle):
            closed.append(handle)

    @workflow
    async def leaks(engine):
        await engine.spawn("default", handle="a")
        await engine.spawn("default", handle="b")
        # workflow returns without closing either

    await run_workflow("leaks", {}, bridge=_Br(),
                       queue_manager=None, inbox_router=None,
                       state_dir=tmp_path)
    assert sorted(closed) == ["a", "b"]


async def test_runner_auto_drains_touched_handles(tmp_path):
    from aegis.queue import InboxRouter
    from aegis.core.session import AgentSession

    drained: list[str] = []

    class _CountingDrainEngine:
        pass  # we just observe via the real drain timing instead

    inbox = InboxRouter()
    handle = "h"
    sess = AgentSession(_LiveFakeHarness(), None, "default", handle,
                        inbox=inbox)
    inbox.bind_session(handle, sess)

    class _Br:
        queue_manager = None
        inbox_router = inbox
        def list_sessions(self): return []
        def list_agents(self): return []
        async def handoff(self, a, b, c): return "ok"
        async def spawn(self, profile, *, handle=None): return handle or "x"
        async def close(self, h): pass

    @workflow
    async def send_then_return(engine):
        engine.send(handle, "go")
        # don't drain explicitly; runner's finally should drain

    out = await run_workflow(
        "send_then_return", {}, bridge=_Br(),
        queue_manager=None, inbox_router=inbox, state_dir=tmp_path)
    assert out["status"] == "ok"
    # After runner returns, the touched session must be idle.
    assert sess.state.value == "ready"
```

- [ ] **Step 2: Run, expect fail (cleanup is still a no-op)**

- [ ] **Step 3: Replace `_runner_cleanup` in `runner.py`**

```python
async def _runner_cleanup(engine: WorkflowEngine) -> None:
    """Best-effort teardown: drain touched handles, close spawned ones.
    Each step is independently best-effort so one failure doesn't block
    the others."""
    try:
        if engine._touched_handles:
            await engine.drain()
    except Exception:  # noqa: BLE001
        pass
    # Iterate over a snapshot — close() mutates the set.
    for h in list(engine._spawned_handles):
        try:
            await engine.close(h)
        except Exception:  # noqa: BLE001
            pass
```

- [ ] **Step 4: Run, expect pass**

```
uv run pytest tests/test_workflow_runner.py -q   # 7 passed
uv run pytest -q -m "not live"; echo "rc=$?"
```

- [ ] **Step 5: Commit**

```bash
git add src/aegis/workflow/runner.py tests/test_workflow_runner.py
git commit -m "feat(workflow): runner auto-drains touched + auto-closes spawned in finally

VS3 GREEN — long-lived agent primitives + caller_handle + auto-teardown."
git push
```

---

## Vertical slice 4 — MCP integration (aegis_run_workflow)

Agents can invoke workflows via MCP; result delivers as a callback into
the producer's inbox tagged `sender="workflow:<name>"`.

### Task 4.1 — aegis_run_workflow MCP tool

**Files:**
- Modify: `src/aegis/mcp/server.py` (register `aegis_run_workflow`;
  extend BRIEFING)
- Create: `tests/test_workflow_mcp.py`
- Modify: `tests/test_mcp_server.py` (FakeBridge stubs spawn/close;
  tool-list test adds `aegis_run_workflow`)

- [ ] **Step 1: Write `tests/test_workflow_mcp.py`**

```python
from __future__ import annotations

import asyncio

import pytest

from aegis.mcp.server import build_server
from aegis.queue import InboxRouter, sender_agent
from aegis.workflow import workflow
from aegis.workflow.decorator import _REGISTRY


@pytest.fixture(autouse=True)
def clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


class _Bridge:
    def __init__(self):
        self.queue_manager = None
        self.inbox_router = InboxRouter()
    def list_sessions(self): return []
    def list_agents(self): return []
    async def handoff(self, a, b, c): return "ok"
    async def spawn(self, profile, *, handle=None): return handle or "x"
    async def close(self, h): pass


async def _call(server, name, **kwargs):
    tools = await server.list_tools()
    tool = next(t for t in tools if t.name == name)
    result = await tool.run(kwargs)
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
        return sc["result"]
    if sc is not None:
        return sc
    return result.content[0].text


async def test_run_workflow_unknown_name_returns_error_synchronously():
    @workflow
    async def alpha(engine): return "done"
    br = _Bridge()
    srv = build_server(br)
    out = await _call(srv, "aegis_run_workflow",
                      name="ghost", kwargs={},
                      from_handle="me", callback=True)
    assert "error" in out
    assert "ghost" in out["error"]
    assert "alpha" in out["error"]


async def test_run_workflow_known_returns_run_id_and_callbacks(tmp_path):
    @workflow
    async def echo(engine, *, x):
        return x
    br = _Bridge()
    srv = build_server(br)
    out = await _call(srv, "aegis_run_workflow",
                      name="echo", kwargs={"x": "hi"},
                      from_handle="lucid-knuth", callback=True)
    assert "workflow_run_id" in out
    # Let the scheduled workflow task run + deliver the callback.
    for _ in range(20):
        await asyncio.sleep(0.01)
        pending = br.inbox_router.pending("lucid-knuth")
        if pending:
            break
    pending = br.inbox_router.pending("lucid-knuth")
    assert len(pending) == 1
    msg = pending[0]
    assert msg.sender == "workflow:echo"
    assert msg.status == "ok"
    assert msg.body == "hi"
    assert msg.task_id == out["workflow_run_id"]
```

- [ ] **Step 2: Run, expect fail (tool not registered)**

- [ ] **Step 3: Modify `mcp/server.py`** — add tool inside `build_server`:

```python
    @server.tool
    async def aegis_run_workflow(name: str, kwargs: dict | None = None,
                                 from_handle: str = "",
                                 callback: bool = True) -> dict:
        """Run a workflow by name. Returns {workflow_run_id, status:
        "running"} immediately. If callback=true, the result lands in
        your inbox (sender=workflow:<name>) when the workflow completes;
        if callback=false, the result is dropped (use only if you don't
        need recovery).

        from_handle is your own aegis handle (read from your system
        prompt). The workflow sees it as engine.caller_handle.
        """
        from aegis.queue import InboxMessage, sender_agent
        from aegis.queue.schema import new_ulid, now_iso
        from aegis.workflow import (
            WorkflowError, get_workflow, list_workflows, run_workflow,
        )

        if get_workflow(name) is None:
            return {
                "error": (f"unknown workflow: {name!r}. "
                          f"Available: {list_workflows()}")}

        async def _run_and_callback():
            out = await run_workflow(
                name, kwargs or {},
                bridge=bridge,
                queue_manager=bridge.queue_manager,
                inbox_router=bridge.inbox_router,
                caller_handle=from_handle or None,
                state_dir=None)
            if not callback or not from_handle:
                return
            ok = out["status"] == "ok"
            body = out.get("result") if ok else out.get("error", "")
            msg = InboxMessage(
                sender=f"workflow:{name}",
                timestamp=now_iso(),
                body=str(body) if body is not None else "",
                task_id=out["workflow_run_id"],
                status=("ok" if ok else "error"))
            await bridge.inbox_router.deliver(from_handle, msg)

        # Schedule the workflow; don't block this tool call on it.
        asyncio.create_task(_run_and_callback())
        # Use the same run-id the runner will mint? It doesn't tell us
        # before running. Mint a separate "outer" id for the immediate
        # ack; the inbox callback carries the real internal id.
        return {"workflow_run_id": new_ulid(), "status": "running"}
```

(There's a known v1 quirk: the outer ack id and the inner runner id are
different. Acceptable for v1; the inner id is what shows up in the
callback's `task_id`. A follow-up could thread the runner's id back.)

Add `import asyncio` if missing at the top of `mcp/server.py`.

Add the BRIEFING entry — append to the tools list inside `BRIEFING`:

```python
    "  - aegis_run_workflow(name, kwargs, from_handle, callback=true) : "
    "invoke a workflow (Python procedure that drives a sequence of "
    "agent interactions with predicate-verified steps). Returns "
    "{workflow_run_id, status: 'running'} immediately. With callback=true "
    "the result lands in your inbox tagged sender=workflow:<name> when "
    "done. If you invoke a workflow on yourself, it can drive your "
    "session through multiple turns via the engine.send/drain pattern.\n"
```

- [ ] **Step 4: Update `tests/test_mcp_server.py`**

`FakeBridge` (already has `queue_manager` + `inbox_router`) needs
`spawn` + `close`:

```python
    async def spawn(self, profile, *, handle=None):
        return handle or "stub-handle"
    async def close(self, handle):
        pass
```

`test_build_server_registers_all_aegis_tools`:

```python
    assert {t.name for t in tools} == {
        "aegis_meta", "aegis_list_sessions",
        "aegis_list_agents", "aegis_handoff",
        "aegis_enqueue", "aegis_task_status",
        "aegis_run_workflow"}
```

`test_meta_and_priming_updated`:

```python
    for t in ("aegis_list_sessions", "aegis_list_agents",
              "aegis_handoff", "aegis_enqueue", "aegis_task_status",
              "aegis_run_workflow"):
        assert t in b
```

- [ ] **Step 5: Run, expect pass**

```
uv run pytest tests/test_workflow_mcp.py tests/test_mcp_server.py -q
uv run pytest -q -m "not live"; echo "rc=$?"
```

- [ ] **Step 6: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_workflow_mcp.py \
  tests/test_mcp_server.py
git commit -m "feat(mcp): aegis_run_workflow tool — callback into producer inbox

VS4 GREEN — agents can invoke workflows; result returns via inbox."
git push
```

---

## Vertical slice 5 — Live smoke (real claude TDD workflow)

### Task 5.1 — examples/tdd_step.py + live test

**Files:**
- Create: `examples/__init__.py` (empty)
- Create: `examples/tdd_step.py`
- Create: `tests/test_workflow_live.py`
- Modify: `AGENTS.md`

- [ ] **Step 1: Create `examples/__init__.py`**

```python
# Example workflows ship-able via `from examples.tdd_step import tdd_step`
# inside a project's .aegis.py.
```

- [ ] **Step 2: Create `examples/tdd_step.py`**

```python
"""Canonical TDD-on-a-plan-step workflow.

Usage in .aegis.py:

    from examples.tdd_step import tdd_step    # noqa: F401 — registers

Then either CLI:

    aegis workflow run tdd_step \\
        --plan-step="VS1 inbox" \\
        --test-command="uv run pytest -k inbox" \\
        --test-path="tests/test_inbox.py"

Or via MCP from any agent:

    aegis_run_workflow(name="tdd_step",
                       kwargs={"plan_step": "...", "test_command": "...",
                               "test_path": "tests/test_inbox.py"},
                       from_handle="<your-handle>", callback=true)
"""
from __future__ import annotations

from aegis.workflow import workflow, WorkflowError


@workflow
async def tdd_step(engine, *, plan_step: str,
                   test_command: str = "uv run pytest",
                   test_path: str = "tests/test_step.py"):
    """Run one TDD cycle. Subject = caller (if MCP-invoked) or a fresh
    queue worker (if CLI-invoked). Returns when tests are green;
    raises WorkflowError on hard failure (predicate violated)."""
    subject = engine.caller_handle or await engine.spawn("worker-sonnet")
    spawned = subject != engine.caller_handle
    try:
        # 1. Write failing tests at the known path.
        engine.send(subject,
            f"Write failing tests at {test_path} for: {plan_step}. "
            f"Cover the spec.")
        await engine.drain(subject)

        # 2. Verify they fail.
        proc = await engine.bash(f"{test_command} {test_path}")
        if proc.returncode == 0:
            raise WorkflowError(
                f"tests at {test_path} passed without implementation")

        # 3. Implement.
        engine.send(subject,
            f"Make {test_path} pass.\n\nFailing output:\n{proc.stdout}")
        await engine.drain(subject)

        # 4. Verify pass; retry up to 3 with feedback.
        for attempt in range(3):
            proc = await engine.bash(f"{test_command} {test_path}")
            if proc.returncode == 0:
                engine.log(f"green after {attempt + 1} attempt(s)")
                return f"green: {test_path}"
            engine.send(subject,
                f"Still failing:\n{proc.stdout}\n\nFix.")
            await engine.drain(subject)
        raise WorkflowError(
            f"tests still red after 3 attempts: {plan_step}")
    finally:
        if spawned:
            await engine.close(subject)
```

- [ ] **Step 3: Create `tests/test_workflow_live.py`**

```python
"""Live e2e for the workflow scaffold using the TDD workflow against
a tiny target. Skips when `claude` not on PATH."""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from aegis.config import Agent
from aegis.core.manager import SessionManager
from aegis.drivers import get_driver
from aegis.mcp import AegisMCP
from aegis.queue import InboxRouter, Queue, QueueManager
from aegis.workflow import run_workflow

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(shutil.which("claude") is None,
                       reason="claude CLI not on PATH; live test skipped"),
]


async def test_live_tdd_workflow_writes_and_passes_trivial_test(tmp_path):
    """Tiny TDD loop: ask the worker to write a test asserting 1+1==2,
    verify it fails on a stub module, implement, verify green."""
    # Register the workflow.
    from examples.tdd_step import tdd_step                    # noqa: F401

    agent = Agent(harness="claude-code", model="sonnet",
                  effort="low", permission="full")
    agents = {"default": agent, "worker-sonnet": agent}

    inbox = InboxRouter(state_dir=tmp_path)
    mcp = AegisMCP()

    def make_session(profile, mcp_url, handle):
        return get_driver(profile.harness).session(
            profile, str(tmp_path), mcp_url, handle)

    mgr = SessionManager(agents, "default", make_session, mcp, inbox=inbox)
    qm = QueueManager(
        {"impl": Queue(name="impl", agent_profile="worker-sonnet",
                       max_parallel=1)},
        mgr, inbox, state_dir=tmp_path)
    mgr.attach_queue_manager(qm)
    mcp.bind(mgr)
    await mcp.start()
    await qm.start()
    try:
        out = await asyncio.wait_for(
            run_workflow(
                "tdd_step",
                {"plan_step": "trivial: assert one plus one equals two",
                 "test_command": "python -m pytest",
                 "test_path": str(tmp_path / "test_one_plus_one.py")},
                bridge=mgr, queue_manager=qm, inbox_router=inbox,
                state_dir=tmp_path),
            timeout=180)
        assert out["status"] == "ok", out
        assert "green" in (out["result"] or "")
        # Sanity: the test file exists and passes.
        assert (tmp_path / "test_one_plus_one.py").exists()
        proc = subprocess.run(
            ["python", "-m", "pytest", str(tmp_path / "test_one_plus_one.py")],
            capture_output=True, text=True)
        assert proc.returncode == 0
    finally:
        await qm.stop()
        await mgr.close_all()
        await mcp.stop()
```

- [ ] **Step 4: Update `AGENTS.md`**

Append after the `src/aegis/queue/` block:

```markdown
- `src/aegis/workflow/` - the workflow scaffold (v1). `@workflow`
  decorator + auto-registry (`decorator.py`); `WorkflowEngine` runtime
  with `delegate` (one-shot via queue), `send`/`drain` (live-agent
  fire-and-forget + await idle), `spawn`/`close` (long-lived agent
  lifecycle), `bash` (async shell), `log` (stderr + JSONL under
  `.aegis/state/workflows/`), and `caller_handle` (whoever invoked
  via MCP `aegis_run_workflow`); `runner.run_workflow` is the unified
  entry for CLI (`aegis workflow run`) and MCP (`aegis_run_workflow`),
  with auto-drain + auto-close in finally. Compose on the v1 queue
  for delegation; no second agent-spawn plane.
- `examples/` - shipped workflows (`tdd_step.py`). Import in your
  `.aegis.py` to register them.
```

- [ ] **Step 5: Run live (manual) + commit**

```
# Manual live run (requires claude on PATH; ~90s budget):
uv run pytest tests/test_workflow_live.py -v -m live; echo "rc=$?"

# Hermetic gate must still be green:
uv run pytest -q -m "not live"; echo "rc=$?"

git add examples/__init__.py examples/tdd_step.py \
  tests/test_workflow_live.py AGENTS.md
git commit -m "test(workflow): live TDD smoke + examples/tdd_step.py + AGENTS update

VS5 GREEN — workflow scaffold v1 shipped end-to-end."
git push
```

---

## Self-review

### Spec coverage

| Spec locked decision | Plan task(s) |
|---|---|
| LD §1 — substrate + one canonical workflow | VS5 (tdd_step example + live test) |
| LD §2 — `@workflow async def` Python | T1.1 |
| LD §3 — decorator-only auto-registry, collision = ConfigError | T1.1 |
| LD §4 — engine surface mirrors agent's surface | T1.2-3.4 (all engine methods) |
| LD §5 — workflows compose on the queue | T2.1 |
| LD §6 — `caller_handle` exposed | T1.2, T1.4, T4.1 |
| LD §7 — `send` (sync FAF) + `drain` (async await idle) | T3.3, T3.4 |
| LD §8 — CLI + MCP invocation surfaces | T1.5, T4.1 |
| LD §9 — no persistence in v1 | (implicit — never written; runner is in-memory) |
| LD §10 — auto-close on workflow exit | T3.5 |

Spec component map: `decorator.py` (T1.1), `engine.py` (T1.2-3.4),
`runner.py` (T1.4, T3.5), CLI subcommands (T1.5), MCP tool (T4.1),
AppBridge extension (T3.1). All present.

### Placeholder scan

No "TBD", "implement later", or "similar to Task N". The plan does
contain one acknowledged v1 quirk (T4.1: outer ack `workflow_run_id`
differs from the runner's internal one) — flagged with a follow-up
note rather than left as a TODO.

### Type / signature consistency

- `WorkflowEngine.__init__` kwargs match across T1.2 (definition) and
  T1.4 (runner construction): `bridge`, `queue_manager`, `inbox_router`,
  `caller_handle`, `state_dir`, `now`, `drain_timeout`.
- `bridge.spawn(profile, *, handle=None) -> str` (async) is the same
  signature in T3.1 (Protocol), T3.1 (SessionManager + AegisApp impl),
  and T3.2 (engine.spawn caller).
- `bridge.close(handle) -> None` (async) is consistent across same.
- `run_workflow(name, kwargs, *, bridge, queue_manager, inbox_router,
  caller_handle=None, state_dir=None) -> dict` consistent across T1.4
  and T4.1 (MCP tool caller).
- `_DelegationPromise.deliver(msg)` is `async def` — matches the
  inbox router's `bind_session(handle, session)` contract (session
  must have `async def deliver`).

---

## Execution handoff

After the implementation lands and `uv run pytest -q -m "not live"`
is green AND the live smoke passes (manual on a host with `claude` on
PATH), append one line to today's journal:

```
> 🤖 HH:MM — milestone: aegis workflow scaffold v1 shipped
```

Then update `repos/aegis/TASKS.md`: move §1 (workflow scaffold) to a
"Shipped 2026-05-20" section; promote remaining items (queue v1 polish,
sequential handoff, long-lived bash terminals) up. Push.
