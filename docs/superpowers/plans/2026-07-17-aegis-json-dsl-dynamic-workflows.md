# Aegis JSON DSL — Dynamic Workflows (Track 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a validated JSON DSL that lets an agent author, validate, and launch a fan-out/pipeline orchestration over aegis's durable multi-harness substrate — interpreted by one trusted `@workflow` (`dynamic`) that drives the existing `WorkflowEngine`.

**Architecture:** A new `src/aegis/dsl/` package holds pydantic spec models, a pure selector/template resolver, a pure semantic validator, a plan-preview builder, and the `dynamic` interpreter. The interpreter is *itself* a `@workflow` — it walks the validated node tree, resolves references over a run-scoped output store, dispatches each node to an existing `WorkflowEngine` primitive (`spawn`/`send`/`close`/`bash`/`ask_human`/`checkpoint`/`resume_state`), and persists the store after every node so `WorkflowRunner.resume` replays it. A new MCP tool `aegis_run_dynamic_workflow` validates → cost-gates → launches via `WorkflowRunner.start`, mirroring the existing `aegis_run_workflow`.

**Tech Stack:** Python 3.13+, pydantic 2.12+ (discriminated unions), `jsonschema` 4.26 (already in `uv.lock` transitively; promote to a direct dependency), asyncio. Tests: pytest with the existing `FakeBridge`/`fake_bridge_with_runner` fixtures in `tests/conftest_workflows.py`.

## Global Constraints

- **Python 3.13+**; use `from __future__ import annotations` in every new module (repo convention).
- **The interpreter is a `@workflow`.** Build on `aegis.workflow.WorkflowEngine` / `WorkflowRunner` / the JSONL ledger. Never introduce a second engine or agent-spawn plane. Spec design principle 5.
- **Select, never compute.** Selectors and templates *navigate* structure only — no operators, filters, or arithmetic anywhere in the DSL. All computation is delegated to an `agent` node. Spec design principle 2.
- **Bounded control flow.** Every `loop` carries a mandatory integer `max_rounds`. Spec design principle 3.
- **Safe by construction, gated by cost only.** Structural (pydantic) + semantic (`validate`) both run *before execution*. No safety gate; a cost/scale gate only. Spec design principle 4.
- **v1 non-goals (do NOT build):** no arithmetic/filter/compute in DSL; no `group` target, `wait_any`, or cancel-losers; no Track-2-launches-Track-2 nesting; `human` node is TUI-only (no Telegram); ephemeral only (no save-as-command). Spec § v1 non-goals.
- **Two open questions are explicit decision points, not silent choices** (see § Open decisions at the end): (a) the `equals` predicate kind — deferred, NOT implemented in v1; (b) plan-preview cost estimate is a *static upper bound* and must be labelled as such in the operator prompt.
- **TDD, commit per logical unit.** Tests are hermetic pure-unit for `models`/`refs`/`validate`/`plan`; fake-bridge for the interpreter (mirror `tests/test_workflow_engine*.py`). Do NOT defer testing to the end.
- **Test command:** `uv run python -m pytest -q -m "not live"` (prefer `python -m pytest`; gate on a blast-radius subset during iteration — see AGENTS.md § Tests). Never `-k "not live"`.
- Commit straight to `main` (aegis convention); conventional commits; push after each meaningful batch.

---

## Grounding notes (spec-vs-reality — read before starting)

These are confirmed against `main` and shape several tasks. Full list is echoed in the final report.

1. **No gating/approval machinery exists yet.** `aegis_run_workflow` (`src/aegis/mcp/server.py:981`) launches non-blocking with **no operator prompt**. The spec's principle 5 says Track 2 "inherits ... the Track-1 approval/gating machinery," but § Relationship-to-Track-1 admits "Track 1 gains the gating rule stated here ... This is the 'gating is missing' piece." **The gate is genuinely new — Slice 6 builds it from scratch** (it does not exist to inherit). The v1 gate is implemented for Track 2 only (the DSL tool); wiring the same rule into the existing `aegis_run_workflow` for Track-1 Python is called out as an optional follow-on task, not required for a shippable Track 2.
2. **The interpreter persists via `engine.checkpoint`/`engine.resume_state`, not a bespoke ledger.** `WorkflowEngine.checkpoint(name, payload)` (`engine.py:379`) appends a `kind:"checkpoint"` record; `resume_state()` (`engine.py:399`) returns the **last** checkpoint payload. The interpreter snapshots its whole output store after each node into one growing checkpoint; on resume it loads that snapshot and skips already-recorded paths. `WorkflowRunner.resume(wid)` (`runner.py:300`) re-invokes the `@workflow` from the top with the same `workflow_id`, so `resume_state()` finds the snapshot — exactly the pattern `tests/test_workflow_resume.py` exercises.
3. **`engine.parallel(coros)` (`engine.py:260`) is a bare `asyncio.gather` with no concurrency cap.** The spec's "existing per-workflow concurrency cap" does not exist as a limiter here. The interpreter implements bounded fan-out itself with an `asyncio.Semaphore` for `map.concurrency` / `parallel`.
4. **Shell predicate uses `engine.bash`, not `engine.bash_predicate`.** `bash_predicate` (`engine.py:220`) is a retry-until-green loop, not a boolean. The DSL shell predicate is "true iff exit 0" → call `engine.bash(cmd, cwd=, timeout=)` and test `res["exit"] == 0`. (Spec says "reuses `bash_predicate` semantics" — reality: reuse `bash`.)
5. **Structured `agent` output is prompt-engineered + parsed, not tool-enforced.** `engine.send` (`engine.py:355`) returns free text. There is no StructuredOutput tool on aegis's spawn path. With a `schema`, the interpreter appends a "return ONLY JSON matching this schema" instruction to the prompt, then parses + `jsonschema`-validates the reply (one bounded reparse-retry on failure).
6. **`human` node maps to `engine.ask_human(question, options=, timeout=)` (`engine.py:414`)**, which returns a `str`. A `schema` with an `enum` becomes `options`; the returned string is validated/coerced against the schema. `ask_human` routes to the host's TUI input bar (headless falls back to Telegram, but v1 DSL specs are TUI-invoked — no new Telegram surface).
7. **Config for validation** is loaded at the MCP boundary via `find_project_root()` + `aegis.config.yaml_loader.load_config(root)` (the pattern in `aegis_config_list_agents`, `server.py:512`). `AegisConfig` (`config/yaml_loader.py:50`) exposes `.agents: dict[str, Agent]` and `.queues: dict[str, QueueSpec]`. `default_agent` lives on the config too. The interpreter receives `default_profile` (and the config-derived data it needs) as explicit workflow kwargs so it never touches the filesystem — keeping fake-bridge tests hermetic.

---

## File structure

New package `src/aegis/dsl/`:

- `src/aegis/dsl/__init__.py` — re-exports `Spec`, `validate`, `build_plan`, `DslValidationError`.
- `src/aegis/dsl/models.py` — pydantic models: `Meta`, targets, predicates, every node, `Spec`. Discriminated unions on `type`/`kind`. Grows one node family per slice.
- `src/aegis/dsl/refs.py` — pure: `resolve_selector(selector, store)`, `substitute(template, bindings)`, the run-scoped `Store`.
- `src/aegis/dsl/validate.py` — pure: `validate(spec, *, agents, queues, default_agent)` semantic pass → raises `DslValidationError`.
- `src/aegis/dsl/plan.py` — pure: `build_plan(spec, *, kwargs) -> PlanPreview`.
- `src/aegis/dsl/interpreter.py` — the `@workflow("dynamic")` + the `Interpreter` walker. Grows one node family per slice.
- `src/aegis/workflows/dynamic.py` — a one-line import that registers the `dynamic` workflow on package import (mirrors `src/aegis/workflows/tdd_cycle.py`; add to `src/aegis/workflows/__init__.py`).

Modified:

- `src/aegis/mcp/server.py` — add `aegis_run_dynamic_workflow` tool + register in `BRIEFING`/`PRIMING` text (Slice 6).
- `pyproject.toml` — promote `jsonschema>=4.26` to a direct dependency (Slice 2).
- `src/aegis/config/yaml_loader.py` / `config/__init__.py` — add the `dynamic_workflow_autoapprove_agents` config key (Slice 6).

Tests (new): `tests/test_dsl_models.py`, `tests/test_dsl_refs.py`, `tests/test_dsl_validate.py`, `tests/test_dsl_plan.py`, `tests/test_dsl_interpreter.py`, `tests/test_dsl_durability.py`, `tests/test_dsl_gate.py`, `tests/test_dsl_mcp.py`, `tests/test_dsl_live.py` (marked `live`).

---

## Slice overview (thinnest-first; each independently shippable)

- **Slice 1 — Walking skeleton.** `sequence` + `agent`(spawn) models + the `dynamic` `@workflow` that spawns→sends→closes each agent in order and returns the sequence output. No data-flow, no persistence. Launchable via `WorkflowRunner`.
- **Slice 2 — Data flow + durability.** `refs.py` (`Store`, selectors, `{{templates}}`), agent `inputs`/`schema` (structured parse+validate), semantic `validate.py` (upstream-only, id-uniqueness, profile existence), and per-node checkpoint/resume replay (sequence-level durability test).
- **Slice 3 — Fan-out.** `map` + `parallel` nodes with bounded concurrency (`Semaphore`), `{{item}}`/`{{index}}`, `map.over` list-source validation.
- **Slice 4 — Bounded control flow.** `loop`(mandatory `max_rounds`, `<id>.last`) + `if`, each with `shell` and `judge` predicates; per-round durability replay of loop/if decisions.
- **Slice 5 — Human in the loop.** `human` node → `engine.ask_human`, schema/enum → options, reply validation.
- **Slice 6 — Invocation + gate.** `plan.py` preview, `aegis_run_dynamic_workflow` MCP tool, the cost gate + Track-1 gating rule (operator-implicit / agent-prompts-above-threshold), config threshold key.

---

## Slice 1 — Walking skeleton (`sequence` + `agent`/spawn)

**Outcome:** `Spec.model_validate({...})` parses a `sequence` of `agent` nodes; running the `dynamic` workflow against `FakeBridge` spawns each profile, sends its prompt, closes it, and returns an object keyed by child `id`.

### Task 1.1: Spec models — subset (`Meta`, `SpawnTarget`, `AgentNode`, `SequenceNode`, `Spec`)

**Files:**
- Create: `src/aegis/dsl/__init__.py`
- Create: `src/aegis/dsl/models.py`
- Test: `tests/test_dsl_models.py`

**Interfaces:**
- Produces: `Meta(name: str, description: str = "")`; `SpawnTarget(kind: Literal["spawn"], profile: str)`; `AgentNode(type: Literal["agent"], id: str | None = None, prompt: str, target: AnyTarget | None = None)`; `SequenceNode(type: Literal["sequence"], id: str | None = None, children: list[AnyNode])`; `Spec(meta: Meta, args_schema: dict | None = None, root: AnyNode)`. `AnyNode` is a `type`-discriminated union (slice 1: `{sequence, agent}`); `AnyTarget` is a `kind`-discriminated union (slice 1: `{spawn}`). `Spec.model_validate(obj)` raises `pydantic.ValidationError` on malformed input.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dsl_models.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from aegis.dsl.models import Spec


def test_parse_minimal_sequence_of_agents():
    spec = Spec.model_validate({
        "meta": {"name": "s1", "description": "seq of agents"},
        "root": {
            "type": "sequence",
            "children": [
                {"type": "agent", "id": "a",
                 "prompt": "do a",
                 "target": {"kind": "spawn", "profile": "worker"}},
                {"type": "agent", "id": "b", "prompt": "do b",
                 "target": {"kind": "spawn", "profile": "worker"}},
            ],
        },
    })
    assert spec.meta.name == "s1"
    assert spec.root.type == "sequence"
    assert spec.root.children[0].type == "agent"
    assert spec.root.children[0].target.profile == "worker"


def test_unknown_node_type_rejected():
    with pytest.raises(ValidationError):
        Spec.model_validate({
            "meta": {"name": "bad"},
            "root": {"type": "frobnicate", "children": []},
        })


def test_agent_requires_prompt():
    with pytest.raises(ValidationError):
        Spec.model_validate({
            "meta": {"name": "bad"},
            "root": {"type": "agent",
                     "target": {"kind": "spawn", "profile": "w"}},
        })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_dsl_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.dsl'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/dsl/models.py
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class Meta(BaseModel):
    name: str
    description: str = ""


class SpawnTarget(BaseModel):
    kind: Literal["spawn"] = "spawn"
    profile: str


AnyTarget = Annotated[Union[SpawnTarget], Field(discriminator="kind")]


class AgentNode(BaseModel):
    type: Literal["agent"] = "agent"
    id: str | None = None
    prompt: str
    target: AnyTarget | None = None


class SequenceNode(BaseModel):
    type: Literal["sequence"] = "sequence"
    id: str | None = None
    children: list["AnyNode"]


AnyNode = Annotated[Union[SequenceNode, AgentNode], Field(discriminator="type")]


class Spec(BaseModel):
    meta: Meta
    args_schema: dict | None = None
    root: AnyNode


SequenceNode.model_rebuild()
```

```python
# src/aegis/dsl/__init__.py
from __future__ import annotations

from aegis.dsl.models import Spec

__all__ = ["Spec"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_dsl_models.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/dsl/__init__.py src/aegis/dsl/models.py tests/test_dsl_models.py
git commit -m "feat(dsl): spec models for sequence + agent/spawn subset"
```

### Task 1.2: The `dynamic` interpreter — sequence of spawned agents

**Files:**
- Create: `src/aegis/dsl/interpreter.py`
- Create: `src/aegis/workflows/dynamic.py`
- Modify: `src/aegis/workflows/__init__.py` (add `from aegis.workflows import dynamic  # noqa: F401` alongside the other built-ins)
- Test: `tests/test_dsl_interpreter.py`

**Interfaces:**
- Consumes: `WorkflowEngine.spawn(profile) -> str`, `WorkflowEngine.send(handle, prompt) -> str`, `WorkflowEngine.close(handle)` (`engine.py:316/355/336`); the `@workflow` decorator (`decorator.py:77`).
- Produces: `@workflow("dynamic") async def dynamic(engine, *, spec, kwargs=None, default_profile=None)`; `class Interpreter(engine, *, args, default_profile)` with `async def run_node(node, *, path, scope) -> Any`. Sequence output is a dict keyed by each id-bearing child's `id`. `agent` output is the raw reply string (slice 1).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dsl_interpreter.py
from __future__ import annotations

import pytest

from aegis.dsl.interpreter import dynamic
from aegis.workflow.engine import WorkflowEngine

pytestmark = pytest.mark.anyio  # or plain async — repo uses asyncio_mode=auto


def _engine(bridge):
    return WorkflowEngine(
        bridge=bridge, workflow_id="wf1", name="dynamic", host="h")


async def test_sequence_spawns_and_sends_in_order(fake_bridge):
    fake_bridge.set_reply_sequence("worker-1", ["reply-a"])
    fake_bridge.set_reply_sequence("worker-2", ["reply-b"])
    spec = {
        "meta": {"name": "s1"},
        "root": {"type": "sequence", "children": [
            {"type": "agent", "id": "a", "prompt": "do a",
             "target": {"kind": "spawn", "profile": "worker"}},
            {"type": "agent", "id": "b", "prompt": "do b",
             "target": {"kind": "spawn", "profile": "worker"}},
        ]},
    }
    out = await dynamic(_engine(fake_bridge), spec=spec)
    assert fake_bridge.spawned_profiles == ["worker", "worker"]
    assert fake_bridge.sends_to("worker-1") == ["do a"]
    assert fake_bridge.sends_to("worker-2") == ["do b"]
    assert out == {"a": "reply-a", "b": "reply-b"}
    # spawned agents are closed after each send
    assert set(fake_bridge.closed_handles) == {"worker-1", "worker-2"}
```

Note: `FakeBridge.spawn_subagent` names handles `f"{profile}-{counter}"` → `worker-1`, `worker-2` (`conftest_workflows.py:108`). `set_reply_sequence`/`sends_to`/`closed_handles` are existing fixture methods.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_dsl_interpreter.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.dsl.interpreter'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/dsl/interpreter.py
from __future__ import annotations

from typing import Any

from aegis.dsl.models import Spec
from aegis.workflow import workflow


class Interpreter:
    def __init__(self, engine, *, args: dict, default_profile: str | None):
        self.engine = engine
        self.args = args or {}
        self.default_profile = default_profile

    async def run_node(self, node, *, path: str, scope: dict) -> Any:
        if node.type == "sequence":
            return await self._run_sequence(node, path=path, scope=scope)
        if node.type == "agent":
            return await self._run_agent(node, path=path, scope=scope)
        raise NotImplementedError(f"node type not supported yet: {node.type}")

    async def _run_sequence(self, node, *, path, scope) -> dict:
        out: dict[str, Any] = {}
        for i, child in enumerate(node.children):
            cout = await self.run_node(
                child, path=f"{path}.{i}", scope=scope)
            if child.id:
                out[child.id] = cout
        return out

    async def _run_agent(self, node, *, path, scope) -> Any:
        profile = self._profile_of(node)
        handle = await self.engine.spawn(profile)
        try:
            reply = await self.engine.send(handle, node.prompt)
        finally:
            await self.engine.close(handle)
        return reply

    def _profile_of(self, node) -> str:
        if node.target is not None:
            return node.target.profile
        if self.default_profile is None:
            raise ValueError(
                f"agent node {node.id!r} has no target and no "
                "default_profile is configured")
        return self.default_profile


@workflow("dynamic")
async def dynamic(engine, *, spec, kwargs=None, default_profile=None):
    model = spec if isinstance(spec, Spec) else Spec.model_validate(spec)
    interp = Interpreter(
        engine, args=kwargs or {}, default_profile=default_profile)
    return await interp.run_node(model.root, path="root", scope={})
```

```python
# src/aegis/workflows/dynamic.py
"""Registers the Track-2 JSON DSL interpreter as the 'dynamic' workflow."""
from __future__ import annotations

from aegis.dsl.interpreter import dynamic  # noqa: F401  (registration side effect)
```

Add to `src/aegis/workflows/__init__.py` the same import line so it registers on package import.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_dsl_interpreter.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/dsl/interpreter.py src/aegis/workflows/dynamic.py src/aegis/workflows/__init__.py tests/test_dsl_interpreter.py
git commit -m "feat(dsl): dynamic @workflow interprets sequence of spawned agents"
```

---

## Slice 2 — Data flow + durability

**Outcome:** Agent nodes carry `inputs` (selectors) substituted into the prompt as `{{name}}`, and an optional `schema` that yields validated structured output. A run-scoped `Store` records each id-bearing node's output for later selectors. `validate()` rejects forward/unknown references, id collisions, and missing profiles before running. The interpreter checkpoints the store after each node; `WorkflowRunner.resume` replays completed nodes and re-runs only the interrupted one.

### Task 2.1: `Store` + selector/template resolution (`refs.py`)

**Files:**
- Create: `src/aegis/dsl/refs.py`
- Test: `tests/test_dsl_refs.py`

**Interfaces:**
- Produces: `class Store` with `record(path: str, node_id: str | None, value)`, `outputs: dict[str, Any]` (by path), `refs: dict[str, Any]` (by id), `snapshot() -> dict`, `load(dict)`; `resolve_selector(selector: str, store: Store) -> Any` (splits `<id>[.dotted.path]`, navigates dict keys / integer list indices / the `.last` sentinel; raises `RefError` on a missing id or path); `substitute(template: str, bindings: dict) -> str` (replaces every `{{name}}` with `str(bindings[name])`; raises `RefError` on an unbound name; no logic inside braces). `class RefError(Exception)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dsl_refs.py
from __future__ import annotations

import pytest

from aegis.dsl.refs import RefError, Store, resolve_selector, substitute


def _store():
    s = Store()
    s.record("root.0", "list", {"files": ["a.ts", "b.ts"]})
    s.record("root.1", "rounds", [{"n": 1}, {"n": 2}])
    return s


def test_resolve_whole_output():
    assert resolve_selector("list", _store()) == {"files": ["a.ts", "b.ts"]}


def test_resolve_dotted_path():
    assert resolve_selector("list.files", _store()) == ["a.ts", "b.ts"]


def test_resolve_list_index():
    assert resolve_selector("list.files.0", _store()) == "a.ts"


def test_resolve_loop_last_sentinel():
    assert resolve_selector("rounds.last", _store()) == {"n": 2}


def test_resolve_missing_id_raises():
    with pytest.raises(RefError):
        resolve_selector("nope.x", _store())


def test_substitute_binds_names():
    assert substitute("Audit {{item}} now", {"item": "a.ts"}) == "Audit a.ts now"


def test_substitute_unbound_raises():
    with pytest.raises(RefError):
        substitute("Hi {{missing}}", {})


def test_substitute_no_logic_in_braces():
    # Only bare names resolve; anything else is an unbound-name error.
    with pytest.raises(RefError):
        substitute("{{a.b + 1}}", {"a": 1})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_dsl_refs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.dsl.refs'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/dsl/refs.py
from __future__ import annotations

import re
from typing import Any

_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")


class RefError(Exception):
    """A selector or template referenced something not present/resolvable."""


class Store:
    def __init__(self) -> None:
        self.outputs: dict[str, Any] = {}   # keyed by structural path
        self.refs: dict[str, Any] = {}      # keyed by node id

    def record(self, path: str, node_id: str | None, value: Any) -> None:
        self.outputs[path] = value
        if node_id:
            self.refs[node_id] = value

    def snapshot(self) -> dict:
        return {"outputs": dict(self.outputs), "refs": dict(self.refs)}

    def load(self, snap: dict) -> None:
        self.outputs = dict(snap.get("outputs", {}))
        self.refs = dict(snap.get("refs", {}))


def resolve_selector(selector: str, store: Store) -> Any:
    parts = selector.split(".")
    head, rest = parts[0], parts[1:]
    if head not in store.refs:
        raise RefError(f"unknown reference id: {head!r}")
    value: Any = store.refs[head]
    for seg in rest:
        value = _navigate(value, seg, selector)
    return value


def _navigate(value: Any, seg: str, selector: str) -> Any:
    if seg == "last":
        if not isinstance(value, list) or not value:
            raise RefError(f"{selector!r}: '.last' needs a non-empty list")
        return value[-1]
    if isinstance(value, dict):
        if seg not in value:
            raise RefError(f"{selector!r}: no key {seg!r}")
        return value[seg]
    if isinstance(value, list):
        if not seg.isdigit():
            raise RefError(f"{selector!r}: list index must be an integer, got {seg!r}")
        idx = int(seg)
        if idx >= len(value):
            raise RefError(f"{selector!r}: index {idx} out of range")
        return value[idx]
    raise RefError(f"{selector!r}: cannot navigate into {type(value).__name__}")


def substitute(template: str, bindings: dict) -> str:
    def _repl(m: re.Match) -> str:
        name = m.group(1)
        cur: Any = bindings
        for seg in name.split("."):
            if isinstance(cur, dict) and seg in cur:
                cur = cur[seg]
            else:
                raise RefError(f"unbound template name: {{{{{name}}}}}")
        return str(cur)
    return _TEMPLATE.sub(_repl, template)
```

Note: `substitute` supports dotted names only for nested binding dicts (e.g. `args.problem` when `bindings={"args": {...}}`); a name like `a.b + 1` never matches the `[A-Za-z0-9_.]` charclass fully → treated as literal text with no `{{...}}` match, OR raises on the resolvable prefix. The test asserts `RefError`; adjust the regex/raise so a brace group that fails to fully bind raises rather than passing through. (Implementation above raises inside `_repl` for the resolvable-prefix case; a non-matching brace body is left literal — either is acceptable as long as no logic executes. If the test needs a hard raise, tighten `_repl` to reject names whose full dotted path doesn't resolve.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_dsl_refs.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/dsl/refs.py tests/test_dsl_refs.py
git commit -m "feat(dsl): run-scoped Store + selector/template resolution"
```

### Task 2.2: Extend agent model with `inputs` + `schema`; promote `jsonschema` dependency

**Files:**
- Modify: `src/aegis/dsl/models.py` (`AgentNode`)
- Modify: `pyproject.toml` (dependencies)
- Test: `tests/test_dsl_models.py` (add cases)

**Interfaces:**
- Produces: `AgentNode` gains `schema: dict | None = None` and `inputs: dict[str, str] = {}` (name → selector). A field validator rejects a `schema` that is not a valid JSON Schema (via `jsonschema.Draft202012Validator.check_schema`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dsl_models.py  (append)
def test_agent_inputs_and_schema_parse():
    from aegis.dsl.models import Spec
    spec = Spec.model_validate({
        "meta": {"name": "s"},
        "root": {"type": "agent", "id": "r", "prompt": "merge {{all}}",
                 "inputs": {"all": "audits"},
                 "schema": {"type": "object",
                            "properties": {"x": {"type": "string"}}}},
    })
    assert spec.root.inputs == {"all": "audits"}
    assert spec.root.schema_["type"] == "object"


def test_agent_invalid_json_schema_rejected():
    import pytest
    from pydantic import ValidationError
    from aegis.dsl.models import Spec
    with pytest.raises(ValidationError):
        Spec.model_validate({
            "meta": {"name": "s"},
            "root": {"type": "agent", "prompt": "x",
                     "schema": {"type": "not-a-real-type"}}})
```

Note: `schema` collides with pydantic's `BaseModel.schema`; declare the field with `alias="schema"` and Python attribute `schema_`. Use `model_config = ConfigDict(populate_by_name=True)` so both `schema` (wire) and `schema_` (attr) work.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_dsl_models.py -q`
Expected: FAIL — `AttributeError`/`ValidationError` mismatch (fields absent).

- [ ] **Step 3: Write minimal implementation**

Add to `pyproject.toml` dependencies: `"jsonschema>=4.26",` (already resolved in `uv.lock`; this promotes it to a direct dep — run `uv lock` after).

```python
# src/aegis/dsl/models.py  (AgentNode replacement)
from pydantic import ConfigDict, field_validator


class AgentNode(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    type: Literal["agent"] = "agent"
    id: str | None = None
    prompt: str
    target: AnyTarget | None = None
    schema_: dict | None = Field(default=None, alias="schema")
    inputs: dict[str, str] = Field(default_factory=dict)

    @field_validator("schema_")
    @classmethod
    def _valid_json_schema(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        from jsonschema import Draft202012Validator
        Draft202012Validator.check_schema(v)  # raises SchemaError → ValidationError
        return v
```

- [ ] **Step 4: Run test + verify**

Run: `uv run python -m pytest tests/test_dsl_models.py -q`
Expected: PASS. Then `uv lock` and confirm `jsonschema` is a direct dep.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/dsl/models.py pyproject.toml uv.lock tests/test_dsl_models.py
git commit -m "feat(dsl): agent inputs + JSON-Schema-validated schema field"
```

### Task 2.3: Interpreter — resolve `inputs`, substitute prompt, parse+validate structured output, record to Store

**Files:**
- Modify: `src/aegis/dsl/interpreter.py`
- Test: `tests/test_dsl_interpreter.py` (add cases)

**Interfaces:**
- Consumes: `Store`, `resolve_selector`, `substitute` (Task 2.1); `AgentNode.inputs`/`schema_` (Task 2.2).
- Produces: `Interpreter` now owns a `self.store: Store`; `_run_agent` resolves each input selector to a value, builds `bindings = {**inputs, "args": self.args, **scope}`, substitutes the prompt, appends a schema instruction when `schema_` is set, sends, and — with a schema — parses JSON from the reply and `jsonschema`-validates it (one reparse-retry with a corrective send on failure, else `WorkflowError`). Records `(path, node.id, output)` into `self.store` before returning.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dsl_interpreter.py  (append)
import json


async def test_agent_inputs_substituted_and_output_referenceable(fake_bridge):
    # First agent returns structured JSON; second consumes it via inputs.
    fake_bridge.set_reply_sequence(
        "lister-1", [json.dumps({"files": ["a.ts", "b.ts"]})])
    fake_bridge.set_reply_sequence("merger-2", ["merged"])
    spec = {
        "meta": {"name": "s"},
        "root": {"type": "sequence", "children": [
            {"type": "agent", "id": "list", "prompt": "list files",
             "target": {"kind": "spawn", "profile": "lister"},
             "schema": {"type": "object", "required": ["files"],
                        "properties": {"files": {"type": "array"}}}},
            {"type": "agent", "id": "report", "prompt": "merge {{all}}",
             "target": {"kind": "spawn", "profile": "merger"},
             "inputs": {"all": "list"}},
        ]},
    }
    out = await dynamic(_engine(fake_bridge), spec=spec)
    assert out["list"] == {"files": ["a.ts", "b.ts"]}
    # The merger's prompt had the whole prior output substituted in.
    assert "a.ts" in fake_bridge.sends_to("merger-2")[0]


async def test_agent_schema_violation_after_retry_raises(fake_bridge):
    fake_bridge.set_reply_sequence("w-1", ["not json", "still not json"])
    spec = {"meta": {"name": "s"},
            "root": {"type": "agent", "id": "x", "prompt": "p",
                     "target": {"kind": "spawn", "profile": "w"},
                     "schema": {"type": "object", "required": ["k"],
                                "properties": {"k": {"type": "string"}}}}}
    from aegis.workflow.decorator import WorkflowError
    with pytest.raises(WorkflowError):
        await dynamic(_engine(fake_bridge), spec=spec)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_dsl_interpreter.py -q`
Expected: FAIL — inputs not substituted / no schema parse.

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/dsl/interpreter.py  (Interpreter additions)
import json

from aegis.dsl.refs import RefError, Store, resolve_selector, substitute
from aegis.workflow.decorator import WorkflowError

_SCHEMA_HINT = (
    "\n\nReturn ONLY a JSON object matching this JSON Schema, no prose:\n{schema}")


class Interpreter:
    def __init__(self, engine, *, args, default_profile):
        self.engine = engine
        self.args = args or {}
        self.default_profile = default_profile
        self.store = Store()

    # run_node / _run_sequence unchanged from slice 1, except _run_sequence
    # records nothing itself; leaf recording happens in _run_agent.

    async def _run_agent(self, node, *, path, scope) -> Any:
        bindings = self._bindings(node, scope)
        prompt = substitute(node.prompt, bindings)
        if node.schema_ is not None:
            prompt = prompt + _SCHEMA_HINT.format(
                schema=json.dumps(node.schema_))
        profile = self._profile_of(node)
        handle = await self.engine.spawn(profile)
        try:
            reply = await self.engine.send(handle, prompt)
            output = await self._coerce(node, handle, reply)
        finally:
            await self.engine.close(handle)
        self.store.record(path, node.id, output)
        return output

    def _bindings(self, node, scope) -> dict:
        b: dict[str, Any] = {"args": self.args}
        b.update(scope)  # item / index inside map bodies (slice 3)
        for name, selector in node.inputs.items():
            b[name] = resolve_selector(selector, self.store)
        return b

    async def _coerce(self, node, handle, reply):
        if node.schema_ is None:
            return reply
        from jsonschema import Draft202012Validator
        validator = Draft202012Validator(node.schema_)
        for attempt in range(2):
            try:
                parsed = json.loads(_extract_json(reply))
                validator.validate(parsed)
                return parsed
            except Exception as e:  # noqa: BLE001
                if attempt == 1:
                    raise WorkflowError(
                        f"agent {node.id!r} did not return schema-valid "
                        f"JSON after retry: {e}") from e
                reply = await self.engine.send(
                    handle,
                    "Your last reply was not valid JSON for the schema. "
                    f"Return ONLY the JSON object. Error: {e}")
        return reply  # unreachable


def _extract_json(text: str) -> str:
    """Pull the outermost {...} or [...] block from a possibly-chatty reply."""
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            return text[start:end + 1]
    return text
```

- [ ] **Step 4: Run test + verify**

Run: `uv run python -m pytest tests/test_dsl_interpreter.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/dsl/interpreter.py tests/test_dsl_interpreter.py
git commit -m "feat(dsl): agent inputs substitution + structured-output coercion"
```

### Task 2.4: Semantic validator (`validate.py`) — upstream-only, id-uniqueness, profile existence

**Files:**
- Create: `src/aegis/dsl/validate.py`
- Modify: `src/aegis/dsl/__init__.py` (export `validate`, `DslValidationError`)
- Test: `tests/test_dsl_validate.py`

**Interfaces:**
- Produces: `class DslValidationError(Exception)`; `validate(spec: Spec, *, agents: set[str], queues: set[str], default_agent: str | None) -> None`. Raises on: id collision; a selector (`inputs` value, later `map.over`/judge `inputs`) that references an id not declared *earlier in document order*; an agent node with no `target` when `default_agent` is None; a `spawn.profile` not in `agents`; (slice 4+) a `queue.queue` not in `queues`. `session.handle` is **not** checked (deferred to runtime per spec § Validation).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dsl_validate.py
from __future__ import annotations

import pytest

from aegis.dsl.models import Spec
from aegis.dsl.validate import DslValidationError, validate

AGENTS = {"worker", "lister", "merger"}


def _v(spec_dict, **kw):
    kw.setdefault("agents", AGENTS)
    kw.setdefault("queues", set())
    kw.setdefault("default_agent", "worker")
    validate(Spec.model_validate(spec_dict), **kw)


def test_valid_upstream_reference_passes():
    _v({"meta": {"name": "ok"}, "root": {"type": "sequence", "children": [
        {"type": "agent", "id": "list", "prompt": "p",
         "target": {"kind": "spawn", "profile": "lister"}},
        {"type": "agent", "id": "r", "prompt": "{{a}}", "inputs": {"a": "list"},
         "target": {"kind": "spawn", "profile": "merger"}}]}})


def test_forward_reference_rejected():
    with pytest.raises(DslValidationError):
        _v({"meta": {"name": "bad"}, "root": {"type": "sequence", "children": [
            {"type": "agent", "id": "r", "prompt": "{{a}}", "inputs": {"a": "later"},
             "target": {"kind": "spawn", "profile": "merger"}},
            {"type": "agent", "id": "later", "prompt": "p",
             "target": {"kind": "spawn", "profile": "lister"}}]}})


def test_id_collision_rejected():
    with pytest.raises(DslValidationError):
        _v({"meta": {"name": "bad"}, "root": {"type": "sequence", "children": [
            {"type": "agent", "id": "dup", "prompt": "p",
             "target": {"kind": "spawn", "profile": "worker"}},
            {"type": "agent", "id": "dup", "prompt": "p",
             "target": {"kind": "spawn", "profile": "worker"}}]}})


def test_unknown_profile_rejected():
    with pytest.raises(DslValidationError):
        _v({"meta": {"name": "bad"},
            "root": {"type": "agent", "id": "x", "prompt": "p",
                     "target": {"kind": "spawn", "profile": "ghost"}}})


def test_missing_target_without_default_rejected():
    with pytest.raises(DslValidationError):
        _v({"meta": {"name": "bad"},
            "root": {"type": "agent", "id": "x", "prompt": "p"}},
           default_agent=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_dsl_validate.py -q`
Expected: FAIL — no `validate` module.

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/dsl/validate.py
from __future__ import annotations

from aegis.dsl.models import Spec


class DslValidationError(Exception):
    """Semantic validation failed (references, ids, profiles/queues)."""


def validate(spec: Spec, *, agents: set[str], queues: set[str],
             default_agent: str | None) -> None:
    seen_ids: set[str] = set()
    _walk(spec.root, seen_ids, agents=agents, queues=queues,
          default_agent=default_agent)


def _walk(node, seen_ids, *, agents, queues, default_agent) -> None:
    t = node.type
    if t == "sequence":
        for child in node.children:
            _walk(child, seen_ids, agents=agents, queues=queues,
                  default_agent=default_agent)
        if node.id:
            _add_id(node.id, seen_ids)
        return
    if t == "agent":
        for selector in node.inputs.values():
            _check_ref(selector, seen_ids)
        _check_target(node, agents, queues, default_agent)
        if node.id:
            _add_id(node.id, seen_ids)
        return
    raise DslValidationError(f"unknown node type in validate: {t!r}")


def _add_id(node_id: str, seen_ids: set[str]) -> None:
    if node_id in seen_ids:
        raise DslValidationError(f"duplicate node id: {node_id!r}")
    seen_ids.add(node_id)


def _check_ref(selector: str, seen_ids: set[str]) -> None:
    head = selector.split(".")[0]
    if head not in seen_ids:
        raise DslValidationError(
            f"reference {selector!r} points at id {head!r} which is not a "
            "declared upstream node")


def _check_target(node, agents, queues, default_agent) -> None:
    target = node.target
    if target is None:
        if default_agent is None:
            raise DslValidationError(
                f"agent {node.id!r} omits target but no default_agent is set")
        return
    if target.kind == "spawn" and target.profile not in agents:
        raise DslValidationError(
            f"spawn.profile {target.profile!r} is not a configured agent")
    if target.kind == "queue" and target.queue not in queues:
        raise DslValidationError(
            f"queue.queue {target.queue!r} is not a configured queue")
    # session.handle deferred to runtime (spec § Validation).
```

Update `src/aegis/dsl/__init__.py` to export `validate`, `DslValidationError`.

Note: `_add_id` runs *after* the node's own refs are checked and (for sequences) after children — so id order = document order, giving upstream-only for free. A sequence records its children before itself; that's fine since sequence ids aren't referenced by their own subtree.

- [ ] **Step 4: Run test + verify**

Run: `uv run python -m pytest tests/test_dsl_validate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/dsl/validate.py src/aegis/dsl/__init__.py tests/test_dsl_validate.py
git commit -m "feat(dsl): semantic validator — upstream refs, id uniqueness, profiles"
```

### Task 2.5: Durability — checkpoint the store per node; replay completed nodes on resume

**Files:**
- Modify: `src/aegis/dsl/interpreter.py`
- Test: `tests/test_dsl_durability.py`

**Interfaces:**
- Consumes: `WorkflowEngine.checkpoint(name, payload)` / `resume_state()` (`engine.py:379/399`); `Store.snapshot()`/`load()`; `WorkflowRunner.resume(wid)` (`runner.py:300`).
- Produces: `Interpreter.run_node` short-circuits — before executing an id-bearing or path-keyed node, if `path in self.store.outputs` it returns the recorded value without re-running. After each leaf/branch records its output, the interpreter calls `await engine.checkpoint("dsl", self.store.snapshot())`. `dynamic` loads `snap = await engine.resume_state()` and `self.store.load(snap)` when present, then re-walks from `root`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dsl_durability.py
from __future__ import annotations

import asyncio
import json

import pytest

from aegis.workflow.decorator import _REGISTRY
from aegis.workflows import dynamic as _dyn_mod  # noqa: F401  (registers "dynamic")


@pytest.fixture(autouse=True)
def _keep_registry():
    # 'dynamic' is a package-level registration; don't clear it here.
    yield


async def test_completed_nodes_replay_only_inflight_reruns(
        fake_bridge_with_runner, tmp_path):
    """First agent completes + checkpoints; run 'crashes' in the second
    agent. Resume must NOT re-spawn the first agent, only the second."""
    br = fake_bridge_with_runner
    br.set_state_dir(tmp_path / "wf")
    runner = br.workflow_runner

    # Reply plan: lister returns JSON once; merger fails on first launch,
    # succeeds after resume.
    br.set_reply_sequence("lister-1", [json.dumps({"files": ["a.ts"]})])

    calls: list[str] = []
    real_spawn = br.spawn_subagent

    async def _counting_spawn(profile, *, alias=None):
        calls.append(profile)
        if profile == "merger" and calls.count("merger") == 1:
            raise RuntimeError("simulated crash on first merger spawn")
        return await real_spawn(profile, alias=alias)

    br.spawn_subagent = _counting_spawn  # type: ignore[assignment]
    br.set_reply_sequence("merger-2", ["done"])

    spec = {"meta": {"name": "s"}, "root": {"type": "sequence", "children": [
        {"type": "agent", "id": "list", "prompt": "p",
         "target": {"kind": "spawn", "profile": "lister"},
         "schema": {"type": "object", "properties": {"files": {"type": "array"}}}},
        {"type": "agent", "id": "rep", "prompt": "merge {{a}}",
         "inputs": {"a": "list"},
         "target": {"kind": "spawn", "profile": "merger"}}]}}

    wid = await runner.start("dynamic", {"spec": spec}, host="h")
    for _ in range(50):
        await asyncio.sleep(0.01)
        if runner.status(wid).get("status") != "running":
            break
    assert runner.status(wid)["status"] == "error"
    assert calls == ["lister", "merger"]  # lister ran once, merger tried once

    await runner.resume(wid)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if runner.status(wid).get("status") in {"ok", "error"}:
            break
    assert runner.status(wid)["status"] == "ok"
    # lister must NOT have re-spawned; only merger re-ran.
    assert calls == ["lister", "merger", "merger"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_dsl_durability.py -q`
Expected: FAIL — the first agent re-spawns on resume (`calls` has two `lister`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/dsl/interpreter.py  (run_node + dynamic changes)

    async def run_node(self, node, *, path, scope) -> Any:
        if path in self.store.outputs:
            return self.store.outputs[path]           # replay — do not re-run
        if node.type == "sequence":
            return await self._run_sequence(node, path=path, scope=scope)
        if node.type == "agent":
            out = await self._run_agent(node, path=path, scope=scope)
            await self._checkpoint()
            return out
        raise NotImplementedError(f"node type not supported yet: {node.type}")

    async def _checkpoint(self) -> None:
        try:
            await self.engine.checkpoint("dsl", self.store.snapshot())
        except RuntimeError:
            pass  # no runner/state_dir (pure unit tests) — durability is opt-in


# dynamic(): load prior snapshot before walking
@workflow("dynamic")
async def dynamic(engine, *, spec, kwargs=None, default_profile=None):
    model = spec if isinstance(spec, Spec) else Spec.model_validate(spec)
    interp = Interpreter(engine, args=kwargs or {}, default_profile=default_profile)
    snap = await engine.resume_state()
    if snap:
        interp.store.load(snap)
    return await interp.run_node(model.root, path="root", scope={})
```

Note: `_run_agent` already records into `self.store` (Task 2.3). The `sequence` node itself is not path-recorded here (its output is reconstructed from children on replay via the short-circuit on each child); the root sequence re-derives its dict from the already-replayed child outputs — correct because children short-circuit. If a later slice needs the sequence's own path recorded, add it after the loop.

- [ ] **Step 4: Run test + verify**

Run: `uv run python -m pytest tests/test_dsl_durability.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/dsl/interpreter.py tests/test_dsl_durability.py
git commit -m "feat(dsl): per-node checkpoint + resume replay (only in-flight node re-runs)"
```

---

## Slice 3 — Fan-out (`map` + `parallel`)

**Outcome:** `parallel` runs children concurrently (barrier); `map` runs a body once per element of a resolved list with `{{item}}`/`{{index}}` in scope, honoring an optional `concurrency` cap. `validate` confirms `map.over` targets a list-producing upstream node.

### Task 3.1: Models — `ParallelNode`, `MapNode`; extend `AnyNode` union

**Files:**
- Modify: `src/aegis/dsl/models.py`
- Test: `tests/test_dsl_models.py` (append)

**Interfaces:**
- Produces: `ParallelNode(type: Literal["parallel"], id: str | None = None, children: list[AnyNode])`; `MapNode(type: Literal["map"], id: str, over: str, body: AnyNode, concurrency: int | None = None)` — `id` and `over` are **required** (spec § Data flow: `map` requires an id). `AnyNode` union extended to `{sequence, parallel, map, agent}`; `model_rebuild()` re-run.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dsl_models.py  (append)
def test_map_requires_id_and_over():
    import pytest
    from pydantic import ValidationError
    from aegis.dsl.models import Spec
    with pytest.raises(ValidationError):
        Spec.model_validate({"meta": {"name": "s"},
            "root": {"type": "map", "over": "list.files",
                     "body": {"type": "agent", "prompt": "p",
                              "target": {"kind": "spawn", "profile": "w"}}}})


def test_parallel_and_map_parse():
    from aegis.dsl.models import Spec
    spec = Spec.model_validate({"meta": {"name": "s"},
        "root": {"type": "map", "id": "audits", "over": "list.files",
                 "concurrency": 4,
                 "body": {"type": "parallel", "children": [
                     {"type": "agent", "prompt": "{{item}}",
                      "target": {"kind": "spawn", "profile": "w"}}]}}})
    assert spec.root.type == "map"
    assert spec.root.over == "list.files"
    assert spec.root.concurrency == 4
    assert spec.root.body.type == "parallel"
```

- [ ] **Step 2: Run — FAIL** (`map`/`parallel` not in union).
- [ ] **Step 3: Implement** — add both classes, extend `AnyNode`, `model_rebuild()` on all recursive models.
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** `feat(dsl): map + parallel node models`.

### Task 3.2: Interpreter — `parallel` (barrier) + `map` (bounded fan-out, item/index scope)

**Files:**
- Modify: `src/aegis/dsl/interpreter.py`
- Test: `tests/test_dsl_interpreter.py` (append)

**Interfaces:**
- Consumes: `WorkflowEngine.parallel(coros)` (`engine.py:260`, bare `gather`); `resolve_selector` for `map.over`; `asyncio.Semaphore` for concurrency.
- Produces: `run_node` handles `parallel` (dict keyed by id-bearing children, run under `asyncio.gather`) and `map` (list of body outputs, `scope` extended with `item`/`index`, capped by `Semaphore(node.concurrency or DEFAULT_CONCURRENCY)`; `DEFAULT_CONCURRENCY = 8`). Body path is `f"{path}#{index}"` so each element replays independently.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dsl_interpreter.py  (append)
import json


async def test_map_fans_out_over_list(fake_bridge):
    fake_bridge.set_reply_sequence(
        "lister-1", [json.dumps({"files": ["a.ts", "b.ts"]})])
    # map bodies spawn "auditor" per file → auditor-2, auditor-3
    fake_bridge.set_reply_sequence("auditor-2", ["found in a"])
    fake_bridge.set_reply_sequence("auditor-3", ["found in b"])
    spec = {"meta": {"name": "s"}, "root": {"type": "sequence", "children": [
        {"type": "agent", "id": "list", "prompt": "list",
         "target": {"kind": "spawn", "profile": "lister"},
         "schema": {"type": "object", "properties": {"files": {"type": "array"}}}},
        {"type": "map", "id": "audits", "over": "list.files", "concurrency": 2,
         "body": {"type": "agent", "prompt": "audit {{item}} idx {{index}}",
                  "target": {"kind": "spawn", "profile": "auditor"}}}]}}
    out = await dynamic(_engine(fake_bridge), spec=spec)
    assert out["audits"] == ["found in a", "found in b"]
    prompts = fake_bridge.sends_to("auditor-2") + fake_bridge.sends_to("auditor-3")
    assert any("audit a.ts idx 0" in p for p in prompts)
    assert any("audit b.ts idx 1" in p for p in prompts)
```

- [ ] **Step 2: Run — FAIL** (map/parallel raise `NotImplementedError`).
- [ ] **Step 3: Implement:**

```python
# src/aegis/dsl/interpreter.py  (run_node additions)
import asyncio

DEFAULT_CONCURRENCY = 8

    # inside run_node dispatch:
        if node.type == "parallel":
            out = await self._run_parallel(node, path=path, scope=scope)
            await self._checkpoint()
            return out
        if node.type == "map":
            out = await self._run_map(node, path=path, scope=scope)
            self.store.record(path, node.id, out)
            await self._checkpoint()
            return out

    async def _run_parallel(self, node, *, path, scope) -> dict:
        idx_children = list(enumerate(node.children))
        results = await self.engine.parallel([
            self.run_node(c, path=f"{path}.{i}", scope=scope)
            for i, c in idx_children])
        return {c.id: r for (i, c), r in zip(idx_children, results) if c.id}

    async def _run_map(self, node, *, path, scope) -> list:
        items = resolve_selector(node.over, self.store)
        if not isinstance(items, list):
            raise WorkflowError(
                f"map.over {node.over!r} did not resolve to a list")
        sem = asyncio.Semaphore(node.concurrency or DEFAULT_CONCURRENCY)

        async def _one(i, item):
            async with sem:
                child_scope = {**scope, "item": item, "index": i}
                return await self.run_node(
                    node.body, path=f"{path}#{i}", scope=child_scope)

        return list(await asyncio.gather(
            *[_one(i, it) for i, it in enumerate(items)]))
```

- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** `feat(dsl): map bounded fan-out + parallel barrier`.

### Task 3.3: Validate `map.over` list-source

**Files:**
- Modify: `src/aegis/dsl/validate.py` (`_walk` handles `map`/`parallel`)
- Test: `tests/test_dsl_validate.py` (append)

**Interfaces:**
- Produces: `_walk` recurses into `parallel.children` and `map.body`; checks `map.over` head is a declared-upstream id; records `map.id`. Within a `map.body`, `{{item}}`/`{{index}}` are always in scope, so a body input selector of literally `item`/`index` is not treated as a node reference (skip refs whose head is `item`/`index`).

- [ ] **Step 1: Test** — `map.over` referencing a non-upstream id → `DslValidationError`; a valid `list.files` upstream passes; a body that references `item` does not raise.
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** the two branches + the `item`/`index` skip in `_check_ref` callers.
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** `feat(dsl): validate map.over list-source + parallel recursion`.

---

## Slice 4 — Bounded control flow (`loop` + `if`, `shell` + `judge` predicates)

**Outcome:** `loop` runs its body up to `max_rounds`, evaluating a typed `until` predicate after each round (`<id>.last` selects the final round output); `if` runs `then`/`else` on a typed `cond`. Predicates are `shell` (exit 0) or `judge` (an agent returns `{decision, reason}`). Per-round outputs and per-round/branch decisions persist so resume replays them and only the interrupted round/branch re-runs.

### Task 4.1: Models — predicates + `LoopNode` + `IfNode`

**Files:**
- Modify: `src/aegis/dsl/models.py`
- Test: `tests/test_dsl_models.py` (append)

**Interfaces:**
- Produces: `ShellPredicate(kind: Literal["shell"], cmd: str, cwd: str | None = None, timeout: int | None = None)`; `JudgePredicate(kind: Literal["judge"], condition: str, inputs: list[str] = [])`; `AnyPredicate = Annotated[Union[ShellPredicate, JudgePredicate], Field(discriminator="kind")]`; `LoopNode(type: Literal["loop"], id: str, body: AnyNode, until: AnyPredicate, max_rounds: int)` — `id` and `max_rounds` **required** (`max_rounds` a strictly-positive int via `Field(gt=0)`); `IfNode(type: Literal["if"], id: str | None = None, cond: AnyPredicate, then: AnyNode, else_: AnyNode | None = Field(default=None, alias="else"))`. Union extended to include `loop`, `if`.

- [ ] **Step 1: Test** — `max_rounds` missing → `ValidationError`; `max_rounds: 0` → `ValidationError`; `if` with `else` (aliased) parses; unknown predicate kind → `ValidationError`.
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** predicates + nodes; `IfNode` uses `populate_by_name=True` for the `else`/`else_` alias; `model_rebuild()`.
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** `feat(dsl): loop/if node + shell/judge predicate models`.

### Task 4.2: Predicate evaluation — `shell` via `engine.bash`, `judge` via agent

**Files:**
- Modify: `src/aegis/dsl/interpreter.py`
- Test: `tests/test_dsl_interpreter.py` (append)

**Interfaces:**
- Consumes: `WorkflowEngine.bash(cmd, cwd=, timeout=) -> _BashResult` (indexable `["exit"]`, `engine.py:174`); `engine.spawn`/`send`/`close` for judge; `resolve_selector` for judge `inputs`.
- Produces: `Interpreter._eval_predicate(pred, *, path, scope, last) -> bool`. `shell` → `(await engine.bash(pred.cmd, cwd=pred.cwd, timeout=pred.timeout))["exit"] == 0`. `judge` → spawn the default profile, send a prompt built from `pred.condition` + the resolved `inputs` (default: `last`, the body's last result), instruct a `{"decision": bool, "reason"?: str}` reply, parse, return `decision`. Judge decisions are recorded under `f"{path}::pred"` for replay.

- [ ] **Step 1: Test** — a `shell` predicate whose bash sequence returns `{"exit": 0}` → True; `{"exit": 1}` → False; a `judge` whose agent replies `{"decision": true, "reason": "ok"}` → True. Use `fake_bridge.set_bash_sequence([...])` and `set_reply_sequence`.
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** `_eval_predicate` (+ a `_run_judge` helper reusing `_extract_json`).
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** `feat(dsl): shell + judge predicate evaluation`.

### Task 4.3: Interpreter — `loop` (max_rounds, per-round output, `.last`) + `if` (branch)

**Files:**
- Modify: `src/aegis/dsl/interpreter.py`
- Test: `tests/test_dsl_interpreter.py` (append)

**Interfaces:**
- Produces: `run_node` handles `loop` — run `body` at `f"{path}#round{n}"`, collect per-round outputs into a list recorded under `node.id`, evaluate `until` after each round with `last` = the round's output; stop when `until` is True or `n+1 == max_rounds`. `if` — evaluate `cond`; run `then` at `f"{path}.then"` or `else_` at `f"{path}.else"`; output is the taken branch's output (or `None` if `else` absent and cond False). Checkpoint after each round and after the branch.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dsl_interpreter.py  (append)
async def test_loop_stops_on_shell_green(fake_bridge):
    # First round: tsc fails (exit 1) → loop again. Second round: exit 0 → stop.
    fake_bridge.set_bash_sequence([{"exit": 1, "stdout": "", "stderr": ""},
                                   {"exit": 0, "stdout": "", "stderr": ""}])
    fake_bridge.set_reply_sequence("fixer-1", ["fixed round 1"])
    fake_bridge.set_reply_sequence("fixer-2", ["fixed round 2"])
    spec = {"meta": {"name": "s"},
            "root": {"type": "loop", "id": "rounds", "max_rounds": 4,
                     "until": {"kind": "shell", "cmd": "tsc --noEmit"},
                     "body": {"type": "agent", "prompt": "fix",
                              "target": {"kind": "spawn", "profile": "fixer"}}}}
    out = await dynamic(_engine(fake_bridge), spec=spec, default_profile="fixer")
    assert out == ["fixed round 1", "fixed round 2"]  # stopped after 2 rounds


async def test_loop_respects_max_rounds(fake_bridge):
    fake_bridge.set_bash_sequence([{"exit": 1}, {"exit": 1}])  # never green
    fake_bridge.set_reply_sequence("fixer-1", ["r1"])
    fake_bridge.set_reply_sequence("fixer-2", ["r2"])
    spec = {"meta": {"name": "s"},
            "root": {"type": "loop", "id": "rounds", "max_rounds": 2,
                     "until": {"kind": "shell", "cmd": "false"},
                     "body": {"type": "agent", "prompt": "fix",
                              "target": {"kind": "spawn", "profile": "fixer"}}}}
    out = await dynamic(_engine(fake_bridge), spec=spec, default_profile="fixer")
    assert len(out) == 2  # capped at max_rounds
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** the `loop`/`if` branches per the interface above.
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** `feat(dsl): loop with max_rounds + if branch routing`.

### Task 4.4: Durability — loop/if decisions replay across resume

**Files:**
- Modify: `src/aegis/dsl/interpreter.py` (record predicate decisions in the store; short-circuit rounds/branches on replay)
- Test: `tests/test_dsl_durability.py` (append)

**Interfaces:**
- Produces: recorded round outputs (`f"{path}#round{n}"`) and predicate decisions (`f"{path}#round{n}::pred"`, `f"{path}::cond"`) so a resumed `loop` re-reads completed rounds + their `until` decisions from the store and continues from the first unrecorded round; a resumed `if` re-reads its recorded `cond` decision rather than re-evaluating a (possibly nondeterministic) `judge`. Per spec § Execution-and-durability: "replays recorded loop/if/judge decisions ... only the interrupted step re-evaluates."

- [ ] **Step 1: Write the failing test** — start a `loop` spec whose round-2 body raises; assert round-1 body output + round-1 `until` decision are checkpointed; resume; assert round-1 does NOT re-run (body spawn count for round 1 stays 1) and the round-1 `until` shell command is NOT re-evaluated (bash call count for round 1 unchanged). Mirror the crash-injection pattern from Task 2.5.
- [ ] **Step 2: Run — FAIL** (round 1 re-runs).
- [ ] **Step 3: Implement** the round/branch/decision short-circuits keyed on `self.store.outputs`.
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** `feat(dsl): loop/if decision replay on resume`.

---

## Slice 5 — Human in the loop (`human` node)

**Outcome:** A `human` node pauses the run and asks the operator via the TUI (`engine.ask_human`); a `schema` with an `enum` surfaces as selectable options; the reply is validated/coerced against the schema and recorded like any other node output (referenceable by `id`).

### Task 5.1: Model — `HumanNode`

**Files:**
- Modify: `src/aegis/dsl/models.py`
- Test: `tests/test_dsl_models.py` (append)

**Interfaces:**
- Produces: `HumanNode(type: Literal["human"], id: str | None = None, question: str, schema_: dict | None = Field(default=None, alias="schema"))`. Union extended to include `human`.

- [ ] **Step 1: Test** — parse a `human` node with an `enum` schema. **Step 2:** FAIL. **Step 3:** add class + union + `model_rebuild`. **Step 4:** PASS. **Step 5:** commit `feat(dsl): human node model`.

### Task 5.2: Interpreter — `human` via `engine.ask_human`, enum→options, coerce reply

**Files:**
- Modify: `src/aegis/dsl/interpreter.py`
- Test: `tests/test_dsl_interpreter.py` (append)

**Interfaces:**
- Consumes: `WorkflowEngine.ask_human(question, options=, timeout=) -> str` (`engine.py:414`). The `FakeBridge.register_human_question` (`conftest_workflows.py:96`) resolves from a per-host reply queue seeded via `enqueue_reply`.
- Produces: `run_node` handles `human` — `options = schema_["enum"]` when the schema is `{"type": "string", "enum": [...]}`, else `None`; substitute `{{...}}` in `question` first; `reply = await engine.ask_human(question, options=options)`; if a schema is present, validate the coerced reply (string enum → the string; object schema → parse JSON like `_coerce`) and raise `WorkflowError` on mismatch; record under `node.id`; checkpoint.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dsl_interpreter.py  (append)
async def test_human_node_asks_and_records_choice(fake_bridge):
    fake_bridge.enqueue_reply("h", "proceed")
    spec = {"meta": {"name": "s"},
            "root": {"type": "human", "id": "gate1",
                     "question": "Proceed or revise?",
                     "schema": {"type": "string", "enum": ["proceed", "revise"]}}}
    out = await dynamic(_engine(fake_bridge), spec=spec)
    assert out == "proceed"
    assert fake_bridge.last_options("h") == ["proceed", "revise"]
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** the `human` branch + enum/option mapping + coercion.
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** `feat(dsl): human node via ask_human with enum options`.

### Task 5.3: Validate — `human` recursion, judge `inputs` upstream refs

**Files:**
- Modify: `src/aegis/dsl/validate.py`
- Test: `tests/test_dsl_validate.py` (append)

**Interfaces:**
- Produces: `_walk` handles `human` (record id) and `loop`/`if` (recurse into `body`/`then`/`else_`, check the predicate's judge `inputs` selectors + `map.over`-style rules are upstream, record `loop.id`/`if.id`). A judge `inputs` default (empty) is fine (means "the body's last result").

- [ ] **Step 1: Test** — a `judge.inputs` selector referencing a downstream id → `DslValidationError`; a `human` node id becomes referenceable by a later `if` cond judge input (design-thinking `gate1` case). **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5:** commit `feat(dsl): validate human + control-flow recursion`.

---

## Slice 6 — Invocation surface + cost gate

**Outcome:** `plan.py` builds a plan preview (node graph + projected agent count + labelled upper-bound estimate). `aegis_run_dynamic_workflow` validates (structural at the pydantic boundary + semantic via `validate`), gates on projected agent count vs the config threshold, and — when under threshold or operator-invoked — launches the `dynamic` workflow via `WorkflowRunner.start` exactly like `aegis_run_workflow`.

### Task 6.1: `plan.py` — plan preview + projected agent count

**Files:**
- Create: `src/aegis/dsl/plan.py`
- Modify: `src/aegis/dsl/__init__.py` (export `build_plan`, `PlanPreview`)
- Test: `tests/test_dsl_plan.py`

**Interfaces:**
- Produces: `class PlanPreview` with `projected_agents: int`, `is_upper_bound: bool`, `lines: list[str]`, `render() -> str`; `build_plan(spec: Spec, *, kwargs: dict | None = None) -> PlanPreview`. Counting rule: each `agent` node = 1; each `judge` predicate = 1; a `loop` multiplies its body's count by `max_rounds` and sets `is_upper_bound=True`; a `map` over a not-yet-known list contributes its body count once and sets `is_upper_bound=True` (length unknown pre-run). `human` nodes contribute 0 agents. `render()` prints the tree + a footer line: `"Projected agents: N (upper bound)"` when `is_upper_bound`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dsl_plan.py
from __future__ import annotations

from aegis.dsl.models import Spec
from aegis.dsl.plan import build_plan


def test_static_sequence_exact_count():
    spec = Spec.model_validate({"meta": {"name": "s"},
        "root": {"type": "sequence", "children": [
            {"type": "agent", "id": "a", "prompt": "p",
             "target": {"kind": "spawn", "profile": "w"}},
            {"type": "agent", "id": "b", "prompt": "p",
             "target": {"kind": "spawn", "profile": "w"}}]}})
    plan = build_plan(spec)
    assert plan.projected_agents == 2
    assert plan.is_upper_bound is False


def test_loop_uses_max_rounds_upper_bound():
    spec = Spec.model_validate({"meta": {"name": "s"},
        "root": {"type": "loop", "id": "r", "max_rounds": 4,
                 "until": {"kind": "shell", "cmd": "true"},
                 "body": {"type": "agent", "prompt": "p",
                          "target": {"kind": "spawn", "profile": "w"}}}})
    plan = build_plan(spec)
    assert plan.projected_agents == 4      # 1 body * max_rounds
    assert plan.is_upper_bound is True
    assert "upper bound" in plan.render()


def test_judge_predicate_counts_as_agent():
    spec = Spec.model_validate({"meta": {"name": "s"},
        "root": {"type": "if",
                 "cond": {"kind": "judge", "condition": "ok?", "inputs": []},
                 "then": {"type": "agent", "prompt": "p",
                          "target": {"kind": "spawn", "profile": "w"}}}})
    plan = build_plan(spec)
    assert plan.projected_agents == 2      # judge (1) + then agent (1)
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** `build_plan` (recursive count + line accumulation).
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** `feat(dsl): plan preview + projected agent count`.

### Task 6.2: Config — `dynamic_workflow_autoapprove_agents` threshold key

**Files:**
- Modify: `src/aegis/config/yaml_loader.py` (parse top-level key onto `AegisConfig`)
- Modify: `src/aegis/config/__init__.py` if `AegisConfig`/accessor is re-exported there
- Test: `tests/test_dsl_gate.py` (config-load portion) or an existing config test module

**Interfaces:**
- Produces: `AegisConfig.dynamic_workflow_autoapprove_agents: int = 5` (default per spec § Gating). Loaded from the `.aegis.yaml` top level; absent → default 5.

- [ ] **Step 1: Test** — loading a `.aegis.yaml` with `dynamic_workflow_autoapprove_agents: 3` yields `3`; absent yields `5`. **Step 2:** FAIL. **Step 3:** add the dataclass field + loader line. **Step 4:** PASS. **Step 5:** commit `feat(config): dynamic_workflow_autoapprove_agents threshold`.

### Task 6.3: Cost gate — decide prompt vs auto-approve

**Files:**
- Create: `src/aegis/dsl/gate.py`
- Test: `tests/test_dsl_gate.py`

**Interfaces:**
- Produces: `gate_decision(*, projected_agents: int, threshold: int, operator_invoked: bool) -> str` returning `"auto"` (run without prompting) or `"prompt"` (show plan preview, await operator). Rule (spec § Gating table): operator-invoked → always `"auto"`; agent-invoked → `"auto"` iff `projected_agents <= threshold`, else `"prompt"`. This is the pure decision; the actual operator prompt is delivered in Task 6.4 via `engine.ask_human`-style approval at the tool boundary.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dsl_gate.py
from aegis.dsl.gate import gate_decision


def test_operator_invoked_always_auto():
    assert gate_decision(projected_agents=100, threshold=5,
                         operator_invoked=True) == "auto"


def test_agent_under_threshold_auto():
    assert gate_decision(projected_agents=5, threshold=5,
                         operator_invoked=False) == "auto"


def test_agent_over_threshold_prompts():
    assert gate_decision(projected_agents=6, threshold=5,
                         operator_invoked=False) == "prompt"
```

- [ ] **Step 2: Run — FAIL. Step 3: implement (3-line function). Step 4: PASS. Step 5: commit** `feat(dsl): cost-gate decision (operator-implicit / agent-threshold)`.

### Task 6.4: MCP tool `aegis_run_dynamic_workflow` — validate → gate → launch

**Files:**
- Modify: `src/aegis/mcp/server.py` (new `@server.tool`; register in `BRIEFING`/`PRIMING` tool list circa `server.py:224`)
- Test: `tests/test_dsl_mcp.py`

**Interfaces:**
- Consumes: `Spec.model_validate` (structural), `validate(...)` (semantic), `build_plan`, `gate_decision`, `WorkflowRunner.start` (mirror `aegis_run_workflow`, `server.py:1055`), `find_project_root` + `load_config` (`server.py:512` pattern) for `agents`/`queues`/`default_agent`/threshold.
- Produces: `aegis_run_dynamic_workflow(spec: dict, kwargs: dict | None = None, from_handle: str = "", callback: bool = True) -> dict`. Flow: (1) `Spec.model_validate(spec)` — malformed → return the pydantic error dict for the model to retry (spec § Validation layer 1). (2) load config; `validate(model, agents=set(cfg.agents), queues=set(cfg.queues), default_agent=cfg.default_agent)` — semantic error → `{"error": ...}`. (3) `plan = build_plan(model, kwargs=kwargs)`; `operator_invoked = (from_handle == "")`; `decision = gate_decision(projected_agents=plan.projected_agents, threshold=cfg.dynamic_workflow_autoapprove_agents, operator_invoked=operator_invoked)`. (4) if `decision == "prompt"`: return `{"status": "gated", "plan": plan.render(), "projected_agents": plan.projected_agents}` (v1: the agent surfaces the plan to the operator; a fully-automatic in-tool operator prompt is a follow-on — see note). (5) else launch: `runner.start("dynamic", {"spec": spec, "kwargs": kwargs or {}, "default_profile": cfg.default_agent}, host=from_handle or None, workflow_id=run_id, state_dir=..., scheduler=..., done_callback=...)` — same wiring as `aegis_run_workflow`, tagging the callback `sender=f"workflow:{model.meta.name}"`. Return `{"workflow_id": run_id, "status": "running"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dsl_mcp.py
from __future__ import annotations

import pytest

# Build the FastMCP server against a FakeBridge with a real runner, call the
# tool function directly (mirror tests/test_workflow_mcp.py's approach).

async def test_run_dynamic_workflow_gated_above_threshold(dsl_mcp_env):
    tool = dsl_mcp_env.get_tool("aegis_run_dynamic_workflow")
    spec = {"meta": {"name": "big"},
            "root": {"type": "loop", "id": "r", "max_rounds": 50,
                     "until": {"kind": "shell", "cmd": "true"},
                     "body": {"type": "agent", "prompt": "p",
                              "target": {"kind": "spawn", "profile": "w"}}}}
    res = await tool(spec=spec, from_handle="agent-7")  # agent-invoked
    assert res["status"] == "gated"
    assert res["projected_agents"] == 50
    assert "upper bound" in res["plan"]


async def test_run_dynamic_workflow_autoapprove_and_launch(dsl_mcp_env):
    tool = dsl_mcp_env.get_tool("aegis_run_dynamic_workflow")
    spec = {"meta": {"name": "small"},
            "root": {"type": "agent", "id": "a", "prompt": "p",
                     "target": {"kind": "spawn", "profile": "w"}}}
    res = await tool(spec=spec, from_handle="agent-7")
    assert res["status"] == "running"
    assert "workflow_id" in res


async def test_malformed_spec_returns_validation_error(dsl_mcp_env):
    tool = dsl_mcp_env.get_tool("aegis_run_dynamic_workflow")
    res = await tool(spec={"meta": {"name": "x"},
                           "root": {"type": "frobnicate"}}, from_handle="a")
    assert "error" in res
```

The `dsl_mcp_env` fixture builds the server with a `FakeBridge` whose config exposes `agents={"w"}`, `queues=set()`, `default_agent="w"`, `dynamic_workflow_autoapprove_agents=5`. Model it on the existing `tests/test_workflow_mcp.py` server-construction fixture; stub `find_project_root`/`load_config` via monkeypatch so the tool reads the fake config hermetically.

- [ ] **Step 2: Run — FAIL** (tool not registered).
- [ ] **Step 3: Implement** the tool per the interface; add its one-line description to the `BRIEFING` tool list and the `PRIMING` note (mirror the `aegis_run_workflow` entries at `server.py:224`/`285`).
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** `feat(mcp): aegis_run_dynamic_workflow — validate, gate, launch`.

### Task 6.5: Live smoke test (marked `live`)

**Files:**
- Create: `tests/test_dsl_live.py` (`@pytest.mark.live`)
- Test: itself

**Interfaces:**
- Consumes: a real `claude` subprocess via the same discipline as `tests/test_workflow_live.py` (auto-skip when `claude` is off PATH).
- Produces: a small real fan-out — a 2-file `map` audit + agent reduction — run end-to-end through `aegis_run_dynamic_workflow` against a real spawn; assert the run reaches `status == "ok"` and the reduction output is non-empty.

- [ ] **Step 1: Write the live test** (skips without `claude`). **Step 2: Run** `uv run python -m pytest tests/test_dsl_live.py -q` (skips locally if no `claude`; runs in the live lane). **Step 3:** iterate until green where `claude` is present. **Step 4: Commit** `test(dsl): live fan-out smoke test`.

---

## Open decisions (surface to the operator — do NOT silently resolve)

Per the spec's § Open questions, two items are explicit decision points. Record the chosen default in the PR description; do not change the design without a decision.

1. **`equals` predicate — deferred (NOT built in v1).** The design-thinking gate expresses "the operator chose `proceed`" as a `judge` predicate (an agent call) rather than a comparison, because selectors *select but never compute*. A third predicate kind `{"kind": "equals", "left": <selector>, "right": <literal>}` would make that check deterministic and free, but it is "the first crack toward an expression language." **v1 ships judge-only**; the models (Task 4.1) leave the `AnyPredicate` union open to a third member so adding `equals` later is additive. Flag in the PR: *do we accept spending one agent call per gate check in v1, or fast-follow `equals`?*
2. **Plan-preview cost estimate is a static upper bound.** `build_plan` (Task 6.1) sets `is_upper_bound=True` for any `loop` (uses `max_rounds`) or `map` (list length unknown pre-run) and `render()` labels it "(upper bound)". This is exact only for fully-static shapes. **v1 ships the labelled upper bound**; refine only if operators find it misleading (spec § Open questions). No token-cost dollar figure in v1 — `projected_agents` is the gate signal; a spend estimate is reachable-later.

---

## Self-review checklist (run before handoff)

- [ ] **Spec coverage:** every node (`sequence`/`parallel`/`map`/`loop`/`if`/`agent`/`human`), both predicates (`shell`/`judge`), both validation layers, durability/resume, the gate + threshold, and the MCP tool each map to a task. Non-goals are excluded.
- [ ] **Placeholder scan:** no TBD/TODO; every code step shows real code; test steps show real assertions.
- [ ] **Type consistency:** `schema_`/`else_` aliases used consistently; `Store.snapshot()`/`load()`, `resolve_selector`, `substitute`, `validate(spec, *, agents, queues, default_agent)`, `build_plan(spec, *, kwargs)`, `gate_decision(...)`, and the `dynamic(engine, *, spec, kwargs, default_profile)` signature are identical across all tasks that reference them.
- [ ] **Grounded symbols:** `WorkflowEngine.spawn/send/close/bash/parallel/ask_human/checkpoint/resume_state`, `WorkflowRunner.start/resume/status`, `@workflow`, `FakeBridge` methods, and the `aegis_run_workflow` registration pattern all exist on `main` (verified during grounding).
</content>
</invoke>
