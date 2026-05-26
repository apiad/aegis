# Per-Queue Token / USD Budgets Implementation Plan (v2 — rewritten 2026-05-26)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Tasks 1 + 2 already landed on `main`. Resume at Task 3.

**Goal:** Ship per-queue token/USD budgets with multi-window all-must-allow enforcement at enqueue time, plus MCP / HTTP / CLI inspection surfaces. TUI is explicitly scope-cut to v0.9.1.

**Architecture:** Pure-function evaluator over the existing per-queue JSONL audit, gated at `QueueManager.enqueue`. Each queue declares one or more `(constraint, window)` pairs (USD or output-token, rolling window). On worker termination, `QueueManager._finalize` writes a `cost` field on the existing `completed` / `failed` JSONL record via `cost.compute(adapter(session.metrics), q.provider, q.model)` where `q.provider` and `q.model` are derived once at config-load from the bound agent profile. No new persistent state; no Telegram observer.

**Tech Stack:** Python 3.13, `Decimal` arithmetic throughout (no float drift), pytest with `uv run pytest -q -m "not live" -x`, Typer (CLI), Starlette (plane endpoints).

**Spec:** `docs/superpowers/specs/2026-05-25-aegis-per-queue-budgets-design.md` (canonical). Read it once before starting Task 3.

**Plan-vs-reality lessons from v1:** the first attempt blocker-stopped at Task 3 because the plan referenced symbol names that didn't exist on `main`. The v2 rewrite uses verified-against-`main` symbols. Resolutions to the three blocker design questions:

- **Q1 (provider/model wiring) → Option D:** `Queue` itself gains `provider: str` and `model: str` fields, derived once at config-load from the bound `agent_profile`'s `Agent.harness` / `Agent.model`. `_finalize` reads `self._queues[task.queue].provider` / `.model`. No `Task` schema mutation, no runtime agent dict lookup.
- **Q2 (JSONL event name) → Option C:** keep `completed`/`failed` (real codebase). Evaluator filters on `event in ("completed", "failed")`. Failed workers count toward budget — they burn tokens too.
- **Q3 (SessionMetrics) → adapter only:** map `c_in → input_tokens`, `c_out → output_tokens`, `c_cached → cache_hit_tokens` in `_finalize`. `cache_write_tokens` / `thinking_tokens` stay `0` (no driver surfaces them today; YAGNI). `cost.compute`'s defensive `getattr(..., 0)` handles missing fields.

**Mechanical corrections threaded throughout:**

- `Queue(name=..., agent_profile=..., max_parallel=...)` — `agent_profile`, not `agent`.
- `QueueManager(queues, session_manager, inbox_router, *, state_dir=...)` — pass `InboxRouter()` in tests.
- `StubSessionManager` is the existing fixture pattern in `tests/test_queue_manager.py` — copy its shape (with `.script(handle, [AssistantText, Result])`) when a test needs to drive a worker to completion. Do NOT invent a `FakeSessionManager`.
- JSONL path is `state_dir/queues/<queue>.jsonl` — no `.aegis/state/` prefix. Tests pass `state_dir=tmp_path` directly.
- `.aegis.py` config uses `"agent": "<profile-name>"` (not `agent_profile`) — the loader maps the dict key to the dataclass field.

**Conventions:**

- Hermetic gate before every commit: `uv run pytest -q -m "not live" -x`.
- Aegis convention: commit straight to `main`, no feature branches, no PRs (workspace memory `feedback_aegis_work_on_main`).
- Use uv: `uv run pytest`, `uv pip install -e .`.

---

## Task 1: ✓ ALREADY LANDED — Price table + window parser

Commit `0cb4fd4` shipped `src/aegis/budget/__init__.py`, `src/aegis/budget/prices.py`, `src/aegis/budget/windows.py`, `tests/test_budget_prices.py`, `tests/test_budget_windows.py`. Do not redo.

## Task 2: ✓ ALREADY LANDED — Cost dataclass + compute()

Commit `c1d4db8` shipped `src/aegis/budget/cost.py` and `tests/test_budget_cost.py` with the `Cost` dataclass + `compute(metrics, provider, model) -> Cost` (defensive `getattr(..., 0)` for missing token-class fields). Do not redo.

---

## Task 3: Queue grows `provider`, `model` fields populated at config-load

**Files:**
- Modify: `src/aegis/queue/schema.py` (Queue dataclass)
- Modify: `src/aegis/config/__init__.py` (`load_queues` resolves provider/model from the agent profile)
- Modify: `tests/test_queue_manager.py` (the `_q` helper grows optional `provider`/`model` kwargs)
- Test: `tests/test_queue_provider_model.py`

Prereq for Task 4 (cost on JSONL). The cost-compute path needs `(provider, model)` per queue; resolving at config-load avoids a runtime agent lookup.

- [ ] **Step 1: Write the failing test**

Create `tests/test_queue_provider_model.py`:

```python
from pathlib import Path

import pytest

from aegis.queue.schema import Queue


def test_queue_dataclass_carries_provider_and_model():
    q = Queue(name="impl", agent_profile="opus", max_parallel=2,
              provider="claude-code", model="opus")
    assert q.provider == "claude-code"
    assert q.model == "opus"


def test_queue_defaults_provider_and_model_to_empty():
    q = Queue(name="impl", agent_profile="opus", max_parallel=2)
    assert q.provider == ""
    assert q.model == ""


def test_load_queues_derives_provider_and_model_from_agent(tmp_path):
    """End-to-end: an .aegis.py with queues:{...} populates Queue.provider
    and Queue.model from the bound agent profile."""
    from aegis.config import load_queues
    aegis_py = tmp_path / ".aegis.py"
    aegis_py.write_text("""
from aegis import Agent, ClaudeCode
agents = {
    "opus":  Agent(provider=ClaudeCode(model="opus",  effort="high")),
    "haiku": Agent(provider=ClaudeCode(model="haiku", effort="low")),
}
default_agent = "opus"
queues = {
    "impl":     {"agent": "opus",  "max_parallel": 2},
    "fast":     {"agent": "haiku", "max_parallel": 4},
}
""")
    queues = load_queues(aegis_py)
    assert queues["impl"].provider == "claude-code"
    assert queues["impl"].model == "opus"
    assert queues["fast"].provider == "claude-code"
    assert queues["fast"].model == "haiku"


def test_load_queues_gemini_provider(tmp_path):
    from aegis.config import load_queues
    aegis_py = tmp_path / ".aegis.py"
    aegis_py.write_text("""
from aegis import Agent, GeminiCLI
agents = {"g": Agent(provider=GeminiCLI(model="gemini-3-pro"))}
default_agent = "g"
queues = {"r": {"agent": "g", "max_parallel": 1}}
""")
    queues = load_queues(aegis_py)
    assert queues["r"].provider == "gemini"
    assert queues["r"].model == "gemini-3-pro"
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/test_queue_provider_model.py -v
```
Expected: FAIL — `Queue(...)` rejects `provider=` / `model=` (those fields don't exist yet).

- [ ] **Step 3: Extend `Queue` dataclass**

In `src/aegis/queue/schema.py`:

```python
@dataclass(frozen=True)
class Queue:
    name: str
    agent_profile: str
    max_parallel: int
    provider: str = ""   # populated from agent_profile at config-load
    model: str = ""      # populated from agent_profile at config-load
```

- [ ] **Step 4: Update `load_queues` to resolve provider/model**

In `src/aegis/config/__init__.py`, change `load_queues` so it keeps the `agents` dict around (instead of only its keys) and looks up `Agent.harness` + `Agent.model`:

```python
def load_queues(path: Path) -> "dict[str, object]":
    """Parse the ``queues`` dict from a .aegis.py file.

    Each queue's bound agent is resolved at load time; Queue.provider
    and Queue.model are populated from the Agent's harness/model so
    the cost-compute path can look them up without chasing the agents
    dict at runtime.
    """
    from aegis.queue import Queue

    namespace: dict[str, object] = {}
    try:
        exec(compile(path.read_text(), str(path), "exec"),  # noqa: S102
             namespace)
    except Exception as e:  # noqa: BLE001
        raise ConfigError(f"Failed to load {path}: {e}") from e

    queues_raw = namespace.get("queues")
    if queues_raw is None:
        return {}
    if not isinstance(queues_raw, dict):
        raise ConfigError(f"{path}: `queues` must be a dict.")

    agents_raw = namespace.get("agents")
    agents: dict[str, Agent] = (
        dict(agents_raw) if isinstance(agents_raw, dict) else {})
    agent_names: set[str] = set(agents)

    out: dict[str, Queue] = {}
    for name, cfg in queues_raw.items():
        if not isinstance(cfg, dict):
            raise ConfigError(
                f"{path}: queues[{name!r}] must be a dict.")
        if "agent" not in cfg:
            raise ConfigError(
                f"{path}: queues[{name!r}] missing required key 'agent'.")
        if "max_parallel" not in cfg:
            raise ConfigError(
                f"{path}: queues[{name!r}] missing required key "
                f"'max_parallel'.")
        agent_ref = cfg["agent"]
        cap = cfg["max_parallel"]
        if agent_ref not in agent_names:
            raise ConfigError(
                f"{path}: queues[{name!r}].agent={agent_ref!r} does not "
                f"reference a declared agent profile "
                f"(known: {sorted(agent_names)}).")
        if not isinstance(cap, int) or cap < 1:
            raise ConfigError(
                f"{path}: queues[{name!r}].max_parallel must be an int "
                f">= 1 (got {cap!r}).")
        agent = agents[agent_ref]
        out[name] = Queue(name=name, agent_profile=agent_ref,
                          max_parallel=cap,
                          provider=agent.harness, model=agent.model)
    return out
```

(The `Agent` validator already populates `harness` and `model` whether the user gave the new `provider=` shape or the legacy flat one.)

- [ ] **Step 5: Update the `_q` test helper in `tests/test_queue_manager.py`**

Find the existing `def _q(...)` (line ~88) and extend:

```python
def _q(name="impl", profile="claude-impl", cap=2,
       provider="", model=""):
    return Queue(name=name, agent_profile=profile, max_parallel=cap,
                 provider=provider, model=model)
```

Existing callers passing no extra args continue to work (defaults are empty strings).

- [ ] **Step 6: Run tests**

```
uv run pytest tests/test_queue_provider_model.py tests/test_queue_manager.py -v
```
Expected: PASS.

- [ ] **Step 7: Run full hermetic suite + commit**

```bash
uv run pytest -q -m "not live" -x
git add src/aegis/queue/schema.py src/aegis/config/__init__.py \
        tests/test_queue_manager.py tests/test_queue_provider_model.py
git commit -m "feat(queue): Queue gains provider/model derived from agent profile at config-load"
```

---

## Task 4: `_finalize` writes `cost` on the JSONL record

**Files:**
- Modify: `src/aegis/queue/manager.py` (`_finalize`, adapter)
- Test: `tests/test_queue_cost_log.py`

Plumb cost computation into `_finalize`. Adapter maps `SessionMetrics.c_in/c_out/c_cached` to `compute()`'s expected names. Provider/model come from `self._queues[task.queue]`. Unknown model → `cost: {"error": "unknown_model"}` instead of crashing.

- [ ] **Step 1: Write failing test**

Create `tests/test_queue_cost_log.py`:

```python
import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from aegis.events import Result, TokenUsage
from aegis.queue import InboxRouter, Queue, QueueManager, sender_agent

# Reuse the existing test_queue_manager helpers (AssistantText, StubSessionManager).
from tests.test_queue_manager import StubSessionManager, AssistantText


@pytest.mark.asyncio
async def test_completed_record_includes_cost(tmp_path):
    """When a worker completes, the JSONL `completed` record carries a
    `cost` field computed from session.metrics + queue's (provider, model)."""
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager(
        {"impl": Queue(name="impl", agent_profile="opus", max_parallel=1,
                        provider="claude-code", model="opus")},
        sm, inbox, state_dir=tmp_path,
    )
    # Drive the worker: script its events to include a Result with usage.
    # Then enqueue + let the substrate finalize it.
    usage = TokenUsage(true_input=10_000, output=5_000, cache_read=0,
                       cache_creation=0)
    sm.script("worker-handle-0",
              [AssistantText(text="DONE"),
               Result(duration_ms=1, is_error=False, usage=usage)])
    tid, _ = qm.enqueue("impl", "x",
                         enqueued_by=sender_agent("p"), callback=False)
    # Let the event loop drive the session to completion.
    await asyncio.sleep(0.1)

    log = tmp_path / "queues" / "impl.jsonl"
    assert log.exists()
    records = [json.loads(l) for l in log.read_text().splitlines()
               if l.strip()]
    done = [r for r in records if r.get("event") in ("completed", "failed")]
    assert len(done) == 1
    assert done[0]["event"] == "completed"
    assert "cost" in done[0]
    c = done[0]["cost"]
    # opus rates: in=15/M, out=75/M, cache_hit=1.50/M
    # cost = 10_000*15/1M + 5_000*75/1M + 0 = 0.15 + 0.375 = 0.525
    assert Decimal(c["usd"]) == Decimal("0.525")
    assert c["input_tokens"] == 10_000
    assert c["output_tokens"] == 5_000


@pytest.mark.asyncio
async def test_unknown_model_records_error_instead_of_crashing(tmp_path):
    """Queue.provider/.model not in PRICES → cost: {error: unknown_model}.
    Finalizer still completes; the budget evaluator treats this as $0."""
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager(
        {"impl": Queue(name="impl", agent_profile="x", max_parallel=1,
                        provider="madeup", model="zzz")},
        sm, inbox, state_dir=tmp_path,
    )
    usage = TokenUsage(true_input=1, output=1, cache_read=0,
                       cache_creation=0)
    sm.script("worker-handle-0",
              [AssistantText(text="DONE"),
               Result(duration_ms=1, is_error=False, usage=usage)])
    qm.enqueue("impl", "x", enqueued_by=sender_agent("p"), callback=False)
    await asyncio.sleep(0.1)

    log = tmp_path / "queues" / "impl.jsonl"
    records = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    done = [r for r in records if r.get("event") in ("completed", "failed")]
    assert len(done) == 1
    assert done[0]["cost"].get("error") == "unknown_model"


@pytest.mark.asyncio
async def test_failed_record_also_carries_cost(tmp_path):
    """Failed workers consumed tokens too — record cost on the failed
    record so the budget evaluator counts them."""
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager(
        {"impl": Queue(name="impl", agent_profile="opus", max_parallel=1,
                        provider="claude-code", model="opus")},
        sm, inbox, state_dir=tmp_path,
    )
    # Script the worker to fail mid-flight: emit a Result with is_error=True.
    usage = TokenUsage(true_input=1_000, output=500, cache_read=0,
                       cache_creation=0)
    sm.script("worker-handle-0",
              [AssistantText(text="oops"),
               Result(duration_ms=1, is_error=True, usage=usage)])
    qm.enqueue("impl", "x", enqueued_by=sender_agent("p"), callback=False)
    await asyncio.sleep(0.1)

    log = tmp_path / "queues" / "impl.jsonl"
    records = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    done = [r for r in records if r.get("event") in ("completed", "failed")]
    assert len(done) == 1
    assert done[0]["event"] == "failed"
    assert "cost" in done[0]
    # Failed workers still charged: 1000*15/1M + 500*75/1M = 0.015 + 0.0375
    assert Decimal(done[0]["cost"]["usd"]) > Decimal("0")
```

(Adjust the `worker-handle-0` literal if `QueueManager` assigns a different handle pattern — check via `grep -nE "handle.*=.*worker|generate_handle" src/aegis/queue/manager.py` or just print the actual handle inside the StubSessionManager fixture.)

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/test_queue_cost_log.py -v
```
Expected: FAIL — `cost` key missing from `completed`/`failed` records.

- [ ] **Step 3: Implement the adapter + extend `_finalize`**

In `src/aegis/queue/manager.py`, at the top, import the cost machinery:

```python
from aegis.budget.cost import compute as _compute_cost
from aegis.budget.prices import UnknownPriceError


def _adapt_metrics(metrics):
    """Map SessionMetrics committed counters to cost.compute's expected
    attribute names. Returns a lightweight object — duck-typed."""
    class _M:
        input_tokens     = int(getattr(metrics, "c_in", 0) or 0)
        output_tokens    = int(getattr(metrics, "c_out", 0) or 0)
        cache_hit_tokens = int(getattr(metrics, "c_cached", 0) or 0)
        cache_write_tokens = 0
        thinking_tokens  = 0
    return _M
```

Then in `_finalize`, after computing `status`, add:

```python
q = self._queues[task.queue]
try:
    cost_dict = _compute_cost(
        _adapt_metrics(session.metrics),
        provider=q.provider, model=q.model,
    ).as_dict()
except UnknownPriceError as e:
    cost_dict = {"error": "unknown_model", "detail": str(e)}
except Exception as e:  # don't let cost compute break the finalizer
    cost_dict = {"error": "compute_failed", "detail": str(e)}
```

And extend the `self._log(task.queue, {...})` call to include the new field:

```python
self._log(task.queue, {
    "event": status, "task_id": task.id,
    "result": result, "error": error,
    "completed_at": completed.completed_at,
    "cost": cost_dict,
})
```

- [ ] **Step 4: Run tests**

```
uv run pytest tests/test_queue_cost_log.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 5: Run full hermetic suite + commit**

```bash
uv run pytest -q -m "not live" -x
git add src/aegis/queue/manager.py tests/test_queue_cost_log.py
git commit -m "feat(budget): _finalize writes cost field on completed/failed JSONL records"
```

---

## Task 5: `Budget` dataclass + `parse_budgets` + `Queue.budgets` field

**Files:**
- Create: `src/aegis/budget/budgets.py` (`Budget`, `BudgetConfigError`, `parse_budgets`)
- Modify: `src/aegis/queue/schema.py` (Queue grows `budgets: list[Budget]`)
- Modify: `src/aegis/config/__init__.py` (`load_queues` parses + attaches budgets)
- Test: `tests/test_budget_config.py`

Pre-budget data model. The evaluator (Task 6) consumes `Budget` objects; the parser validates at config-load.

- [ ] **Step 1: Write failing test**

Create `tests/test_budget_config.py`:

```python
from decimal import Decimal
from pathlib import Path

import pytest

from aegis.budget.budgets import Budget, BudgetConfigError, parse_budgets


def test_parse_single_usd_budget():
    b = parse_budgets([{"usd": 1.00, "window": "1h"}])
    assert len(b) == 1
    assert b[0].constraint == "usd"
    assert b[0].limit == Decimal("1.00")
    assert b[0].window_str == "1h"


def test_parse_output_tokens_budget():
    b = parse_budgets([{"output_tokens": 500_000, "window": "1h"}])
    assert b[0].constraint == "output_tokens"
    assert b[0].limit == Decimal("500000")


def test_parse_multiple_preserves_order():
    b = parse_budgets([
        {"usd": 1.00, "window": "1h"},
        {"usd": 10.00, "window": "24h"},
        {"output_tokens": 500_000, "window": "1h"},
    ])
    assert [x.window_str for x in b] == ["1h", "24h", "1h"]


def test_parse_rejects_both_constraints():
    with pytest.raises(BudgetConfigError, match="exactly one"):
        parse_budgets([{"usd": 1.00, "output_tokens": 500, "window": "1h"}])


def test_parse_rejects_neither_constraint():
    with pytest.raises(BudgetConfigError, match="exactly one"):
        parse_budgets([{"window": "1h"}])


def test_parse_rejects_missing_window():
    with pytest.raises(BudgetConfigError, match="window"):
        parse_budgets([{"usd": 1.00}])


def test_parse_rejects_bad_window():
    with pytest.raises(BudgetConfigError, match="window"):
        parse_budgets([{"usd": 1.00, "window": "5y"}])


def test_parse_rejects_duplicate_pair():
    with pytest.raises(BudgetConfigError, match="duplicate"):
        parse_budgets([
            {"usd": 1.00, "window": "1h"},
            {"usd": 2.00, "window": "1h"},
        ])


def test_parse_rejects_non_positive():
    with pytest.raises(BudgetConfigError, match="positive"):
        parse_budgets([{"usd": 0, "window": "1h"}])
    with pytest.raises(BudgetConfigError, match="positive"):
        parse_budgets([{"output_tokens": -1, "window": "1h"}])


def test_parse_empty_list_returns_empty():
    assert parse_budgets([]) == []
    assert parse_budgets(None) == []


def test_load_queues_attaches_budgets(tmp_path):
    """End-to-end: budgets land on Queue.budgets via load_queues."""
    from aegis.config import load_queues
    aegis_py = tmp_path / ".aegis.py"
    aegis_py.write_text("""
from aegis import Agent, ClaudeCode
agents = {"opus": Agent(provider=ClaudeCode(model="opus"))}
default_agent = "opus"
queues = {
    "impl": {
        "agent": "opus", "max_parallel": 2,
        "budgets": [
            {"usd": 1.00, "window": "1h"},
            {"output_tokens": 500_000, "window": "1h"},
        ],
    },
}
""")
    queues = load_queues(aegis_py)
    assert len(queues["impl"].budgets) == 2
    assert queues["impl"].budgets[0].constraint == "usd"


def test_load_queues_with_bad_budget_fails(tmp_path):
    from aegis.config import ConfigError, load_queues
    aegis_py = tmp_path / ".aegis.py"
    aegis_py.write_text("""
from aegis import Agent, ClaudeCode
agents = {"opus": Agent(provider=ClaudeCode(model="opus"))}
default_agent = "opus"
queues = {
    "impl": {
        "agent": "opus", "max_parallel": 1,
        "budgets": [{"usd": 1.00, "output_tokens": 500, "window": "1h"}],
    },
}
""")
    with pytest.raises((BudgetConfigError, ConfigError), match="impl"):
        load_queues(aegis_py)
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/test_budget_config.py -v
```
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `parse_budgets`**

Create `src/aegis/budget/budgets.py`:

```python
"""Budget dataclass + config-time parser/validator."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from aegis.budget.windows import parse_window


class BudgetConfigError(ValueError):
    """Raised when a queue's `budgets:` config is malformed."""


@dataclass(frozen=True)
class Budget:
    constraint: str         # "usd" or "output_tokens"
    limit:      Decimal
    window_str: str         # verbatim from config
    window:     timedelta   # parsed


def parse_budgets(raw) -> list[Budget]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise BudgetConfigError(
            f"budgets must be a list, got {type(raw).__name__}")

    out: list[Budget] = []
    seen: set[tuple[str, str]] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise BudgetConfigError(
                f"budgets[{i}] must be a dict, got {type(entry).__name__}")
        has_usd = "usd" in entry
        has_tok = "output_tokens" in entry
        if has_usd == has_tok:
            raise BudgetConfigError(
                f"budgets[{i}] must have exactly one of 'usd' or "
                f"'output_tokens' (got both or neither)")
        if "window" not in entry:
            raise BudgetConfigError(f"budgets[{i}] missing 'window'")
        try:
            window = parse_window(entry["window"])
        except ValueError as e:
            raise BudgetConfigError(f"budgets[{i}] window: {e}")
        if has_usd:
            constraint = "usd"
            raw_limit = entry["usd"]
        else:
            constraint = "output_tokens"
            raw_limit = entry["output_tokens"]
        try:
            limit = Decimal(str(raw_limit))
        except (InvalidOperation, ValueError):
            raise BudgetConfigError(
                f"budgets[{i}] {constraint} must be numeric, "
                f"got {raw_limit!r}")
        if limit <= 0:
            raise BudgetConfigError(
                f"budgets[{i}] {constraint} must be positive, got {limit}")
        key = (constraint, entry["window"])
        if key in seen:
            raise BudgetConfigError(
                f"budgets[{i}] duplicate ({constraint!r}, "
                f"{entry['window']!r})")
        seen.add(key)
        out.append(Budget(constraint=constraint, limit=limit,
                          window_str=entry["window"], window=window))
    return out
```

- [ ] **Step 4: Extend `Queue` with `budgets`**

In `src/aegis/queue/schema.py`:

```python
from dataclasses import dataclass, field

from aegis.budget.budgets import Budget   # forward-import safe; no cycle

@dataclass(frozen=True)
class Queue:
    name: str
    agent_profile: str
    max_parallel: int
    provider: str = ""
    model: str = ""
    budgets: list[Budget] = field(default_factory=list)
```

If `aegis.queue.schema` is imported by `aegis.budget.budgets` (it isn't currently — check), use a deferred import to break a cycle. Most likely no cycle exists since `budgets.py` only imports `windows.py`.

- [ ] **Step 5: Wire `load_queues` to parse + attach**

In `src/aegis/config/__init__.py`, update `load_queues`'s queue-construction loop:

```python
from aegis.budget.budgets import parse_budgets, BudgetConfigError

# ...inside the for-each-queue loop, after the validation block:
try:
    budgets = parse_budgets(cfg.get("budgets"))
except BudgetConfigError as e:
    raise ConfigError(f"{path}: queues[{name!r}].budgets: {e}")

out[name] = Queue(name=name, agent_profile=agent_ref,
                  max_parallel=cap,
                  provider=agent.harness, model=agent.model,
                  budgets=budgets)
```

- [ ] **Step 6: Run tests**

```
uv run pytest tests/test_budget_config.py -v
```
Expected: PASS.

- [ ] **Step 7: Run full hermetic suite + commit**

```bash
uv run pytest -q -m "not live" -x
git add src/aegis/budget/budgets.py src/aegis/queue/schema.py \
        src/aegis/config/__init__.py tests/test_budget_config.py
git commit -m "feat(budget): Budget dataclass + Queue.budgets via load_queues parser"
```

---

## Task 6: `evaluate_budgets()` pure function

**Files:**
- Create: `src/aegis/budget/evaluator.py`
- Test: `tests/test_budget_evaluator.py`

Pure-function evaluator over a list of JSONL records. Filters on `event in ("completed", "failed")` (not the imaginary `task_done`). Returns `Decision` with per-budget `BudgetCheck` rows.

- [ ] **Step 1: Write failing test**

Create `tests/test_budget_evaluator.py`:

```python
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from aegis.budget.budgets import Budget
from aegis.budget.evaluator import BudgetCheck, Decision, evaluate_budgets
from aegis.budget.windows import parse_window


def _now() -> datetime:
    return datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)


def _budget(constraint: str, limit: str, window_str: str) -> Budget:
    return Budget(constraint=constraint, limit=Decimal(limit),
                  window_str=window_str, window=parse_window(window_str))


def _rec(ts: datetime, event: str = "completed",
         usd: str = "0", output_tokens: int = 0) -> dict:
    return {
        "event": event,
        "completed_at": ts.isoformat().replace("+00:00", "Z"),
        "cost": {"usd": usd, "input_tokens": 0,
                  "output_tokens": output_tokens,
                  "cache_hit_tokens": 0, "cache_write_tokens": 0,
                  "thinking_tokens": 0},
    }


def test_no_budgets_allows():
    d = evaluate_budgets([], [], _now())
    assert d.allowed is True
    assert d.blocked_by == []


def test_completed_record_counts():
    n = _now()
    tail = [_rec(n - timedelta(minutes=10), event="completed", usd="0.50")]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is True
    assert d.checks[0].spent == Decimal("0.50")


def test_failed_record_also_counts():
    """Failed workers burned tokens — count them."""
    n = _now()
    tail = [_rec(n - timedelta(minutes=10), event="failed", usd="0.80")]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.checks[0].spent == Decimal("0.80")


def test_under_limit_allows():
    n = _now()
    tail = [_rec(n - timedelta(minutes=10), usd="0.50")]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is True
    assert d.checks[0].headroom == Decimal("0.50")


def test_over_limit_blocks():
    n = _now()
    tail = [_rec(n - timedelta(minutes=10), usd="0.80"),
            _rec(n - timedelta(minutes=20), usd="0.50")]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is False
    assert d.blocked_by[0].spent == Decimal("1.30")


def test_records_outside_window_ignored():
    n = _now()
    tail = [_rec(n - timedelta(minutes=30), usd="0.50"),
            _rec(n - timedelta(hours=2), usd="100.00")]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is True


def test_multi_budget_partial_block():
    n = _now()
    tail = [_rec(n - timedelta(minutes=10), usd="0.80"),
            _rec(n - timedelta(minutes=20), usd="0.50")]
    d = evaluate_budgets(tail, [
        _budget("usd", "1.00", "1h"),    # blocks
        _budget("usd", "10.00", "24h"),  # ok
    ], n)
    assert d.allowed is False
    assert len(d.blocked_by) == 1
    assert d.blocked_by[0].window_str == "1h"


def test_output_tokens_budget():
    n = _now()
    tail = [_rec(n - timedelta(minutes=5), output_tokens=600_000)]
    d = evaluate_budgets(tail, [_budget("output_tokens", "500000", "1h")], n)
    assert d.allowed is False
    assert d.blocked_by[0].spent == Decimal("600000")


def test_unblock_at_for_blocking_budget():
    n = _now()
    older = n - timedelta(minutes=30)
    newer = n - timedelta(minutes=10)
    tail = [_rec(newer, usd="0.80"), _rec(older, usd="0.50")]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is False
    # Older ages out first; remaining 0.80 < 1.00 → allowed.
    assert d.blocked_by[0].unblock_at == older + timedelta(hours=1)


def test_decision_unblock_at_is_max():
    n = _now()
    tail = [_rec(n - timedelta(minutes=10), usd="2.00",
                  output_tokens=600_000)]
    d = evaluate_budgets(tail, [
        _budget("usd", "1.00", "1h"),
        _budget("output_tokens", "500000", "30m"),
    ], n)
    assert d.allowed is False
    times = [c.unblock_at for c in d.blocked_by if c.unblock_at]
    assert d.unblock_at == max(times)


def test_record_without_cost_counts_as_zero():
    """Backwards compat for pre-budget records."""
    n = _now()
    tail = [
        {"event": "completed",
         "completed_at": (n - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")},
        _rec(n - timedelta(minutes=10), usd="0.30"),
    ]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is True
    assert d.checks[0].spent == Decimal("0.30")


def test_non_terminal_events_ignored():
    n = _now()
    tail = [
        {"event": "task_enqueued",
         "completed_at": (n - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
         "cost": {"usd": "100.00"}},
        _rec(n - timedelta(minutes=10), usd="0.30"),
    ]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.checks[0].spent == Decimal("0.30")
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/test_budget_evaluator.py -v
```
Expected: FAIL — module missing.

- [ ] **Step 3: Implement evaluator**

Create `src/aegis/budget/evaluator.py`:

```python
"""Pure-function evaluator for per-queue budgets over a JSONL tail."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from aegis.budget.budgets import Budget

# JSONL events that count toward budget. The substrate writes either
# "completed" or "failed" on terminal transition; both consumed tokens
# so both contribute to spent.
_TERMINAL_EVENTS = ("completed", "failed")


@dataclass(frozen=True)
class BudgetCheck:
    constraint:    str
    limit:         Decimal
    spent:         Decimal
    window_str:    str
    window_start:  datetime
    allowed:       bool
    headroom:      Decimal
    unblock_at:    datetime | None


@dataclass(frozen=True)
class Decision:
    allowed:    bool
    checks:     list[BudgetCheck]
    blocked_by: list[BudgetCheck]
    unblock_at: datetime | None


_ZERO = Decimal("0")


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _record_value(rec: dict, constraint: str) -> Decimal:
    cost = rec.get("cost") or {}
    if "error" in cost:
        return _ZERO
    if constraint == "usd":
        try:
            return Decimal(cost.get("usd", "0"))
        except Exception:
            return _ZERO
    if constraint == "output_tokens":
        try:
            return Decimal(int(cost.get("output_tokens", 0) or 0))
        except (ValueError, TypeError):
            return _ZERO
    return _ZERO


def _evaluate_one(records: list[dict], budget: Budget,
                  now: datetime) -> BudgetCheck:
    window_start = now - budget.window
    inside: list[tuple[datetime, Decimal]] = []
    for rec in records:
        if rec.get("event") not in _TERMINAL_EVENTS:
            continue
        ts = _parse_iso(rec.get("completed_at"))
        if ts is None or ts <= window_start or ts > now:
            continue
        inside.append((ts, _record_value(rec, budget.constraint)))
    inside.sort(key=lambda p: p[0])
    spent = sum((v for _, v in inside), start=_ZERO)
    allowed = spent < budget.limit
    headroom = budget.limit - spent

    unblock_at: datetime | None = None
    if not allowed:
        running = spent
        for ts, value in inside:
            running -= value
            if running < budget.limit:
                unblock_at = ts + budget.window
                break

    return BudgetCheck(
        constraint=budget.constraint, limit=budget.limit, spent=spent,
        window_str=budget.window_str, window_start=window_start,
        allowed=allowed, headroom=headroom, unblock_at=unblock_at,
    )


def evaluate_budgets(jsonl_tail: Iterable[dict],
                     budgets: list[Budget],
                     now: datetime) -> Decision:
    records = list(jsonl_tail)
    checks = [_evaluate_one(records, b, now) for b in budgets]
    blocked_by = [c for c in checks if not c.allowed]
    decision_unblock: datetime | None = None
    if blocked_by:
        eligible = [c.unblock_at for c in blocked_by if c.unblock_at]
        decision_unblock = max(eligible) if eligible else None
    return Decision(
        allowed=not blocked_by, checks=checks, blocked_by=blocked_by,
        unblock_at=decision_unblock,
    )
```

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest tests/test_budget_evaluator.py -v
uv run pytest -q -m "not live" -x
git add src/aegis/budget/evaluator.py tests/test_budget_evaluator.py
git commit -m "feat(budget): evaluate_budgets() pure function over JSONL tail"
```

---

## Task 7: `QueueManager.enqueue` gates on budgets

**Files:**
- Modify: `src/aegis/queue/manager.py` (enqueue gate + `_load_recent_jsonl` helper)
- Test: `tests/test_queue_budget_enforcement.py`

The gate. `enqueue` reads the queue's JSONL tail, runs the evaluator, returns a structured error dict when blocked. Tasks with budgets cause the return shape to potentially be `dict` (rejection) instead of `tuple[str, int]` — callers must handle both.

- [ ] **Step 1: Write failing test**

Create `tests/test_queue_budget_enforcement.py`:

```python
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from aegis.budget.budgets import Budget
from aegis.budget.windows import parse_window
from aegis.queue import InboxRouter, Queue, QueueManager, sender_agent

from tests.test_queue_manager import StubSessionManager


def _seed_completed(state_dir: Path, queue: str, usd: str,
                    minutes_ago: int = 5) -> None:
    log = state_dir / "queues" / f"{queue}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    rec = {"event": "completed",
           "completed_at": (now - timedelta(minutes=minutes_ago)
                              ).isoformat().replace("+00:00", "Z"),
           "cost": {"usd": usd, "input_tokens": 0, "output_tokens": 0,
                     "cache_hit_tokens": 0, "cache_write_tokens": 0,
                     "thinking_tokens": 0}}
    log.write_text(json.dumps(rec) + "\n")


def _q_with_budget(usd: str = "1.00", window: str = "1h") -> Queue:
    return Queue(name="impl", agent_profile="opus", max_parallel=1,
                 provider="claude-code", model="opus",
                 budgets=[Budget("usd", Decimal(usd), window,
                                  parse_window(window))])


@pytest.mark.asyncio
async def test_enqueue_admits_when_budget_allows(tmp_path):
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q_with_budget()}, sm, inbox,
                       state_dir=tmp_path)
    result = qm.enqueue("impl", "x",
                         enqueued_by=sender_agent("p"), callback=False)
    assert isinstance(result, tuple)
    tid, pos = result
    assert isinstance(tid, str)


@pytest.mark.asyncio
async def test_enqueue_rejects_when_budget_exhausted(tmp_path):
    _seed_completed(tmp_path, "impl", usd="1.50", minutes_ago=5)
    sm = StubSessionManager()
    inbox = InboxRouter()
    qm = QueueManager({"impl": _q_with_budget()}, sm, inbox,
                       state_dir=tmp_path)
    result = qm.enqueue("impl", "x",
                         enqueued_by=sender_agent("p"), callback=False)
    assert isinstance(result, dict)
    assert "error" in result
    assert result["queue"] == "impl"
    assert len(result["blocked_by"]) == 1
    bc = result["blocked_by"][0]
    assert bc["constraint"] == "usd"
    assert Decimal(bc["spent"]) == Decimal("1.50")
    assert bc["window"] == "1h"
    assert bc["unblock_at"]
    assert result["unblock_at"]


@pytest.mark.asyncio
async def test_enqueue_no_budgets_unchanged(tmp_path):
    """Queue with no budgets: same tuple return as pre-v0.9."""
    sm = StubSessionManager()
    inbox = InboxRouter()
    q = Queue(name="impl", agent_profile="opus", max_parallel=1,
              provider="claude-code", model="opus")
    qm = QueueManager({"impl": q}, sm, inbox, state_dir=tmp_path)
    result = qm.enqueue("impl", "x",
                         enqueued_by=sender_agent("p"), callback=False)
    assert isinstance(result, tuple)


@pytest.mark.asyncio
async def test_multi_budget_partial_block_names_only_blocking(tmp_path):
    _seed_completed(tmp_path, "impl", usd="1.50", minutes_ago=5)
    sm = StubSessionManager()
    inbox = InboxRouter()
    q = Queue(name="impl", agent_profile="opus", max_parallel=1,
              provider="claude-code", model="opus",
              budgets=[
                  Budget("usd", Decimal("1.00"), "1h", parse_window("1h")),
                  Budget("usd", Decimal("10.00"), "24h", parse_window("24h")),
              ])
    qm = QueueManager({"impl": q}, sm, inbox, state_dir=tmp_path)
    result = qm.enqueue("impl", "x",
                         enqueued_by=sender_agent("p"), callback=False)
    assert isinstance(result, dict)
    assert len(result["blocked_by"]) == 1
    assert result["blocked_by"][0]["window"] == "1h"
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/test_queue_budget_enforcement.py -v
```
Expected: FAIL — gate not implemented.

- [ ] **Step 3: Implement the gate**

In `src/aegis/queue/manager.py`:

1. Import:
   ```python
   from datetime import datetime, timezone
   from aegis.budget.evaluator import evaluate_budgets
   ```

2. Add the JSONL tail loader as a method on `QueueManager`:
   ```python
   def _load_recent_jsonl(self, queue: str, max_age) -> list[dict]:
       """Read this queue's JSONL, return terminal records within max_age."""
       import json
       if self._state_dir is None:
           return []
       path = Path(self._state_dir) / "queues" / f"{queue}.jsonl"
       if not path.exists():
           return []
       cutoff = datetime.now(timezone.utc) - max_age
       out: list[dict] = []
       for line in path.read_text().splitlines():
           if not line.strip():
               continue
           try:
               rec = json.loads(line)
           except json.JSONDecodeError:
               continue
           if rec.get("event") not in ("completed", "failed"):
               continue
           ts_str = rec.get("completed_at", "")
           if ts_str.endswith("Z"):
               ts_str = ts_str[:-1] + "+00:00"
           try:
               ts = datetime.fromisoformat(ts_str)
           except (ValueError, TypeError):
               continue
           if ts >= cutoff:
               out.append(rec)
       return out
   ```

3. At the top of `enqueue(...)`, after the `queue not in self._queues` check, add:
   ```python
   q = self._queues[queue]
   if q.budgets:
       tail = self._load_recent_jsonl(
           queue, max_age=max(b.window for b in q.budgets))
       decision = evaluate_budgets(
           tail, q.budgets, datetime.now(timezone.utc))
       if not decision.allowed:
           return {
               "error": f"queue {queue!r} over budget",
               "queue": queue,
               "blocked_by": [
                   {"constraint": c.constraint,
                    "limit": str(c.limit),
                    "spent": str(c.spent),
                    "window": c.window_str,
                    "unblock_at": c.unblock_at.isoformat().replace(
                        "+00:00", "Z") if c.unblock_at else None}
                   for c in decision.blocked_by],
               "unblock_at": decision.unblock_at.isoformat().replace(
                   "+00:00", "Z") if decision.unblock_at else None,
           }
   ```

- [ ] **Step 4: Audit callers for the dict-or-tuple return shape**

```bash
grep -rn "\.enqueue(" src/aegis/ | grep -v "_enqueue\|tests" | head
```

For each caller that does `tid, pos = ...enqueue(...)`, wrap in a dict-shape check or change the unpack to handle both. Notable callers:

- `src/aegis/mcp/server.py::aegis_enqueue` — already returns `result` (dict or task_id+pos), no change.
- `src/aegis/remote/plane.py::enqueue` HTTP handler — make sure it returns 4xx with the error body for budget rejection (not 200). Add: `if isinstance(result, dict): return JSONResponse(result, status_code=429)`.
- `src/aegis/workflow/engine.py::enqueue` (if present) — fold into Task 8.
- `src/aegis/scheduler/workflows/enqueue.py` (if present) — built-in scheduler workflow. Fold into Task 8 too.

For any others, add the dict-shape branch inline.

- [ ] **Step 5: Run tests**

```
uv run pytest tests/test_queue_budget_enforcement.py -v
uv run pytest -q -m "not live" -x
```
Expected: all green. If any existing test breaks because of the new return shape, fix the caller in-place.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/queue/manager.py src/aegis/remote/plane.py \
        tests/test_queue_budget_enforcement.py
# plus any other caller fixes
git commit -m "feat(budget): QueueManager.enqueue gates on multi-window budgets"
```

---

## Task 8: `BudgetExceeded` typed exception for `WorkflowEngine.enqueue`

**Files:**
- Create: `src/aegis/budget/errors.py`
- Modify: `src/aegis/budget/__init__.py` (export)
- Modify: `src/aegis/workflow/engine.py`
- Test: `tests/test_workflow_budget.py`

`engine.enqueue` raises `BudgetExceeded(queue, decision)` on rejection so workflow Python can `try/except` for retry-with-different-queue patterns.

- [ ] **Step 1: Write failing test**

Create `tests/test_workflow_budget.py`:

```python
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from aegis.budget import BudgetExceeded
from aegis.budget.budgets import Budget
from aegis.budget.windows import parse_window
from aegis.queue import InboxRouter, Queue, QueueManager, sender_agent
from aegis.workflow.engine import WorkflowEngine

from tests.test_queue_manager import StubSessionManager


@pytest.mark.asyncio
async def test_engine_enqueue_raises_on_budget_exhausted(tmp_path):
    log = tmp_path / "queues" / "impl.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    log.write_text(json.dumps({
        "event": "completed",
        "completed_at": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "cost": {"usd": "1.50"},
    }) + "\n")
    sm = StubSessionManager()
    inbox = InboxRouter()
    q = Queue(name="impl", agent_profile="opus", max_parallel=1,
              provider="claude-code", model="opus",
              budgets=[Budget("usd", Decimal("1.00"), "1h",
                                parse_window("1h"))])
    qm = QueueManager({"impl": q}, sm, inbox, state_dir=tmp_path)
    engine = WorkflowEngine(queue_manager=qm, ...)  # match real ctor;
                                                     # check test_workflow_engine.py
    with pytest.raises(BudgetExceeded) as ei:
        await engine.enqueue("impl", "x", from_handle="caller")
    assert ei.value.queue == "impl"
    assert ei.value.decision.allowed is False
    assert "1.50" in str(ei.value)
```

(Look up the real `WorkflowEngine` constructor in `tests/test_workflow_engine.py` for the parameter shape — pass equivalents here.)

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/test_workflow_budget.py -v
```
Expected: FAIL — `BudgetExceeded` not defined.

- [ ] **Step 3: Implement `BudgetExceeded`**

Create `src/aegis/budget/errors.py`:

```python
"""Typed exceptions for budget rejection."""
from __future__ import annotations

from aegis.budget.evaluator import Decision


class BudgetExceeded(Exception):
    """Raised when a queue's budgets reject an enqueue.

    Carries the full Decision so callers can inspect blocked_by /
    unblock_at and choose a retry strategy.
    """
    def __init__(self, queue: str, decision: Decision) -> None:
        self.queue = queue
        self.decision = decision
        binding = ", ".join(
            f"{c.spent}/{c.limit} {c.constraint} in {c.window_str}"
            for c in decision.blocked_by)
        super().__init__(f"queue {queue!r} over budget: {binding}")
```

Update `src/aegis/budget/__init__.py` to export it:

```python
from aegis.budget.errors import BudgetExceeded

__all__ = ["BudgetExceeded"]
```

- [ ] **Step 4: Wire into `WorkflowEngine.enqueue`**

In `src/aegis/workflow/engine.py`, find the `enqueue` method. Change its dict-shape branch from "return error" to "raise BudgetExceeded":

```python
async def enqueue(self, queue: str, payload: str, *,
                  from_handle: str, callback: bool = False) -> str:
    result = self._qm.enqueue(
        queue, payload,
        enqueued_by=sender_agent(from_handle), callback=callback)
    if isinstance(result, dict):
        # Reconstruct the Decision from the dict for the exception body.
        from decimal import Decimal
        from datetime import datetime
        from aegis.budget import BudgetExceeded
        from aegis.budget.evaluator import BudgetCheck, Decision

        def _parse_iso_or_none(s):
            if not s:
                return None
            try:
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                return datetime.fromisoformat(s)
            except Exception:
                return None

        checks = [BudgetCheck(
            constraint=bc["constraint"],
            limit=Decimal(bc["limit"]),
            spent=Decimal(bc["spent"]),
            window_str=bc["window"],
            window_start=None,
            allowed=False,
            headroom=Decimal(bc["limit"]) - Decimal(bc["spent"]),
            unblock_at=_parse_iso_or_none(bc.get("unblock_at"))
        ) for bc in result.get("blocked_by", [])]
        decision = Decision(
            allowed=False, checks=checks, blocked_by=checks,
            unblock_at=_parse_iso_or_none(result.get("unblock_at")))
        raise BudgetExceeded(queue=queue, decision=decision)
    tid, _ = result
    return tid
```

If the engine doesn't currently have `enqueue` — add a fresh method following the local-substrate pattern that already exists in workflow code.

- [ ] **Step 5: Run tests + commit**

```bash
uv run pytest tests/test_workflow_budget.py -v
uv run pytest -q -m "not live" -x
git add src/aegis/budget/errors.py src/aegis/budget/__init__.py \
        src/aegis/workflow/engine.py tests/test_workflow_budget.py
git commit -m "feat(budget): BudgetExceeded typed exception for workflow engine"
```

---

## Task 9: HTTP — `GET /remote/v1/budget` + `/budget/<queue>`

**Files:**
- Modify: `src/aegis/remote/plane.py`
- Test: `tests/test_remote_budget_endpoints.py`

Two GET endpoints. Auth gated by `_check_auth` like the other remote-plane routes. Cross-host inspection; no PUT/DELETE.

- [ ] **Step 1: Write failing tests**

Create `tests/test_remote_budget_endpoints.py`. Mirror the bridge construction pattern from `tests/test_remote_plane.py` — copy the `_make_queue_manager(tmp_path)` helper or build an inline equivalent that uses `StubSessionManager` and `InboxRouter`:

```python
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from httpx import ASGITransport

from aegis.budget.budgets import Budget
from aegis.budget.windows import parse_window
from aegis.queue import InboxRouter, Queue, QueueManager
from aegis.remote.config import RemotePlaneSpec
from aegis.remote.plane import build_plane

from tests.test_queue_manager import StubSessionManager


def _bridge(qm):
    """Minimal bridge for the plane: needs queue_manager."""
    class B:
        queue_manager = qm
        inbox_router = qm._inbox
        remote_plane = None
        remotes = {}
        # Other fields as the plane reads them — copy the pattern from
        # tests/test_remote_plane.py if helpers there cover more.
    return B()


@pytest.mark.asyncio
async def test_budget_list_includes_all_queues(tmp_path):
    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({
        "impl": Queue(name="impl", agent_profile="opus", max_parallel=1,
                       provider="claude-code", model="opus",
                       budgets=[Budget("usd", Decimal("1.00"), "1h",
                                        parse_window("1h"))]),
        "fast": Queue(name="fast", agent_profile="haiku", max_parallel=2,
                       provider="claude-code", model="haiku"),
    }, sm, inbox, state_dir=tmp_path)
    app = build_plane(_bridge(qm),
                       RemotePlaneSpec(bind="127.0.0.1:8556",
                                        peer_name="test"))
    async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                  base_url="http://test") as c:
        r = await c.get("/remote/v1/budget")
        assert r.status_code == 200
    data = r.json()
    names = {q["name"] for q in data["queues"]}
    assert names == {"impl", "fast"}
    fast = next(q for q in data["queues"] if q["name"] == "fast")
    assert fast["budgets_count"] == 0
    assert fast["status"] == "no-budget"


@pytest.mark.asyncio
async def test_budget_show_blocked(tmp_path):
    # Pre-seed JSONL: $1.50 spent.
    log = tmp_path / "queues" / "impl.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    log.write_text(json.dumps({
        "event": "completed",
        "completed_at": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "cost": {"usd": "1.50"},
    }) + "\n")

    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({
        "impl": Queue(name="impl", agent_profile="opus", max_parallel=1,
                       provider="claude-code", model="opus",
                       budgets=[Budget("usd", Decimal("1.00"), "1h",
                                        parse_window("1h"))]),
    }, sm, inbox, state_dir=tmp_path)
    app = build_plane(_bridge(qm),
                       RemotePlaneSpec(bind="127.0.0.1:8556",
                                        peer_name="test"))
    async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                  base_url="http://test") as c:
        r = await c.get("/remote/v1/budget/impl")
        assert r.status_code == 200
    data = r.json()
    assert data["allowed"] is False
    assert len(data["blocked_by"]) == 1
    assert Decimal(data["blocked_by"][0]["spent"]) == Decimal("1.50")


@pytest.mark.asyncio
async def test_budget_show_unknown_queue_404(tmp_path):
    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({}, sm, inbox, state_dir=tmp_path)
    app = build_plane(_bridge(qm),
                       RemotePlaneSpec(bind="127.0.0.1:8556",
                                        peer_name="test"))
    async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                  base_url="http://test") as c:
        r = await c.get("/remote/v1/budget/ghost")
        assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/test_remote_budget_endpoints.py -v
```
Expected: FAIL — endpoints not registered.

- [ ] **Step 3: Register endpoints in `plane.py`**

Follow the same pattern as the existing schedule endpoints. Add inside `build_plane`:

```python
# /remote/v1/budget — list shape
@app.route("/remote/v1/budget", methods=["GET"])
async def budget_list(request):
    auth_err = _check_auth(request, spec)
    if auth_err:
        return JSONResponse(auth_err, status_code=401)
    from datetime import datetime, timezone
    from aegis.budget.evaluator import evaluate_budgets

    qm = bridge.queue_manager
    now = datetime.now(timezone.utc)
    rows = []
    for name, q in qm._queues.items():
        if not q.budgets:
            rows.append({"name": name, "budgets_count": 0,
                          "status": "no-budget", "binding": None,
                          "unblock_at": None})
            continue
        tail = qm._load_recent_jsonl(
            name, max_age=max(b.window for b in q.budgets))
        d = evaluate_budgets(tail, q.budgets, now)
        if d.allowed:
            tightest = min(
                d.checks,
                key=lambda c: (c.headroom / c.limit) if c.limit > 0 else 0)
            binding = (f"${tightest.spent} of ${tightest.limit} "
                        f"/ {tightest.window_str}"
                        if tightest.constraint == "usd"
                        else f"{tightest.spent} of {tightest.limit} "
                              f"{tightest.constraint} / {tightest.window_str}")
            rows.append({"name": name, "budgets_count": len(q.budgets),
                          "status": "ok", "binding": binding,
                          "unblock_at": None})
        else:
            c = d.blocked_by[0]
            binding = (f"${c.spent} of ${c.limit} / {c.window_str}"
                        if c.constraint == "usd"
                        else f"{c.spent} of {c.limit} "
                              f"{c.constraint} / {c.window_str}")
            rows.append({"name": name, "budgets_count": len(q.budgets),
                          "status": "blocked", "binding": binding,
                          "unblock_at": d.unblock_at.isoformat().replace(
                              "+00:00", "Z") if d.unblock_at else None})
    return JSONResponse({"queues": rows})


# /remote/v1/budget/<queue> — full Decision
@app.route("/remote/v1/budget/{queue}", methods=["GET"])
async def budget_show(request):
    auth_err = _check_auth(request, spec)
    if auth_err:
        return JSONResponse(auth_err, status_code=401)
    name = request.path_params["queue"]
    qm = bridge.queue_manager
    if name not in qm._queues:
        return JSONResponse({"error": "unknown queue"}, status_code=404)
    q = qm._queues[name]
    from datetime import datetime, timezone
    from aegis.budget.evaluator import evaluate_budgets
    now = datetime.now(timezone.utc)
    if not q.budgets:
        return JSONResponse({"name": name, "allowed": True, "checks": [],
                              "blocked_by": [], "unblock_at": None})
    tail = qm._load_recent_jsonl(
        name, max_age=max(b.window for b in q.budgets))
    d = evaluate_budgets(tail, q.budgets, now)

    def _ser(c):
        return {"constraint": c.constraint, "limit": str(c.limit),
                "spent": str(c.spent), "window": c.window_str,
                "window_start": c.window_start.isoformat().replace(
                    "+00:00", "Z"),
                "allowed": c.allowed, "headroom": str(c.headroom),
                "unblock_at": c.unblock_at.isoformat().replace(
                    "+00:00", "Z") if c.unblock_at else None}
    return JSONResponse({
        "name": name, "allowed": d.allowed,
        "checks": [_ser(c) for c in d.checks],
        "blocked_by": [_ser(c) for c in d.blocked_by],
        "unblock_at": d.unblock_at.isoformat().replace("+00:00", "Z")
                       if d.unblock_at else None,
    })
```

(Add the matching route registrations to the `Route(...)` list at the bottom of `build_plane` — same shape as the other GET routes.)

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest tests/test_remote_budget_endpoints.py -v
uv run pytest -q -m "not live" -x
git add src/aegis/remote/plane.py tests/test_remote_budget_endpoints.py
git commit -m "feat(budget): GET /remote/v1/budget list + show"
```

---

## Task 10: Outbound client + `aegis_budget_status` MCP tool

**Files:**
- Modify: `src/aegis/remote/client.py`
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_remote_budget_client.py`
- Test: `tests/test_mcp_budget_tool.py`

Two outbound client functions + one MCP tool that dispatches local-vs-remote.

- [ ] **Step 1: Write client tests**

Create `tests/test_remote_budget_client.py`:

```python
import httpx
import pytest

from aegis.remote.client import remote_budget_list, remote_budget_show
from aegis.remote.config import RemoteSpec


@pytest.mark.asyncio
async def test_remote_budget_list(httpx_mock):
    spec = RemoteSpec(url="http://1.2.3.4:8556")
    httpx_mock.add_response(
        method="GET", url="http://1.2.3.4:8556/remote/v1/budget",
        status_code=200,
        json={"queues": [{"name": "impl", "budgets_count": 1,
                           "status": "ok"}]})
    r = await remote_budget_list(spec)
    assert r["queues"][0]["name"] == "impl"


@pytest.mark.asyncio
async def test_remote_budget_show(httpx_mock):
    spec = RemoteSpec(url="http://1.2.3.4:8556")
    httpx_mock.add_response(
        method="GET", url="http://1.2.3.4:8556/remote/v1/budget/impl",
        status_code=200,
        json={"name": "impl", "allowed": True, "checks": [],
              "blocked_by": [], "unblock_at": None})
    r = await remote_budget_show(spec, "impl")
    assert r["name"] == "impl"


@pytest.mark.asyncio
async def test_remote_budget_show_404(httpx_mock):
    spec = RemoteSpec(url="http://1.2.3.4:8556")
    httpx_mock.add_response(
        method="GET", url="http://1.2.3.4:8556/remote/v1/budget/ghost",
        status_code=404,
        json={"error": "unknown queue"})
    r = await remote_budget_show(spec, "ghost")
    assert "error" in r
```

- [ ] **Step 2: Implement clients in `src/aegis/remote/client.py`**

```python
async def remote_budget_list(spec: RemoteSpec) -> dict:
    async with await _build_client(spec) as client:
        r = await client.get("/remote/v1/budget")
    if r.status_code == 200:
        return r.json()
    return _normalize_err("budget list", r)


async def remote_budget_show(spec: RemoteSpec, queue: str) -> dict:
    async with await _build_client(spec) as client:
        r = await client.get(f"/remote/v1/budget/{queue}")
    if r.status_code == 200:
        return r.json()
    return _normalize_err("budget show", r)
```

(`_normalize_err` already exists from the v0.7 work — reuse.)

- [ ] **Step 3: Write MCP tool test**

Create `tests/test_mcp_budget_tool.py`:

```python
from decimal import Decimal
from pathlib import Path

import pytest

from aegis.budget.budgets import Budget
from aegis.budget.windows import parse_window
from aegis.mcp.server import build_server
from aegis.queue import InboxRouter, Queue, QueueManager
from aegis.remote.config import RemoteSpec

from tests.test_queue_manager import StubSessionManager


class _Bridge:
    def __init__(self, qm, remotes=None):
        self.queue_manager = qm
        self.inbox_router = qm._inbox
        self.remotes = remotes or {}
        self.remote_plane = None


async def _call(server, name, **kwargs):
    tools = await server.list_tools()
    tool = next(t for t in tools if t.name == name)
    result = await tool.run(kwargs)
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]
    if sc is not None:
        return sc
    return result.content[0].text


@pytest.mark.asyncio
async def test_budget_status_local_no_queue_lists_all(tmp_path):
    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({
        "impl": Queue(name="impl", agent_profile="opus", max_parallel=1,
                       provider="claude-code", model="opus",
                       budgets=[Budget("usd", Decimal("1.00"), "1h",
                                        parse_window("1h"))]),
        "fast": Queue(name="fast", agent_profile="opus", max_parallel=1,
                       provider="claude-code", model="opus"),
    }, sm, inbox, state_dir=tmp_path)
    server = build_server(_Bridge(qm))
    r = await _call(server, "aegis_budget_status", from_handle="h")
    assert "queues" in r


@pytest.mark.asyncio
async def test_budget_status_local_with_queue(tmp_path):
    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({
        "impl": Queue(name="impl", agent_profile="opus", max_parallel=1,
                       provider="claude-code", model="opus",
                       budgets=[Budget("usd", Decimal("1.00"), "1h",
                                        parse_window("1h"))]),
    }, sm, inbox, state_dir=tmp_path)
    server = build_server(_Bridge(qm))
    r = await _call(server, "aegis_budget_status",
                     from_handle="h", queue="impl")
    assert r["name"] == "impl"
    assert "checks" in r


@pytest.mark.asyncio
async def test_budget_status_remote_routes_through_client(monkeypatch, tmp_path):
    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({}, sm, inbox, state_dir=tmp_path)
    bridge = _Bridge(qm, remotes={"vps": RemoteSpec(url="http://x")})
    captured = {}
    async def fake_list(spec):
        captured["called"] = True
        return {"queues": []}
    monkeypatch.setattr("aegis.remote.client.remote_budget_list", fake_list)
    server = build_server(bridge)
    r = await _call(server, "aegis_budget_status",
                     from_handle="h", target="vps")
    assert captured.get("called")


@pytest.mark.asyncio
async def test_budget_status_unknown_target_errors(tmp_path):
    sm = StubSessionManager(); inbox = InboxRouter()
    qm = QueueManager({}, sm, inbox, state_dir=tmp_path)
    bridge = _Bridge(qm, remotes={})
    server = build_server(bridge)
    r = await _call(server, "aegis_budget_status",
                     from_handle="h", target="vps")
    assert "error" in r
```

- [ ] **Step 4: Implement `aegis_budget_status` in `src/aegis/mcp/server.py`**

```python
@server.tool
async def aegis_budget_status(from_handle: str,
                                queue: str | None = None,
                                target: str | None = None) -> dict:
    """Inspect per-queue budgets on this serve or a remote peer.

    queue=None: summary across all queues on the targeted serve.
    queue="<name>": full Decision for that queue.
    target=None local; target="<peer>" routes through /remote/v1/budget.
    """
    if target is not None:
        remotes = getattr(bridge, "remotes", {}) or {}
        if target not in remotes:
            return {"error": f"unknown target {target!r}"}
        from aegis.remote.client import (remote_budget_list,
                                          remote_budget_show)
        spec = remotes[target]
        if queue is None:
            return await remote_budget_list(spec)
        return await remote_budget_show(spec, queue)

    # Local path.
    from datetime import datetime, timezone
    from aegis.budget.evaluator import evaluate_budgets
    qm = bridge.queue_manager
    now = datetime.now(timezone.utc)

    def _ser(c):
        return {"constraint": c.constraint, "limit": str(c.limit),
                "spent": str(c.spent), "window": c.window_str,
                "allowed": c.allowed, "headroom": str(c.headroom)}

    if queue is None:
        rows = []
        for name, q in qm._queues.items():
            if not q.budgets:
                rows.append({"name": name, "budgets_count": 0,
                              "status": "no-budget"})
                continue
            tail = qm._load_recent_jsonl(
                name, max_age=max(b.window for b in q.budgets))
            d = evaluate_budgets(tail, q.budgets, now)
            rows.append({"name": name, "budgets_count": len(q.budgets),
                          "status": "ok" if d.allowed else "blocked"})
        return {"queues": rows}

    if queue not in qm._queues:
        return {"error": f"unknown queue {queue!r}"}
    q = qm._queues[queue]
    if not q.budgets:
        return {"name": queue, "allowed": True, "checks": [],
                "blocked_by": [], "unblock_at": None}
    tail = qm._load_recent_jsonl(
        queue, max_age=max(b.window for b in q.budgets))
    d = evaluate_budgets(tail, q.budgets, now)
    return {"name": queue, "allowed": d.allowed,
            "checks": [_ser(c) for c in d.checks],
            "blocked_by": [_ser(c) for c in d.blocked_by]}
```

- [ ] **Step 5: Run tests + commit**

```bash
uv run pytest tests/test_remote_budget_client.py tests/test_mcp_budget_tool.py -v
uv run pytest -q -m "not live" -x
git add src/aegis/remote/client.py src/aegis/mcp/server.py \
        tests/test_remote_budget_client.py tests/test_mcp_budget_tool.py
git commit -m "feat(budget): aegis_budget_status MCP tool + remote_budget_* clients"
```

---

## Task 11: `aegis budget` CLI subapp

**Files:**
- Create: `src/aegis/cli_budget.py`
- Modify: `src/aegis/cli.py` (mount the subapp)
- Test: `tests/test_cli_budget.py`

Mirror `aegis schedule`'s shape. `aegis budget list` / `show <queue>` with optional `--remote <peer>`.

- [ ] **Step 1: Write the test**

Create `tests/test_cli_budget.py` mirroring the existing `tests/test_cli_schedule_remote.py`:

```python
import pytest
from typer.testing import CliRunner

# Use the same import path the schedule tests use.
from aegis.cli import app


def test_budget_list_runs(tmp_path, monkeypatch):
    """`aegis budget list` invokes without error against an empty config."""
    # ... seed a minimal .aegis.py via tmp_path,
    #     change cwd to tmp_path, run CliRunner(app, ["budget", "list"]),
    #     assert exit code 0
    ...


def test_budget_show_unknown_queue_errors(tmp_path, monkeypatch):
    """`aegis budget show ghost` returns non-zero."""
    ...


def test_budget_list_remote_calls_client(monkeypatch, tmp_path):
    """`aegis budget list --remote vps` invokes remote_budget_list."""
    ...
```

(Flesh out from the existing `tests/test_cli_schedule_remote.py` pattern.)

- [ ] **Step 2: Implement the subapp**

Create `src/aegis/cli_budget.py`:

```python
"""`aegis budget` CLI subapp."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(add_completion=False, no_args_is_help=False)
_console = Console()


def _cfg():
    from aegis.config import find_project_root, load_config
    return load_config(find_project_root())


def _load_jsonl(state_dir: Path, queue: str) -> list[dict]:
    log = state_dir / "queues" / f"{queue}.jsonl"
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text().splitlines()
            if l.strip()]


@app.command("list")
def list_budgets(
    remote: str = typer.Option(None, "--remote"),
) -> None:
    cfg = _cfg()
    if remote is not None:
        from aegis.remote.client import remote_budget_list
        if remote not in cfg.remotes:
            typer.echo(f"unknown remote {remote!r}", err=True)
            raise typer.Exit(1)
        result = asyncio.run(remote_budget_list(cfg.remotes[remote]))
    else:
        from aegis.budget.evaluator import evaluate_budgets
        state_dir = Path.cwd() / ".aegis" / "state"
        now = datetime.now(timezone.utc)
        rows = []
        for name, q in cfg.queues.items():
            if not q.budgets:
                rows.append({"name": name, "budgets_count": 0,
                              "status": "no-budget"})
                continue
            # Filter tail to terminal events within longest window.
            cutoff = now - max(b.window for b in q.budgets)
            tail = []
            for rec in _load_jsonl(state_dir, name):
                if rec.get("event") not in ("completed", "failed"):
                    continue
                ts_str = rec.get("completed_at", "")
                try:
                    ts = datetime.fromisoformat(
                        ts_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue
                if ts >= cutoff:
                    tail.append(rec)
            d = evaluate_budgets(tail, q.budgets, now)
            rows.append({"name": name, "budgets_count": len(q.budgets),
                          "status": "ok" if d.allowed else "blocked"})
        result = {"queues": rows}

    if "error" in result:
        typer.echo(result["error"], err=True)
        raise typer.Exit(1)

    table = Table()
    table.add_column("QUEUE")
    table.add_column("BUDGETS")
    table.add_column("STATUS")
    for row in result["queues"]:
        table.add_row(row["name"],
                      str(row.get("budgets_count", "?")),
                      row.get("status", "?"))
    _console.print(table)


@app.command("show")
def show_budget(
    queue: str,
    remote: str = typer.Option(None, "--remote"),
) -> None:
    cfg = _cfg()
    if remote is not None:
        from aegis.remote.client import remote_budget_show
        if remote not in cfg.remotes:
            typer.echo(f"unknown remote {remote!r}", err=True)
            raise typer.Exit(1)
        result = asyncio.run(remote_budget_show(cfg.remotes[remote], queue))
    else:
        from aegis.budget.evaluator import evaluate_budgets
        if queue not in cfg.queues:
            typer.echo(f"unknown queue {queue!r}", err=True)
            raise typer.Exit(1)
        q = cfg.queues[queue]
        if not q.budgets:
            typer.echo(f"queue {queue!r} has no budgets configured.")
            return
        state_dir = Path.cwd() / ".aegis" / "state"
        now = datetime.now(timezone.utc)
        tail = _load_jsonl(state_dir, queue)
        d = evaluate_budgets(tail, q.budgets, now)
        result = {
            "name": queue, "allowed": d.allowed,
            "checks": [{"constraint": c.constraint, "limit": str(c.limit),
                          "spent": str(c.spent), "window": c.window_str,
                          "allowed": c.allowed, "headroom": str(c.headroom)}
                         for c in d.checks],
        }

    if "error" in result:
        typer.echo(result["error"], err=True)
        raise typer.Exit(1)

    table = Table(title=f"budget for queue {queue!r}")
    for col in ("CONSTRAINT", "LIMIT", "SPENT", "WINDOW",
                "HEADROOM", "STATUS"):
        table.add_column(col)
    for c in result["checks"]:
        status = "✓" if c["allowed"] else "⛔"
        table.add_row(c["constraint"], c["limit"], c["spent"],
                       c["window"], c["headroom"], status)
    _console.print(table)
```

- [ ] **Step 3: Mount in `cli.py`**

In `src/aegis/cli.py`, near the existing schedule mount:

```python
from aegis.cli_budget import app as _budget_app  # noqa: E402
app.add_typer(_budget_app, name="budget")
```

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest tests/test_cli_budget.py -v
uv run pytest -q -m "not live" -x
git add src/aegis/cli_budget.py src/aegis/cli.py tests/test_cli_budget.py
git commit -m "feat(budget): aegis budget list/show CLI verbs + --remote flag"
```

---

## Task 12: Docs + CHANGELOG + 0.9.0 release

**Files:**
- Create: `docs/budget.md`
- Modify: `docs/configuration.md`, `docs/index.md`, `docs/roadmap.md`, `README.md`, `mkdocs.yml`, `CHANGELOG.md`, `pyproject.toml`, `uv.lock`

Pattern mirrors the v0.8 / v0.8.1 releases. Write `docs/budget.md` in user-doc voice, sync README + index + configuration + roadmap, bump version, tag, push.

- [ ] **Step 1: Write `docs/budget.md`**

User-facing doc covering motivation, model (multi-window all-must-allow, USD + output_tokens), worked config example, rejection shape, observability (CLI / MCP / HTTP), patterns (cap-an-opus-queue, runaway-output belt), non-goals, FAQ. Pull paragraphs from the spec; rewrite in second-person ("you").

- [ ] **Step 2: Sync `docs/configuration.md`**

Under Queues, after `max_parallel`, document the optional `budgets:` list with the same worked example as the spec. Link to `docs/budget.md`.

- [ ] **Step 3: Sync `docs/index.md` + `mkdocs.yml`**

Add a bullet in "What's also in the box" of `index.md`:

> - **Per-queue budgets.** Declare USD or output-token ceilings over rolling windows on any queue; the substrate rejects new enqueues that would land the queue over budget, naming the binding constraint. Pull-only observability via CLI, MCP, HTTP. See [Budgets](budget.md).

In `mkdocs.yml`, add `- Budgets: budget.md` under `Concepts`.

- [ ] **Step 4: Sync `docs/roadmap.md`**

Above `### v0.8.1`:

```markdown
### v0.9.0 (current)
- **Per-queue budgets.** Multi-window per-queue USD / output-token
  ceilings, all-must-allow, enforcement at enqueue time with a
  structured rejection naming the binding constraint and an
  `unblock_at` ETA. Cost computed from existing SessionMetrics via a
  static per-(provider, model) price table at
  `src/aegis/budget/prices.py`. Inspection via `aegis budget
  list/show`, `aegis_budget_status` MCP tool, and `GET
  /remote/v1/budget` on the plane. TUI surface deferred to v0.9.1.
```

- [ ] **Step 5: Sync `README.md`**

Add a "Per-queue budgets" section near the v0.8.1 callbacks section. Include the worked `queues = {...}` example and a link to `docs/budget.md`. Add `- [Budgets](https://apiad.github.io/aegis/budget/)` to the docs link list.

- [ ] **Step 6: `CHANGELOG.md` `[0.9.0]` entry**

Above `## [0.8.1]`:

```markdown
## [0.9.0] - 2026-05-26

### Added
- **Per-queue budgets.** Each queue may declare one or more
  `(constraint, window)` ceilings (USD or output-token) over a
  rolling window. New `aegis_enqueue` calls are rejected with a
  structured error when admitting the task would push the queue
  over any of the declared budgets; ALL budgets must allow. Rejection
  names every blocked constraint and an `unblock_at` ETA.
- **Cost accounting.** Existing per-queue JSONL audit now carries a
  `cost` field on every `completed` and `failed` record:
  `{usd, input_tokens, output_tokens, cache_hit_tokens,
  cache_write_tokens, thinking_tokens}` computed from
  `SessionMetrics` (committed c_in/c_out/c_cached counters) +
  a static per-(provider, model) price table at
  `src/aegis/budget/prices.py`. Unknown models record
  `cost: {error: "unknown_model"}` without crashing the finalizer.
  Failed workers count toward budget — they burned tokens too.
- **`BudgetExceeded` typed exception** for the workflow engine:
  `engine.enqueue` raises with the full Decision attached so
  workflow Python can choose a retry strategy.
- **`aegis_budget_status` MCP tool** with `target=None` local and
  `target="<peer>"` cross-host via the new `GET /remote/v1/budget`
  and `GET /remote/v1/budget/<queue>` HTTP endpoints.
- **`aegis budget` CLI** — `list` (one-line summary per queue) and
  `show <queue>` (full Decision with per-budget rows). `--remote
  <peer>` on both.

The TUI strip + dashboard band described in the spec are
**deferred to v0.9.1**.

Spec: `docs/superpowers/specs/2026-05-25-aegis-per-queue-budgets-design.md`.
```

- [ ] **Step 7: Bump version + lock**

```bash
sed -i 's/^version = "0\.8\.1"$/version = "0.9.0"/' pyproject.toml
sed -i '0,/^version = "0\.8\.1"$/s//version = "0.9.0"/' uv.lock
grep -nE '^name = "aegis-harness"|^version = ' uv.lock | head -4
grep '^version' pyproject.toml
```
Expected: both at `0.9.0`.

- [ ] **Step 8: Final gate**

```
uv run pytest -q -m "not live" -x
```
Expected: all green.

- [ ] **Step 9: Release commit + tag + push**

```bash
git add docs/budget.md docs/configuration.md docs/index.md \
        docs/roadmap.md README.md mkdocs.yml CHANGELOG.md \
        pyproject.toml uv.lock
git commit -m "release: 0.9.0 — per-queue token / USD budgets"
git pull --rebase
git tag -a v0.9.0 -m "v0.9.0 — per-queue budgets. See CHANGELOG.md."
git push origin main
git push origin v0.9.0
```

- [ ] **Step 10: Confirm PyPI**

```bash
sleep 30
curl -sS https://pypi.org/pypi/aegis-harness/json | \
  python3 -c "import sys,json;d=json.load(sys.stdin);print('latest:',d['info']['version'])"
```
Expected: `latest: 0.9.0`. Retry up to 3 times with 15s sleeps if PyPI lags.

- [ ] **Step 11: Notify Telegram**

```bash
bin/notify-telegram.sh "🎉 aegis 0.9.0 released — per-queue token/USD budgets on PyPI" || true
```

---

## Self-review (v2)

**Spec coverage:**

| Spec section | Implementation task |
|---|---|
| Motivation | (context only) |
| Non-goals | enforced by absence of code + the Task 5 validator |
| Architecture overview | Tasks 3–7 |
| Config shape (multi-window, all-must-allow) | Task 5 (parser) + Task 7 (gate) |
| Cost source + price table | Task 1 (prices, ✓) + Task 2 (compute, ✓) + Task 3 (Queue.provider/model) + Task 4 (JSONL write) |
| Evaluator | Task 6 |
| Rejection shape | Task 7 (substrate) + Task 8 (workflow exception) + Task 9 (HTTP) + Task 10 (MCP) + Task 11 (CLI) |
| In-flight cost | covered by spec; Task 6 only counts terminal records |
| MCP surface | Task 10 |
| HTTP surface | Task 9 |
| CLI surface | Task 11 |
| TUI surface | **deferred to v0.9.1** — explicit in plan + CHANGELOG |
| Testing | every task has hermetic coverage; live deferred |
| Implementation notes | embedded in tasks |
| Open questions | Q1 typed BudgetExceeded → Task 8 implements it; Q2 ACP metrics → adapter in Task 4 uses defensive getattr; Q3 workflow runner own spend → non-goal |

**Plan-vs-reality verification (the v1 trap):**
Every test and code block in Tasks 3–11 references symbols verified against `main` HEAD as of `da2c719`:

- `Queue(name, agent_profile, max_parallel)` — confirmed `queue/schema.py:46`
- `Task` fields — confirmed `queue/schema.py:53` (NO provider/model; Task 3 adds them to Queue, not Task)
- `QueueManager(queues, session_manager, inbox_router, *, state_dir=...)` — confirmed `queue/manager.py:50`
- `SessionMetrics.c_in/c_out/c_cached` — confirmed `tui/metrics.py:34`
- `Agent.harness` + `Agent.model` populated by validator — confirmed `config/__init__.py:115`
- `StubSessionManager.script(handle, [events])` — confirmed `tests/test_queue_manager.py:54`
- JSONL event values `"completed"` / `"failed"` — confirmed `queue/manager.py:211, 222`
- State dir path `state_dir/queues/<queue>.jsonl` — confirmed `queue/manager.py:104`

**Placeholder scan:** the placeholder `_make_queue_manager` from v1 is removed. Tests now build QueueManager inline with the verified constructor. No "TBD" / "implement later" / "similar to Task N" survives.

**Type consistency:** `Cost` (already shipped, Task 2) ↔ `cost` field on JSONL (Task 4) ↔ `_record_value` (Task 6) all agree. `Budget(constraint, limit, window_str, window)` consistent across Tasks 5–11. `BudgetCheck` / `Decision` shape identical across evaluator, HTTP, MCP, CLI.

Plan complete.
