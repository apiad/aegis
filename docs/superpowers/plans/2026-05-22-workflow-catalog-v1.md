# Workflow Catalog v1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each slice ends with `uv run pytest -q --ignore=tests/test_opencode_live_*.py` clean and a conventional commit on `main`. Per repo memory, aegis commits straight to main — no feature branch.

**Goal:** Extend the v1 `WorkflowEngine` with the primitives the catalog needs (unified `engine.send`, `ask_human`, explicit checkpoints with durable resume, subagent `spawn`/`close`, `parallel`, `bash_predicate`), then ship four seed workflows under `aegis.workflows`: `brainstorm_to_spec`, `execute_plan`, `review_branch`, `tdd_cycle`.

**Architecture:** New `WorkflowRunner` module owns running workflows as asyncio tasks. `aegis_run_workflow` becomes non-blocking (returns `{workflow_id, host, status}`); new MCP tools `aegis_workflow_status` / `aegis_workflow_cancel`. Workflows narrate into the host agent's transcript via the existing inbox plumbing; `ask_human` flips the host's tab input bar into "workflow question" mode. Subagents are backstage by default. Ledger at `.aegis/state/workflows/<id>/` enables `aegis --resume` to restart workflows from the last explicit checkpoint.

**Tech Stack:** Python 3.13+, asyncio, Textual 8.x, FastMCP, pytest. No new third-party deps.

**Spec:** `docs/superpowers/specs/2026-05-22-workflow-catalog-design.md` — read it before starting.

**Precondition checks (run first, hard-stop if any fail):**

```bash
cd /home/apiad/Workspace/repos/aegis && \
  git fetch --quiet origin && \
  git checkout --quiet main && \
  git pull --ff-only --quiet && \
  test -f docs/superpowers/specs/2026-05-22-workflow-catalog-design.md || { echo "spec missing"; exit 1; }
test -d src/aegis/workflow && \
  ls src/aegis/workflow/engine.py src/aegis/workflow/runner.py >/dev/null 2>&1 || \
  { echo "v1 workflow scaffold missing — wrong checkout"; exit 1; }
uv run pytest -q --ignore=tests/test_opencode_live_*.py 2>&1 | tail -5
```

Last line should show all-green. If not, stop and ping Alex before doing anything else.

---

## Slice 1 — Engine: unified `send`, `spawn`, `close`, `host` handle

**Files:**
- Modify: `src/aegis/workflow/engine.py` — `WorkflowEngine` gains `host`, `workflow_id`, `name`, `config` attributes; refactors `delegate` into unified `send(handle, prompt)`; adds `spawn` / `close`.
- Modify: `src/aegis/workflow/runner.py` — `_RunningWorkflow` carries host handle and config; runner returns `workflow_id`.
- Create: `tests/test_workflow_engine_send.py`
- Create: `tests/test_workflow_engine_spawn.py`

The v1 scaffold has a `delegate` / `send` / `drain` / `spawn` / `close` set on the engine. We're tightening these: one `send(handle, prompt) → reply_text`, one `spawn(profile, *, alias) → Handle`, one `close(handle)`, and `host` always available as a handle attribute.

- [ ] **Step 1: Read current engine + runner.** Open `src/aegis/workflow/engine.py` and `src/aegis/workflow/runner.py`; understand the current method shapes. Match the existing conventions for handle representation (string slug like `"lucid-knuth"`).

- [ ] **Step 2: Write failing tests.**

```python
# tests/test_workflow_engine_send.py
import asyncio
import pytest
from aegis.workflow.engine import WorkflowEngine


@pytest.mark.asyncio
async def test_engine_has_host_handle(monkeypatch, fake_bridge):
    eng = WorkflowEngine(
        bridge=fake_bridge, workflow_id="wf_1", name="t",
        host="lucid-knuth", config={},
    )
    assert eng.host == "lucid-knuth"
    assert eng.workflow_id == "wf_1"
    assert eng.name == "t"


@pytest.mark.asyncio
async def test_send_to_host_returns_reply(fake_bridge_with_canned_reply):
    fake_bridge_with_canned_reply.set_reply("lucid-knuth", "ack: hi")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_canned_reply, workflow_id="w",
        name="t", host="lucid-knuth", config={},
    )
    reply = await eng.send(eng.host, "hi")
    assert reply == "ack: hi"


@pytest.mark.asyncio
async def test_send_to_subagent_returns_reply(fake_bridge_with_canned_reply):
    fake_bridge_with_canned_reply.set_reply("brisk-curie", "subagent reply")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_canned_reply, workflow_id="w",
        name="t", host="lucid-knuth", config={},
    )
    reply = await eng.send("brisk-curie", "do it")
    assert reply == "subagent reply"
```

```python
# tests/test_workflow_engine_spawn.py
import pytest
from aegis.workflow.engine import WorkflowEngine, SubagentSpawnError


@pytest.mark.asyncio
async def test_spawn_returns_handle(fake_bridge_with_spawner):
    eng = WorkflowEngine(
        bridge=fake_bridge_with_spawner, workflow_id="w", name="t",
        host="lucid-knuth", config={},
    )
    handle = await eng.spawn("implementer")
    assert isinstance(handle, str)
    assert handle in fake_bridge_with_spawner.live_handles


@pytest.mark.asyncio
async def test_close_subagent_succeeds(fake_bridge_with_spawner):
    eng = WorkflowEngine(
        bridge=fake_bridge_with_spawner, workflow_id="w", name="t",
        host="lucid-knuth", config={},
    )
    handle = await eng.spawn("implementer")
    await eng.close(handle)
    assert handle not in fake_bridge_with_spawner.live_handles


@pytest.mark.asyncio
async def test_close_host_raises(fake_bridge_with_spawner):
    eng = WorkflowEngine(
        bridge=fake_bridge_with_spawner, workflow_id="w", name="t",
        host="lucid-knuth", config={},
    )
    with pytest.raises(ValueError):
        await eng.close(eng.host)
```

You'll need to define test fixtures (`fake_bridge`, `fake_bridge_with_canned_reply`, `fake_bridge_with_spawner`) in a new `tests/conftest_workflows.py` or inline. Keep them minimal — just enough surface to exercise the engine.

- [ ] **Step 3: Run tests — expect fail.**

```bash
uv run pytest tests/test_workflow_engine_send.py tests/test_workflow_engine_spawn.py -q
```

- [ ] **Step 4: Implement.** Update `WorkflowEngine`:
  - Add `host`, `workflow_id`, `name`, `config` to `__init__`.
  - `send(handle, prompt, *, timeout=None) → str`: puts prompt into `bridge.inbox_router.deliver(handle, ...)` and awaits the next assistant message from that handle (new bookkeeping — see Slice 2 for the awaiter mechanism; for now, route through whatever exists in the v1 scaffold's `delegate` and unify).
  - `spawn(profile, *, alias=None) → handle`: calls `bridge.session_manager.spawn_subagent(profile, alias)` (add this method to `SessionManager` if missing — it should mirror normal spawn but flag the session as `subagent=True` so the TUI suppresses the tab by default).
  - `close(handle)`: calls `bridge.session_manager.close_session(handle)`; raises `ValueError` if `handle == self.host`.

- [ ] **Step 5: Tests pass.**

```bash
uv run pytest tests/test_workflow_engine_send.py tests/test_workflow_engine_spawn.py -q
```

- [ ] **Step 6: Commit.**

```bash
git add src/aegis/workflow/ tests/test_workflow_engine_send.py tests/test_workflow_engine_spawn.py tests/conftest_workflows.py
git commit -m "feat(workflow): unified engine.send + spawn/close + host handle"
```

---

## Slice 2 — Engine: `ask_human` + non-blocking MCP trigger

**Files:**
- Modify: `src/aegis/workflow/engine.py` — add `ask_human`.
- Modify: `src/aegis/workflow/runner.py` — `WorkflowRunner` becomes an asyncio-owned scheduler; tracks per-workflow asyncio tasks; exposes `start(name, kwargs, host) → workflow_id` and `status(workflow_id)` / `cancel(workflow_id)`.
- Modify: `src/aegis/mcp/server.py` — `aegis_run_workflow` returns `{workflow_id, host, status: "running"}` immediately; add `aegis_workflow_status` and `aegis_workflow_cancel`.
- Modify: `src/aegis/tui/agent_tab.py` (or wherever the input widget lives) — input bar gains "workflow question" mode triggered by a pending `ask_human` on this host.
- Create: `tests/test_workflow_engine_ask_human.py`
- Create: `tests/test_workflow_mcp.py`

- [ ] **Step 1: Write failing tests.**

```python
# tests/test_workflow_engine_ask_human.py
import asyncio
import pytest
from aegis.workflow.engine import WorkflowEngine


@pytest.mark.asyncio
async def test_ask_human_returns_user_reply(fake_bridge_with_human_queue):
    fake_bridge_with_human_queue.enqueue_reply("lucid-knuth", "the user said this")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_human_queue, workflow_id="w", name="t",
        host="lucid-knuth", config={},
    )
    reply = await eng.ask_human("what color?")
    assert reply == "the user said this"


@pytest.mark.asyncio
async def test_ask_human_with_options(fake_bridge_with_human_queue):
    fake_bridge_with_human_queue.enqueue_reply("lucid-knuth", "red")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_human_queue, workflow_id="w", name="t",
        host="lucid-knuth", config={},
    )
    reply = await eng.ask_human("which?", options=["red", "blue", "green"])
    assert reply == "red"
    # The bridge should have been told the options:
    assert fake_bridge_with_human_queue.last_options("lucid-knuth") == \
        ["red", "blue", "green"]


@pytest.mark.asyncio
async def test_ask_human_fifo_when_two_questions_queued(fake_bridge_with_human_queue):
    fake_bridge_with_human_queue.enqueue_reply("lucid-knuth", "first")
    fake_bridge_with_human_queue.enqueue_reply("lucid-knuth", "second")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_human_queue, workflow_id="w", name="t",
        host="lucid-knuth", config={},
    )
    assert await eng.ask_human("q1") == "first"
    assert await eng.ask_human("q2") == "second"
```

```python
# tests/test_workflow_mcp.py
import asyncio
import pytest
from aegis.mcp.server import build_server


@pytest.mark.asyncio
async def test_run_workflow_returns_immediately(fake_bridge_with_runner):
    server = build_server(fake_bridge_with_runner)
    res = await server.call_tool("aegis_run_workflow", {
        "name": "echo", "kwargs": {"text": "hi"},
        "from_handle": "lucid-knuth",
    })
    data = res.structured_content or res.data
    assert "workflow_id" in data
    assert data["host"] == "lucid-knuth"
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_workflow_status(fake_bridge_with_runner):
    server = build_server(fake_bridge_with_runner)
    r1 = await server.call_tool("aegis_run_workflow", {
        "name": "echo", "kwargs": {}, "from_handle": "h",
    })
    wid = (r1.structured_content or r1.data)["workflow_id"]
    r2 = await server.call_tool("aegis_workflow_status", {"workflow_id": wid})
    data = r2.structured_content or r2.data
    assert data["workflow_id"] == wid
    assert "status" in data


@pytest.mark.asyncio
async def test_workflow_cancel(fake_bridge_with_runner):
    server = build_server(fake_bridge_with_runner)
    r1 = await server.call_tool("aegis_run_workflow", {
        "name": "long_sleeper", "kwargs": {}, "from_handle": "h",
    })
    wid = (r1.structured_content or r1.data)["workflow_id"]
    r2 = await server.call_tool("aegis_workflow_cancel", {"workflow_id": wid})
    data = r2.structured_content or r2.data
    assert data["ok"] is True
```

- [ ] **Step 2: Implement `ask_human` in `WorkflowEngine`.** Pseudocode:

```python
async def ask_human(self, question, *, options=None, timeout=None):
    fut = asyncio.get_running_loop().create_future()
    await self._bridge.workflow_runner.register_human_question(
        host=self.host, workflow_id=self.workflow_id,
        question=question, options=options, fut=fut,
    )
    if timeout is None:
        return await fut
    return await asyncio.wait_for(fut, timeout=timeout)
```

`WorkflowRunner.register_human_question` (new method) records the
pending question and notifies the TUI (or Telegram in headless) to
surface it. When the host's input bar receives a reply (TUI) or a
matching Telegram message arrives, the runner resolves the future.

- [ ] **Step 3: Make `WorkflowRunner` async-task owned.** `start(name, kwargs, host) → workflow_id`:

```python
async def start(self, name, kwargs, host):
    wid = f"wf_{ulid_or_token_hex(8)}"
    workflow_fn = REGISTRY[name]
    eng = WorkflowEngine(
        bridge=self._bridge, workflow_id=wid, name=name,
        host=host, config=workflow_fn._config,
    )
    self._init_ledger(wid, name, host, kwargs)
    task = asyncio.create_task(self._run(eng, workflow_fn, kwargs))
    self._running[wid] = _RunningWorkflow(
        id=wid, name=name, host=host, task=task, eng=eng,
    )
    return wid

async def _run(self, eng, fn, kwargs):
    try:
        result = await fn(eng, **kwargs)
        self._finalize(eng.workflow_id, result=result, errored=False)
    except asyncio.CancelledError:
        self._finalize(eng.workflow_id, errored="cancelled_by_user")
        raise
    except Exception as e:
        self._finalize(eng.workflow_id, errored=str(e))
```

- [ ] **Step 4: Update `aegis_run_workflow` MCP tool.** It now calls `await bridge.workflow_runner.start(name, kwargs, host=from_handle)` and returns `{workflow_id, host, status}`. Add `aegis_workflow_status(workflow_id)` and `aegis_workflow_cancel(workflow_id)` as new tools.

- [ ] **Step 5: TUI input bar "workflow question" mode.** Find the input widget for an agent tab. Add a property `pending_workflow_question` (set by `WorkflowRunner` when a question is registered for this host). When set, the input bar renders `? <question>  ↵ to send` (instead of the default `›`). Next `Enter` calls the question's future with the input text, then clears `pending_workflow_question`.

- [ ] **Step 6: Tests pass; commit.**

```bash
uv run pytest tests/test_workflow_engine_ask_human.py tests/test_workflow_mcp.py -q
git add -A
git commit -m "feat(workflow): non-blocking MCP trigger + ask_human + status/cancel"
```

---

## Slice 3 — Engine: explicit checkpoints + durable resume

**Files:**
- Modify: `src/aegis/workflow/engine.py` — add `checkpoint`, `resume_state`.
- Modify: `src/aegis/workflow/runner.py` — write ledger on each checkpoint/spawn/close/finished/errored; resume from ledger on `aegis --resume`.
- Modify: `src/aegis/core/manager.py` — `SessionManager` snapshot now includes running workflows (id, name, host, kwargs); restore on `--resume` calls `WorkflowRunner.resume(...)`.
- Create: `tests/test_workflow_checkpoints.py`
- Create: `tests/test_workflow_resume.py`

- [ ] **Step 1: Write failing tests.**

```python
# tests/test_workflow_checkpoints.py
import json
import pytest
from pathlib import Path
from aegis.workflow.engine import WorkflowEngine


@pytest.mark.asyncio
async def test_checkpoint_appends_ledger(fake_bridge_with_state, tmp_path):
    fake_bridge_with_state.set_state_dir(tmp_path / "wf")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_state, workflow_id="w1", name="t",
        host="h", config={},
    )
    await eng.checkpoint("phase_one", {"x": 1})
    ledger = (tmp_path / "wf" / "w1" / "ledger.jsonl").read_text()
    recs = [json.loads(l) for l in ledger.splitlines() if l.strip()]
    assert any(r["kind"] == "checkpoint" and r["name"] == "phase_one"
               and r["payload"] == {"x": 1} for r in recs)


@pytest.mark.asyncio
async def test_resume_state_returns_last_checkpoint_payload(fake_bridge_with_state, tmp_path):
    fake_bridge_with_state.set_state_dir(tmp_path / "wf")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_state, workflow_id="w2", name="t",
        host="h", config={},
    )
    await eng.checkpoint("one", {"x": 1})
    await eng.checkpoint("two", {"x": 2})
    # New engine for same workflow_id (simulating resume)
    eng2 = WorkflowEngine(
        bridge=fake_bridge_with_state, workflow_id="w2", name="t",
        host="h", config={},
    )
    state = await eng2.resume_state()
    assert state == {"x": 2}


@pytest.mark.asyncio
async def test_resume_state_returns_none_for_fresh_workflow(fake_bridge_with_state, tmp_path):
    fake_bridge_with_state.set_state_dir(tmp_path / "wf")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_state, workflow_id="fresh", name="t",
        host="h", config={},
    )
    assert await eng.resume_state() is None


@pytest.mark.asyncio
async def test_non_jsonable_checkpoint_raises_immediately(fake_bridge_with_state, tmp_path):
    fake_bridge_with_state.set_state_dir(tmp_path / "wf")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_state, workflow_id="w3", name="t",
        host="h", config={},
    )
    with pytest.raises(TypeError):
        await eng.checkpoint("bad", {"sock": object()})
```

```python
# tests/test_workflow_resume.py
import asyncio
import pytest
from aegis.workflow import workflow
from aegis.workflow.runner import WorkflowRunner


@pytest.mark.asyncio
async def test_resume_from_last_checkpoint(fake_bridge_with_runner, tmp_path):
    """Workflow that checkpoints once, then 'crashes'. On resume, it should
    pick up after the checkpoint."""
    fake_bridge_with_runner.set_state_dir(tmp_path / "wf")

    progress = []

    @workflow("crashy")
    async def crashy(engine, *, fail_after_checkpoint: bool):
        state = await engine.resume_state() or {"phase": "init"}
        if state["phase"] == "init":
            progress.append("before_checkpoint")
            await engine.checkpoint("done_init", {"phase": "next", "data": 42})
            state = {"phase": "next", "data": 42}
            if fail_after_checkpoint:
                raise RuntimeError("simulated crash")
        if state["phase"] == "next":
            progress.append(f"after_resume_with_{state['data']}")
            return "ok"

    runner = fake_bridge_with_runner.workflow_runner
    wid = await runner.start("crashy", {"fail_after_checkpoint": True}, host="h")
    # Wait for failure
    await asyncio.sleep(0.1)
    assert "before_checkpoint" in progress
    assert "after_resume_with_42" not in progress

    # Simulate resume: re-start with same workflow_id
    await runner.resume(wid)
    await asyncio.sleep(0.1)
    assert "after_resume_with_42" in progress
```

- [ ] **Step 2: Implement.**

In engine:

```python
async def checkpoint(self, name: str, payload: dict) -> None:
    json.dumps(payload)  # raises TypeError early if non-serializable
    await self._bridge.workflow_runner.append_ledger(
        self.workflow_id,
        {"kind": "checkpoint", "at": _now_iso(),
         "name": name, "payload": payload},
    )

async def resume_state(self) -> dict | None:
    records = self._bridge.workflow_runner.read_ledger(self.workflow_id)
    for rec in reversed(records):
        if rec["kind"] == "checkpoint":
            return rec["payload"]
    return None
```

In runner:

```python
def append_ledger(self, wid, record):
    path = self._ledger_path(wid)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")

def read_ledger(self, wid):
    path = self._ledger_path(wid)
    if not path.exists(): return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]

async def resume(self, wid):
    """Restart a workflow whose ledger exists but which is not finished."""
    meta = self._read_meta(wid)
    if not meta: raise WorkflowNotFound(wid)
    records = self.read_ledger(wid)
    if any(r["kind"] in {"finished", "errored"} for r in records):
        return  # terminal; nothing to do
    workflow_fn = REGISTRY[meta["name"]]
    eng = WorkflowEngine(
        bridge=self._bridge, workflow_id=wid, name=meta["name"],
        host=meta["host"], config=workflow_fn._config,
    )
    task = asyncio.create_task(self._run(eng, workflow_fn, meta["kwargs"]))
    self._running[wid] = _RunningWorkflow(...)
```

In `SessionManager.save_workspace`: snapshot `running_workflows = [{wid, name, host, kwargs} for w in workflow_runner.running()]`. In `restore_workspace`: for each, call `workflow_runner.resume(wid)`.

- [ ] **Step 3: Tests pass; commit.**

```bash
uv run pytest tests/test_workflow_checkpoints.py tests/test_workflow_resume.py -q
git add -A
git commit -m "feat(workflow): explicit checkpoints + durable resume via ledger"
```

---

## Slice 4 — Engine: `bash`, `bash_predicate`, `parallel`, `log`, `config`

**Files:**
- Modify: `src/aegis/workflow/engine.py` — add `bash`, `bash_predicate`, `parallel`, `log`, `config`.
- Create: `tests/test_workflow_bash.py`

The `bash` method is mostly already in the v1 scaffold; we're tightening its return shape and adding `bash_predicate` and `parallel` on top.

- [ ] **Step 1: Write failing tests.**

```python
# tests/test_workflow_bash.py
import pytest
from aegis.workflow.engine import WorkflowEngine, PredicateFailed


@pytest.mark.asyncio
async def test_bash_returns_structured_result(fake_bridge):
    eng = WorkflowEngine(bridge=fake_bridge, workflow_id="w", name="t",
                        host="h", config={})
    res = await eng.bash("echo hello")
    assert res["exit"] == 0
    assert "hello" in res["stdout"]


@pytest.mark.asyncio
async def test_bash_predicate_succeeds_first_try(fake_bridge):
    eng = WorkflowEngine(bridge=fake_bridge, workflow_id="w", name="t",
                        host="h", config={})
    res = await eng.bash_predicate("true", retry_with="never used")
    assert res["exit"] == 0


@pytest.mark.asyncio
async def test_bash_predicate_retries_with_feedback(fake_bridge_with_canned_reply):
    fake_bridge_with_canned_reply.set_reply("h", "ok I'll try")
    eng = WorkflowEngine(bridge=fake_bridge_with_canned_reply,
                        workflow_id="w", name="t",
                        host="h", config={})
    # The fake bridge bash should fail twice then succeed.
    fake_bridge_with_canned_reply.set_bash_sequence([
        {"exit": 1, "stdout": "fail1", "stderr": ""},
        {"exit": 1, "stdout": "fail2", "stderr": ""},
        {"exit": 0, "stdout": "ok", "stderr": ""},
    ])
    res = await eng.bash_predicate("pytest", retry_with="fix it", max_retries=3)
    assert res["exit"] == 0
    # The host should have received 2 send() calls with retry feedback:
    assert fake_bridge_with_canned_reply.sends_to("h") == [
        "fix it",  # after first failure
        "fix it",  # after second
    ]


@pytest.mark.asyncio
async def test_bash_predicate_raises_when_max_retries_exhausted(fake_bridge_with_canned_reply):
    fake_bridge_with_canned_reply.set_reply("h", "trying")
    eng = WorkflowEngine(bridge=fake_bridge_with_canned_reply,
                        workflow_id="w", name="t",
                        host="h", config={})
    fake_bridge_with_canned_reply.set_bash_sequence([
        {"exit": 1, "stdout": "", "stderr": ""},
        {"exit": 1, "stdout": "", "stderr": ""},
    ])
    with pytest.raises(PredicateFailed):
        await eng.bash_predicate("pytest", retry_with="x", max_retries=1)


@pytest.mark.asyncio
async def test_parallel_runs_branches(fake_bridge):
    eng = WorkflowEngine(bridge=fake_bridge, workflow_id="w", name="t",
                        host="h", config={})

    async def one(): return "a"
    async def two(): return "b"

    results = await eng.parallel([one(), two()])
    assert set(results) == {"a", "b"}
```

- [ ] **Step 2: Implement.**

```python
async def bash(self, cmd, *, cwd=None, timeout=None):
    return await self._bridge.workflow_runner.run_bash(cmd, cwd=cwd, timeout=timeout)

async def bash_predicate(self, cmd, *, retry_with, max_retries=3):
    for attempt in range(max_retries + 1):
        result = await self.bash(cmd)
        if result["exit"] == 0:
            return result
        if attempt == max_retries:
            raise PredicateFailed(cmd, result, attempts=attempt + 1)
        # Format retry_with feedback
        if callable(retry_with):
            feedback = retry_with(result)
        else:
            feedback = retry_with.format(
                stdout=result["stdout"], stderr=result["stderr"],
                exit=result["exit"])
        await self.send(self.host, feedback)

async def parallel(self, coros):
    return await asyncio.gather(*coros)

async def log(self, msg):
    await self._bridge.workflow_runner.narrate(
        self.workflow_id, self.host,
        f"▶ {self.name}: {msg}",
    )
```

- [ ] **Step 3: Tests pass; commit.**

```bash
uv run pytest tests/test_workflow_bash.py -q
git add -A
git commit -m "feat(workflow): bash_predicate retry loop + parallel + log + config"
```

---

## Slice 5 — Catalog package scaffolding + `aegis.workflows.__init__`

**Files:**
- Create: `src/aegis/workflows/__init__.py`
- Create: `src/aegis/workflows/_lib/__init__.py`
- Create: `src/aegis/workflows/_lib/plan_parser.py`
- Create: `src/aegis/workflows/_lib/spec_renderer.py`
- Create: `src/aegis/workflows/_lib/git_helpers.py`
- Create: `src/aegis/workflows/_lib/options.py`
- Create: `tests/test_workflow_catalog_scaffolding.py`

- [ ] **Step 1: Write `__init__.py` placeholder** (will re-export seeds as they're added in slices 6–9):

```python
"""Aegis workflow catalog.

Importing a workflow from this package registers it with the workflow
runtime, after which it's invocable via aegis_run_workflow.

>>> from aegis.workflows import brainstorm_to_spec
>>> # Now `aegis_run_workflow(name="brainstorm_to_spec", ...)` works.
"""
from aegis.workflows.brainstorm_to_spec import brainstorm_to_spec
from aegis.workflows.execute_plan import execute_plan
from aegis.workflows.review_branch import review_branch
from aegis.workflows.tdd_cycle import tdd_cycle

__all__ = [
    "brainstorm_to_spec",
    "execute_plan",
    "review_branch",
    "tdd_cycle",
]
```

This will fail import until each seed file exists. We'll create stubs for the seeds first, then flesh them out in subsequent slices.

- [ ] **Step 2: Stub the four seed files** with minimal `@workflow(...)` decorated functions that just `return "stub"`:

```python
# src/aegis/workflows/brainstorm_to_spec.py
from aegis.workflow import workflow

@workflow("brainstorm_to_spec")
async def brainstorm_to_spec(engine, *, topic=None):
    return "stub"
```

(Repeat for the other three.)

- [ ] **Step 3: Implement `_lib/plan_parser.py`.**

```python
"""Parse a markdown plan into a task list. Tasks are top-level numbered
slices or `### Task N:` headers in our convention."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass
class Task:
    id: str
    title: str
    body: str

@dataclass
class Plan:
    title: str
    tasks: list[Task]


_TASK_RE = re.compile(r"^##\s+Slice\s+(\d+)\s+[—-]\s+(.+)$", re.MULTILINE)


def parse_plan(path: str | Path) -> Plan:
    text = Path(path).read_text()
    title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else str(path)
    matches = list(_TASK_RE.finditer(text))
    tasks = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        tasks.append(Task(id=f"slice-{m.group(1)}", title=m.group(2).strip(), body=body))
    return Plan(title=title, tasks=tasks)
```

- [ ] **Step 4: Implement `_lib/spec_renderer.py`.**

```python
"""Render a dialogue (Q/A) into a spec doc."""
from datetime import date


def render_spec_prompt(topic: str, answers: dict[str, str]) -> str:
    pairs = "\n".join(f"### Q: {q}\nA: {a}" for q, a in answers.items())
    return (
        f"You are drafting a design spec. Topic: {topic}\n\n"
        f"Below are the answers the user gave to clarifying questions. "
        f"Synthesize them into a complete design spec following the conventions "
        f"in docs/superpowers/specs/. Output the spec body in markdown.\n\n"
        f"{pairs}"
    )


def slugify(text: str) -> str:
    return "-".join(text.lower().split())[:60]


def today_iso() -> str:
    return date.today().isoformat()
```

- [ ] **Step 5: Implement `_lib/git_helpers.py`.**

```python
"""Git helpers for review_branch and friends."""
import subprocess


def diff_vs(base: str = "main") -> str:
    return subprocess.run(
        ["git", "diff", f"{base}...HEAD"],
        capture_output=True, text=True,
    ).stdout


def branch_slug() -> str:
    out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    return out.replace("/", "-")
```

- [ ] **Step 6: Implement `_lib/options.py`** (simple option formatter for `ask_human`):

```python
def format_options(options: list[str]) -> str:
    return "\n".join(f"  {i+1}. {opt}" for i, opt in enumerate(options))
```

- [ ] **Step 7: Write scaffolding tests.**

```python
# tests/test_workflow_catalog_scaffolding.py
def test_imports_register_seeds():
    from aegis.workflows import (
        brainstorm_to_spec, execute_plan, review_branch, tdd_cycle,
    )
    from aegis.workflow import REGISTRY
    assert "brainstorm_to_spec" in REGISTRY
    assert "execute_plan" in REGISTRY
    assert "review_branch" in REGISTRY
    assert "tdd_cycle" in REGISTRY


def test_plan_parser_extracts_slices(tmp_path):
    from aegis.workflows._lib.plan_parser import parse_plan
    p = tmp_path / "plan.md"
    p.write_text("# Plan\n\n## Slice 1 — first\nbody1\n\n## Slice 2 — second\nbody2\n")
    plan = parse_plan(p)
    assert plan.title == "Plan"
    assert len(plan.tasks) == 2
    assert plan.tasks[0].id == "slice-1"
    assert plan.tasks[0].title == "first"
```

- [ ] **Step 8: Tests pass; commit.**

```bash
uv run pytest tests/test_workflow_catalog_scaffolding.py -q
git add -A
git commit -m "feat(workflows): catalog package scaffolding + _lib helpers + stub seeds"
```

---

## Slice 6 — Seed: `brainstorm_to_spec`

**Files:**
- Modify: `src/aegis/workflows/brainstorm_to_spec.py` — full implementation per spec sketch.
- Create: `tests/test_workflow_brainstorm_to_spec.py`

- [ ] **Step 1: Write failing test.**

```python
# tests/test_workflow_brainstorm_to_spec.py
import asyncio
import pytest
from aegis.workflows.brainstorm_to_spec import brainstorm_to_spec


@pytest.mark.asyncio
async def test_brainstorm_to_spec_happy_path(workflow_test_harness, tmp_path):
    """Walk through the questions, get a spec doc out."""
    harness = workflow_test_harness(
        host="h",
        human_replies=[
            # 5 question answers
            "answer 1", "answer 2", "answer 3", "answer 4", "answer 5",
        ],
        subagent_replies={"spec_writer": "# Spec\n\nDrafted content."},
        cwd=tmp_path,
    )
    result = await brainstorm_to_spec(harness.engine, topic="testing")
    assert result.endswith(".md")
    written = (tmp_path / result).read_text()
    assert "# Spec" in written
    # Verify subagent was spawned and closed:
    assert "spec_writer" in harness.spawned_profiles
    assert harness.spawned_handles == harness.closed_handles
```

- [ ] **Step 2: Implement.** Full body per spec sketch (Section "Seed catalog #1" in the design doc). Use the `_lib` helpers (`render_spec_prompt`, `slugify`, `today_iso`).

- [ ] **Step 3: Tests pass; commit.**

```bash
uv run pytest tests/test_workflow_brainstorm_to_spec.py -q
git add -A
git commit -m "feat(workflows): brainstorm_to_spec — interactive dialogue → spec doc"
```

---

## Slice 7 — Seed: `execute_plan`

**Files:**
- Modify: `src/aegis/workflows/execute_plan.py` — full implementation.
- Create: `tests/test_workflow_execute_plan.py`

- [ ] **Step 1: Write failing test.** The test should cover: parse a plan, dispatch one subagent per task, checkpoint after each, skip done tasks on resume.

```python
# tests/test_workflow_execute_plan.py
import pytest
from aegis.workflows.execute_plan import execute_plan


@pytest.mark.asyncio
async def test_execute_plan_dispatches_subagent_per_task(workflow_test_harness, tmp_path):
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("""
# Plan

## Slice 1 — first
body1

## Slice 2 — second
body2
""")
    harness = workflow_test_harness(
        host="h",
        subagent_replies={"implementer": "done"},
        cwd=tmp_path,
    )
    result = await execute_plan(harness.engine, plan_path=str(plan_path))
    assert "2/2" in result
    # 2 spawns + 2 closes:
    assert harness.spawned_profiles.count("implementer") == 2
    assert harness.closed_handles == harness.spawned_handles


@pytest.mark.asyncio
async def test_execute_plan_skips_done_tasks_on_resume(workflow_test_harness, tmp_path):
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# Plan\n\n## Slice 1 — a\n\n## Slice 2 — b\n")
    # Simulate a prior run that finished slice-1 only:
    harness = workflow_test_harness(
        host="h", workflow_id="resumed",
        initial_state={"phase": "tasks",
                       "plan_path": str(plan_path),
                       "tasks": [{"id": "slice-1", "title": "a", "body": ""},
                                 {"id": "slice-2", "title": "b", "body": ""}],
                       "done": ["slice-1"]},
        subagent_replies={"implementer": "ok"},
        cwd=tmp_path,
    )
    result = await execute_plan(harness.engine, plan_path=str(plan_path))
    # Should only have dispatched slice-2:
    assert harness.spawned_profiles.count("implementer") == 1
```

- [ ] **Step 2: Implement.** Full body per spec sketch.

- [ ] **Step 3: Tests pass; commit.**

```bash
uv run pytest tests/test_workflow_execute_plan.py -q
git add -A
git commit -m "feat(workflows): execute_plan — durable plan-execution loop with subagents"
```

---

## Slice 8 — Seed: `review_branch`

**Files:**
- Modify: `src/aegis/workflows/review_branch.py` — full implementation.
- Create: `tests/test_workflow_review_branch.py`

- [ ] **Step 1: Write failing test.**

```python
# tests/test_workflow_review_branch.py
import pytest
from aegis.workflows.review_branch import review_branch


@pytest.mark.asyncio
async def test_review_branch_runs_reviewers_in_parallel(workflow_test_harness, tmp_path, monkeypatch):
    monkeypatch.setattr("aegis.workflows.review_branch.diff_vs",
                        lambda base: "diff --git a/x b/x\n+ change\n")
    harness = workflow_test_harness(
        host="h",
        subagent_replies={
            "security-reviewer": "security: lgtm",
            "api-reviewer": "api: lgtm",
            "test-reviewer": "tests: lgtm",
        },
        cwd=tmp_path,
        config={"reviewers": ["security-reviewer", "api-reviewer", "test-reviewer"]},
    )
    result = await review_branch(harness.engine)
    # Result is a path; the file should exist.
    written = (tmp_path / result).read_text()
    assert "security-reviewer" in written
    assert "api-reviewer" in written
    assert "test-reviewer" in written
    # All three reviewers were spawned:
    assert set(harness.spawned_profiles) >= {
        "security-reviewer", "api-reviewer", "test-reviewer",
    }


@pytest.mark.asyncio
async def test_review_branch_skips_empty_diff(workflow_test_harness, monkeypatch):
    monkeypatch.setattr("aegis.workflows.review_branch.diff_vs", lambda base: "")
    harness = workflow_test_harness(host="h")
    result = await review_branch(harness.engine)
    assert result == "no diff vs base"
    assert harness.spawned_profiles == []
```

- [ ] **Step 2: Implement.** Full body per spec sketch. Add a `render_review_report(results)` helper that produces a structured markdown doc.

- [ ] **Step 3: Tests pass; commit.**

```bash
uv run pytest tests/test_workflow_review_branch.py -q
git add -A
git commit -m "feat(workflows): review_branch — parallel multi-reviewer fan-out"
```

---

## Slice 9 — Seed: `tdd_cycle`

**Files:**
- Modify: `src/aegis/workflows/tdd_cycle.py` — full implementation.
- Create: `tests/test_workflow_tdd_cycle.py`

- [ ] **Step 1: Write failing test.**

```python
# tests/test_workflow_tdd_cycle.py
import pytest
from aegis.workflows.tdd_cycle import tdd_cycle


@pytest.mark.asyncio
async def test_tdd_cycle_writes_test_implements_reviews(workflow_test_harness):
    harness = workflow_test_harness(
        host="h",
        subagent_replies={
            "implementer": "ok",
            "reviewer": "lgtm",
        },
        # Sequence of bash results for the two bash_predicates:
        bash_sequence=[
            # First predicate: test should fail. We need a FAIL/ERROR line.
            {"exit": 0, "stdout": "FAIL test_x", "stderr": ""},  # grep "FAIL" → exit 0
            # Second predicate: tests should pass.
            {"exit": 0, "stdout": "1 passed", "stderr": ""},
        ],
    )
    result = await tdd_cycle(harness.engine, feature="rate_limit",
                             test_path="tests/test_rate_limit.py")
    assert "complete" in result
    # implementer spawned at least once for write_test + once for implement
    assert harness.spawned_profiles.count("implementer") >= 2
    assert harness.spawned_profiles.count("reviewer") == 1
```

- [ ] **Step 2: Implement.** Full body per spec sketch.

- [ ] **Step 3: Tests pass; commit.**

```bash
uv run pytest tests/test_workflow_tdd_cycle.py -q
git add -A
git commit -m "feat(workflows): tdd_cycle — predicate-retry loop for TDD"
```

---

## Slice 10 — CLI launch + docs + changelog

**Files:**
- Modify: `src/aegis/cli.py` — `aegis workflow run/list/status/cancel` CLI; `--on`, `--keep-host`, `--show-subagents`, `--headless`, `-- <kw=v>` kwarg parser.
- Create: `docs/workflows.md` — concept page (already in nav; just write the body now).
- Modify: `docs/index.md` — landing-page primitives grid: ensure "Workflow" card mentions catalog + ask_human + checkpoints.
- Modify: `README.md` — update workflow primitive section with catalog snippets.
- Modify: `mkdocs.yml` — already has `Workflows: workflows.md`. Add `- Catalog: workflows-catalog.md` if a separate page is needed (or fold the catalog into `workflows.md`).
- Modify: `CHANGELOG.md` — add `Added: aegis.workflows catalog with 4 seed workflows` under `[Unreleased]`.

- [ ] **Step 1: Implement `aegis workflow run`** with kwarg parsing.

```bash
aegis workflow run brainstorm_to_spec -- topic="testing the catalog"
aegis workflow run execute_plan --on lucid-knuth -- plan_path=docs/.../plan.md
```

`--on <handle>` attaches to an existing live session. Without it, spawn the default agent, run the workflow, auto-close the host unless `--keep-host`.

- [ ] **Step 2: Implement `aegis workflow list/status/cancel`.** These thinly wrap the MCP tools (or call the runner directly in TUI mode).

- [ ] **Step 3: Write `docs/workflows.md`.** Cover: what a workflow is, how it relates to agents, the engine API surface (table), the four seeds (one paragraph each with a code snippet), durability model, configuration in `.aegis.py`.

- [ ] **Step 4: Update `CHANGELOG.md`, `README.md`, `docs/index.md`.**

- [ ] **Step 5: Hermetic suite passes.**

```bash
uv run pytest -q --ignore=tests/test_opencode_live_*.py
```

- [ ] **Step 6: Final commit + push.**

```bash
git add -A
git commit -m "feat(workflows): aegis workflow CLI + catalog docs + changelog"
git push origin main
```

---

## Done definition

- All 10 slices committed on `main`.
- `uv run pytest -q --ignore=tests/test_opencode_live_*.py` is fully green.
- The four catalog workflows are registered on import and discoverable via `aegis_run_workflow`.
- `ask_human` works in TUI mode (assumed — visual verification deferred to zion).
- `aegis --resume` restarts running workflows from the last explicit checkpoint.
- Documentation reflects the catalog across spec, `docs/workflows.md`, README, landing page, and changelog.

Ping Alex on completion with: `✅ workflow catalog v1 landed on main`. If any slice fails repeatedly (>2 attempts on the same red test), stop and ping with the failing test name + last error.
