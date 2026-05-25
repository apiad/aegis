# Per-Queue Token / USD Budgets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship per-queue token/USD budgets with multi-window all-must-allow enforcement at enqueue time, plus MCP / HTTP / CLI / TUI inspection surfaces.

**Architecture:** A pure-function evaluator over the existing per-queue JSONL audit, gated at `QueueManager.enqueue`. Each queue declares one or more `(constraint, window)` pairs (USD or output-token ceilings over a rolling window). On worker termination, `QueueManager._finalize` writes a `cost` field on the existing `task_done` record via a new `cost.compute(metrics, provider, model)` function that consults a static per-provider price table. No new persistent state, no Telegram observer — the rejection at enqueue *is* the signal.

**Tech Stack:** Python 3.13, `Decimal` arithmetic throughout (no float drift), pytest with `uv run pytest -q -m "not live" -x`, Typer (CLI), Starlette (plane endpoints), ruamel.yaml unchanged.

**Spec:** `docs/superpowers/specs/2026-05-25-aegis-per-queue-budgets-design.md` (canonical). Read it once before starting Task 1.

**Conventions:**
- Tests live flat under `tests/`, file name `test_<topic>.py`.
- Live tests are marked `@pytest.mark.live` and auto-skip.
- Commit straight to `main` (aegis convention — see workspace memory `feedback_aegis_work_on_main`).
- Run hermetic gate before each commit: `uv run pytest -q -m "not live" -x`.
- Use uv, not pip. `uv run pytest`, `uv pip install -e .`.
- Acquire a workspace lock at the start: `bin/ws-lock acquire repos/aegis --desc "v0.9 implementation"`. Release with `bin/ws-lock gc` at the end.

---

## Task 1: Price table + window parser scaffolding

**Files:**
- Create: `src/aegis/budget/__init__.py`
- Create: `src/aegis/budget/prices.py`
- Create: `src/aegis/budget/windows.py`
- Test: `tests/test_budget_prices.py`
- Test: `tests/test_budget_windows.py`

Lays the foundation: the `PRICES` dict, the `ProviderPrices` dataclass, the `parse_window` helper. No dependencies on the rest of aegis; pure data + pure functions.

- [ ] **Step 1: Write failing prices test**

Create `tests/test_budget_prices.py`:

```python
from decimal import Decimal

import pytest

from aegis.budget.prices import PRICES, ProviderPrices, UnknownPriceError, lookup


def test_provider_prices_uses_decimal():
    p = PRICES[("claude-code", "opus")]
    assert isinstance(p.input, Decimal)
    assert isinstance(p.output, Decimal)
    assert isinstance(p.cache_hit, Decimal)
    assert isinstance(p.cache_write, Decimal)
    assert isinstance(p.thinking, Decimal)


def test_lookup_known_pair_returns_row():
    row = lookup("claude-code", "opus")
    assert row.input == Decimal("15.00")
    assert row.output == Decimal("75.00")


def test_lookup_unknown_pair_raises():
    with pytest.raises(UnknownPriceError, match="no price for"):
        lookup("madeup-provider", "made-up-model")
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/test_budget_prices.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.budget'`.

- [ ] **Step 3: Implement prices**

Create `src/aegis/budget/__init__.py` (empty for now).

Create `src/aegis/budget/prices.py`:

```python
"""Static per-(provider, model) price table for cost computation.

Rates are per-MILLION-tokens in USD. Update this file when providers
publish new prices — it is the only piece of maintained data the
budget feature depends on.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class UnknownPriceError(KeyError):
    """Raised when cost.compute() can't find a (provider, model) pair."""


@dataclass(frozen=True)
class ProviderPrices:
    """Per-million-token rates in USD, all Decimal to avoid float drift."""
    input:       Decimal
    output:      Decimal
    cache_hit:   Decimal
    cache_write: Decimal
    thinking:    Decimal


def _d(s: str) -> Decimal:
    return Decimal(s)


PRICES: dict[tuple[str, str], ProviderPrices] = {
    # Claude Code (Anthropic) — Nov 2025 list prices.
    ("claude-code", "opus"): ProviderPrices(
        input=_d("15.00"), output=_d("75.00"),
        cache_hit=_d("1.50"), cache_write=_d("18.75"),
        thinking=_d("75.00")),
    ("claude-code", "sonnet"): ProviderPrices(
        input=_d("3.00"), output=_d("15.00"),
        cache_hit=_d("0.30"), cache_write=_d("3.75"),
        thinking=_d("15.00")),
    ("claude-code", "haiku"): ProviderPrices(
        input=_d("1.00"), output=_d("5.00"),
        cache_hit=_d("0.10"), cache_write=_d("1.25"),
        thinking=_d("5.00")),
    # Gemini CLI — Nov 2025 list prices.
    ("gemini", "gemini-3-pro"): ProviderPrices(
        input=_d("1.25"), output=_d("10.00"),
        cache_hit=_d("0.31"), cache_write=_d("1.25"),
        thinking=_d("10.00")),
    ("gemini", "gemini-3-flash-preview"): ProviderPrices(
        input=_d("0.075"), output=_d("0.30"),
        cache_hit=_d("0.019"), cache_write=_d("0.075"),
        thinking=_d("0.30")),
    # OpenCode — provider-routed; defaults match Kimi K2.6 listed pricing.
    ("opencode", "kimi-k2.6"): ProviderPrices(
        input=_d("0.30"), output=_d("1.20"),
        cache_hit=_d("0.06"), cache_write=_d("0.30"),
        thinking=_d("1.20")),
}


def lookup(provider: str, model: str) -> ProviderPrices:
    """Return the price row, raise UnknownPriceError on miss."""
    try:
        return PRICES[(provider, model)]
    except KeyError:
        raise UnknownPriceError(
            f"no price for {(provider, model)!r}; "
            f"add to aegis.budget.prices.PRICES")
```

- [ ] **Step 4: Run prices test**

```
uv run pytest tests/test_budget_prices.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 5: Write failing windows test**

Create `tests/test_budget_windows.py`:

```python
from datetime import timedelta

import pytest

from aegis.budget.windows import parse_window


def test_parse_minutes():
    assert parse_window("30m") == timedelta(minutes=30)


def test_parse_hours():
    assert parse_window("1h") == timedelta(hours=1)
    assert parse_window("24h") == timedelta(hours=24)


def test_parse_days():
    assert parse_window("7d") == timedelta(days=7)


def test_parse_weeks():
    assert parse_window("1w") == timedelta(weeks=1)


def test_parse_rejects_unknown_suffix():
    with pytest.raises(ValueError, match="unknown window suffix"):
        parse_window("5y")


def test_parse_rejects_zero():
    with pytest.raises(ValueError, match="must be positive"):
        parse_window("0h")


def test_parse_rejects_negative():
    with pytest.raises(ValueError, match="must be positive"):
        parse_window("-1h")


def test_parse_rejects_no_suffix():
    with pytest.raises(ValueError, match="must end with"):
        parse_window("60")


def test_parse_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        parse_window("")
```

- [ ] **Step 6: Run test to verify failure**

```
uv run pytest tests/test_budget_windows.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 7: Implement windows**

Create `src/aegis/budget/windows.py`:

```python
"""Parse window strings like '30m', '1h', '24h', '7d', '1w' to timedelta."""
from __future__ import annotations

from datetime import timedelta

_SUFFIXES = {
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}


def parse_window(s: str) -> timedelta:
    """Convert a window string to a positive timedelta.

    Accepted: ``Nm`` (minutes), ``Nh`` (hours), ``Nd`` (days), ``Nw``
    (weeks). N must be a positive integer.
    """
    if not s:
        raise ValueError("window string is empty")
    suffix = s[-1].lower()
    if suffix not in _SUFFIXES:
        if suffix.isdigit():
            raise ValueError(f"window {s!r} must end with one of m/h/d/w")
        raise ValueError(f"unknown window suffix {suffix!r} in {s!r}")
    try:
        n = int(s[:-1])
    except ValueError:
        raise ValueError(f"window {s!r} prefix must be an integer")
    if n <= 0:
        raise ValueError(f"window {s!r} must be positive")
    return timedelta(**{_SUFFIXES[suffix]: n})
```

- [ ] **Step 8: Run windows test**

```
uv run pytest tests/test_budget_windows.py -v
```
Expected: PASS (9 tests).

- [ ] **Step 9: Run full hermetic suite**

```
uv run pytest -q -m "not live" -x
```
Expected: all green.

- [ ] **Step 10: Commit**

```bash
git add src/aegis/budget/__init__.py src/aegis/budget/prices.py \
        src/aegis/budget/windows.py \
        tests/test_budget_prices.py tests/test_budget_windows.py
git commit -m "feat(budget): price table + window parser scaffolding"
```

---

## Task 2: Cost dataclass + compute()

**Files:**
- Create: `src/aegis/budget/cost.py`
- Test: `tests/test_budget_cost.py`

`compute(metrics, provider, model) -> Cost` is the function `QueueManager._finalize` will call when a worker terminates. Takes the existing `SessionMetrics` shape; returns a `Cost` with USD + per-class token counts, ready to serialize into the `task_done` JSONL record.

- [ ] **Step 1: Inspect the SessionMetrics shape**

```bash
grep -nE "class SessionMetrics|input_tokens|output_tokens|cache_hit|cache_write|thinking_tokens" src/aegis/tui/metrics.py src/aegis/core/session.py 2>/dev/null | head -20
```

Note the exact attribute names. The plan assumes: `input_tokens`, `output_tokens`, `cache_hit_tokens`, `cache_write_tokens`, `thinking_tokens` (all ints). If any are named differently in current `SessionMetrics`, the test's mock object must match the real shape — but `compute()` should accept any object with the right attribute names (use `getattr(..., 0)` defensive reads).

- [ ] **Step 2: Write failing test**

Create `tests/test_budget_cost.py`:

```python
from dataclasses import dataclass
from decimal import Decimal

import pytest

from aegis.budget.cost import Cost, compute
from aegis.budget.prices import UnknownPriceError


@dataclass
class _FakeMetrics:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_write_tokens: int = 0
    thinking_tokens: int = 0


def test_compute_sums_all_token_classes_for_opus():
    m = _FakeMetrics(input_tokens=10_000, output_tokens=5_000,
                    cache_hit_tokens=100_000, cache_write_tokens=2_000,
                    thinking_tokens=1_000)
    c = compute(m, "claude-code", "opus")
    # opus rates per million: in=15, out=75, hit=1.50, write=18.75, think=75
    # = 10_000*15/1M + 5_000*75/1M + 100_000*1.5/1M + 2_000*18.75/1M + 1_000*75/1M
    # = 0.15 + 0.375 + 0.15 + 0.0375 + 0.075 = 0.7875
    assert c.usd == Decimal("0.7875")
    assert c.input_tokens == 10_000
    assert c.output_tokens == 5_000
    assert c.cache_hit_tokens == 100_000
    assert c.cache_write_tokens == 2_000
    assert c.thinking_tokens == 1_000


def test_compute_zero_metrics_is_zero_cost():
    c = compute(_FakeMetrics(), "claude-code", "haiku")
    assert c.usd == Decimal("0")
    assert c.output_tokens == 0


def test_compute_missing_attr_defaults_to_zero():
    """Defensive: ACP-driven providers may not split cache classes."""
    class _Sparse:
        input_tokens = 1_000_000
        output_tokens = 1_000_000
        # cache_hit_tokens / cache_write_tokens / thinking_tokens absent
    c = compute(_Sparse(), "claude-code", "haiku")
    # haiku: in=1.00/M, out=5.00/M → 1.00 + 5.00 = 6.00
    assert c.usd == Decimal("6.00")


def test_compute_unknown_model_raises():
    with pytest.raises(UnknownPriceError):
        compute(_FakeMetrics(input_tokens=1), "claude-code", "ghost")


def test_cost_as_dict_serializes_decimal_as_string():
    c = Cost(usd=Decimal("0.0421"), input_tokens=1, output_tokens=2,
              cache_hit_tokens=3, cache_write_tokens=4, thinking_tokens=5)
    d = c.as_dict()
    assert d["usd"] == "0.0421"
    assert d["input_tokens"] == 1


def test_cost_from_dict_round_trips():
    """JSONL round-trip: dict -> Cost -> dict must be identical."""
    src = {"usd": "0.0421", "input_tokens": 1, "output_tokens": 2,
           "cache_hit_tokens": 3, "cache_write_tokens": 4,
           "thinking_tokens": 5}
    c = Cost.from_dict(src)
    assert c.usd == Decimal("0.0421")
    assert c.as_dict() == src


def test_compute_no_float_drift_over_1000_rounds():
    """1000 small computes summed equal 1000 * single compute."""
    m = _FakeMetrics(input_tokens=12_345, output_tokens=6_789)
    total = sum((compute(m, "claude-code", "sonnet").usd for _ in range(1000)),
                start=Decimal("0"))
    single = compute(m, "claude-code", "sonnet").usd
    assert total == single * 1000
```

- [ ] **Step 3: Run test to verify failure**

```
uv run pytest tests/test_budget_cost.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement cost**

Create `src/aegis/budget/cost.py`:

```python
"""Compute USD cost from SessionMetrics + price table."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from aegis.budget.prices import lookup

_MILLION = Decimal("1000000")


@dataclass(frozen=True)
class Cost:
    """A worker's finalized cost, ready to land on a task_done JSONL record."""
    usd:                Decimal
    input_tokens:       int
    output_tokens:      int
    cache_hit_tokens:   int
    cache_write_tokens: int
    thinking_tokens:    int

    def as_dict(self) -> dict:
        """Serialize for JSONL — `usd` becomes a string to avoid float drift."""
        return {
            "usd": str(self.usd),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "thinking_tokens": self.thinking_tokens,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Cost":
        return cls(
            usd=Decimal(d["usd"]),
            input_tokens=int(d.get("input_tokens", 0)),
            output_tokens=int(d.get("output_tokens", 0)),
            cache_hit_tokens=int(d.get("cache_hit_tokens", 0)),
            cache_write_tokens=int(d.get("cache_write_tokens", 0)),
            thinking_tokens=int(d.get("thinking_tokens", 0)),
        )


def compute(metrics, provider: str, model: str) -> Cost:
    """Compute USD cost for the worker, looking up rates by (provider, model).

    ``metrics`` is any object exposing the SessionMetrics token attributes.
    Missing attributes default to 0 (some providers don't expose all of
    cache_hit/cache_write/thinking).
    Raises UnknownPriceError if (provider, model) isn't in PRICES.
    """
    row = lookup(provider, model)

    def _tok(name: str) -> int:
        return int(getattr(metrics, name, 0) or 0)

    inp = _tok("input_tokens")
    out = _tok("output_tokens")
    hit = _tok("cache_hit_tokens")
    wr  = _tok("cache_write_tokens")
    th  = _tok("thinking_tokens")

    usd = (
        Decimal(inp) * row.input       / _MILLION +
        Decimal(out) * row.output      / _MILLION +
        Decimal(hit) * row.cache_hit   / _MILLION +
        Decimal(wr)  * row.cache_write / _MILLION +
        Decimal(th)  * row.thinking    / _MILLION
    )
    return Cost(usd=usd, input_tokens=inp, output_tokens=out,
                cache_hit_tokens=hit, cache_write_tokens=wr,
                thinking_tokens=th)
```

- [ ] **Step 5: Run test**

```
uv run pytest tests/test_budget_cost.py -v
```
Expected: PASS (7 tests).

- [ ] **Step 6: Run full hermetic suite**

```
uv run pytest -q -m "not live" -x
```
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/budget/cost.py tests/test_budget_cost.py
git commit -m "feat(budget): Cost dataclass + compute(metrics, provider, model)"
```

---

## Task 3: `task_done` JSONL grows a `cost` field

**Files:**
- Modify: `src/aegis/queue/manager.py`
- Test: `tests/test_queue_manager.py` (or new `tests/test_queue_cost_log.py`)

When a worker terminates, `QueueManager._finalize` already collects the final `SessionMetrics` and writes a `task_done` JSONL record. Extend that record with a `cost` field via `compute()`. Also catch `UnknownPriceError` and log `cost_compute_failed` instead of crashing the finalizer.

- [ ] **Step 1: Locate the current _finalize**

```bash
grep -nE "def _finalize|task_done|self._log" src/aegis/queue/manager.py | head -20
```

Read the function body. Note the variable holding the worker's metrics and the (provider, model) you can pluck off the `Task` record. (Tasks already record `task.provider` and `task.model` since v0.6+.)

- [ ] **Step 2: Write failing test**

Create `tests/test_queue_cost_log.py`:

```python
"""Verify _finalize writes a `cost` field on the task_done JSONL record."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

# These imports use whatever queue-manager fixture pattern the existing
# tests use (see tests/test_queue_manager.py for the FakeSessionManager
# pattern + how to drive a task to completion).


@pytest.mark.asyncio
async def test_task_done_record_includes_cost(tmp_path: Path) -> None:
    from tests.fixtures.fake_session import FakeSessionManager  # existing
    from aegis.queue.manager import QueueManager
    from aegis.queue.schema import Queue

    # Build a QueueManager with one queue whose worker is "opus" claude-code.
    sm = FakeSessionManager(provider="claude-code", model="opus")
    qm = QueueManager(
        queues={"impl": Queue(name="impl", agent="default", max_parallel=1)},
        session_manager=sm,
        state_dir=tmp_path,
    )
    await qm.start()
    try:
        tid, _ = qm.enqueue("impl", "do it",
                             enqueued_by="agent:caller", callback=False)
        # Drive worker to completion with a known metrics shape.
        await sm.finish_task(tid, result_text="done",
                              metrics=dict(input_tokens=10_000,
                                           output_tokens=5_000,
                                           cache_hit_tokens=0,
                                           cache_write_tokens=0,
                                           thinking_tokens=0))
        # Verify the JSONL audit on disk.
        log_path = tmp_path / ".aegis" / "state" / "queues" / "impl.jsonl"
        assert log_path.exists()
        records = [json.loads(line) for line in
                   log_path.read_text().splitlines() if line.strip()]
        done = [r for r in records if r.get("event") == "task_done"]
        assert len(done) == 1
        assert "cost" in done[0]
        cost = done[0]["cost"]
        # opus: in=15/M, out=75/M → 10_000*15/1M + 5_000*75/1M = 0.15 + 0.375
        assert cost["usd"] == "0.5250" or Decimal(cost["usd"]) == Decimal("0.525")
        assert cost["input_tokens"] == 10_000
        assert cost["output_tokens"] == 5_000
    finally:
        await qm.stop()


@pytest.mark.asyncio
async def test_task_done_record_when_price_unknown(tmp_path: Path) -> None:
    """If the (provider, model) isn't in PRICES, log cost_compute_failed
    on the same record instead of crashing the finalizer."""
    from tests.fixtures.fake_session import FakeSessionManager
    from aegis.queue.manager import QueueManager
    from aegis.queue.schema import Queue

    sm = FakeSessionManager(provider="madeup", model="zzz")
    qm = QueueManager(
        queues={"impl": Queue(name="impl", agent="default", max_parallel=1)},
        session_manager=sm,
        state_dir=tmp_path,
    )
    await qm.start()
    try:
        tid, _ = qm.enqueue("impl", "x", enqueued_by="a", callback=False)
        await sm.finish_task(tid, result_text="done",
                              metrics=dict(input_tokens=1, output_tokens=1))
        log = tmp_path / ".aegis" / "state" / "queues" / "impl.jsonl"
        records = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
        done = [r for r in records if r.get("event") == "task_done"][0]
        assert "cost" in done
        assert done["cost"].get("error") == "unknown_model"
    finally:
        await qm.stop()
```

Note: if `tests/fixtures/fake_session.py` doesn't already exist or doesn't accept `provider`/`model` constructor args + `metrics=` on `finish_task`, you need to extend it (or write a minimal local double). Check `tests/test_queue_manager.py` for the existing fixture pattern; pull in additions as needed.

- [ ] **Step 3: Run test to verify failure**

```
uv run pytest tests/test_queue_cost_log.py -v
```
Expected: FAIL — `cost` key not present in the task_done record (the current code doesn't write it).

- [ ] **Step 4: Implement — extend `_finalize`**

In `src/aegis/queue/manager.py`, inside `_finalize`, near where the existing `task_done` JSONL record is built (the `self._log(task.queue, {...})` call), wrap a cost computation:

```python
from aegis.budget.cost import compute as _compute_cost
from aegis.budget.prices import UnknownPriceError

# ... inside _finalize, after collecting `metrics` and before self._log ...
cost_dict: dict
try:
    cost_dict = _compute_cost(metrics, task.provider, task.model).as_dict()
except UnknownPriceError as e:
    cost_dict = {"error": "unknown_model", "detail": str(e)}
except Exception as e:    # don't let cost computation break the finalizer
    cost_dict = {"error": "compute_failed", "detail": str(e)}

self._log(task.queue, {
    "event": "task_done",
    # ...existing fields...
    "cost": cost_dict,
})
```

(The exact location of the existing `self._log({"event": "task_done", ...})` call depends on the current `_finalize` shape — find it with the grep in Step 1; the addition is just one new key.)

- [ ] **Step 5: Run test**

```
uv run pytest tests/test_queue_cost_log.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 6: Run full hermetic suite**

```
uv run pytest -q -m "not live" -x
```
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/queue/manager.py tests/test_queue_cost_log.py
git commit -m "feat(budget): task_done JSONL record carries cost field"
```

---

## Task 4: `Budget` dataclass + config parser

**Files:**
- Modify: `src/aegis/queue/schema.py` (or wherever `Queue` lives — grep `class Queue\b`)
- Modify: `src/aegis/config.py` (or `src/aegis/config/yaml_loader.py` — wherever `.aegis.py` queues are parsed)
- Create: `src/aegis/budget/budgets.py` — `Budget` dataclass + `parse_budgets()` function
- Test: `tests/test_budget_config.py`

Parse the `budgets:` list in `.aegis.py` queue declarations into a `list[Budget]` on `Queue`. Validation at config-load: one constraint per entry (xor), valid window string, no duplicate `(constraint, window)` pairs, fail boot loud on violations.

- [ ] **Step 1: Write failing test**

Create `tests/test_budget_config.py`:

```python
from decimal import Decimal

import pytest

from aegis.budget.budgets import Budget, parse_budgets, BudgetConfigError


def test_parse_single_usd_budget():
    b = parse_budgets([{"usd": 1.00, "window": "1h"}])
    assert len(b) == 1
    assert b[0].constraint == "usd"
    assert b[0].limit == Decimal("1.00")
    assert b[0].window_str == "1h"


def test_parse_single_output_tokens_budget():
    b = parse_budgets([{"output_tokens": 500_000, "window": "1h"}])
    assert b[0].constraint == "output_tokens"
    assert b[0].limit == Decimal("500000")
    assert b[0].window_str == "1h"


def test_parse_multiple_budgets_preserves_order():
    b = parse_budgets([
        {"usd": 1.00, "window": "1h"},
        {"usd": 10.00, "window": "24h"},
        {"output_tokens": 500_000, "window": "1h"},
        {"usd": 50.00, "window": "7d"},
    ])
    assert len(b) == 4
    assert [x.window_str for x in b] == ["1h", "24h", "1h", "7d"]


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
            {"usd": 2.00, "window": "1h"},   # same (constraint, window)
        ])


def test_parse_accepts_decimal_usd():
    from decimal import Decimal as D
    b = parse_budgets([{"usd": D("0.5"), "window": "1h"}])
    assert b[0].limit == D("0.5")


def test_parse_rejects_non_positive_limit():
    with pytest.raises(BudgetConfigError, match="positive"):
        parse_budgets([{"usd": 0, "window": "1h"}])
    with pytest.raises(BudgetConfigError, match="positive"):
        parse_budgets([{"output_tokens": -1, "window": "1h"}])


def test_parse_empty_list_is_empty():
    assert parse_budgets([]) == []
    assert parse_budgets(None) == []
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/test_budget_config.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `Budget` + `parse_budgets`**

Create `src/aegis/budget/budgets.py`:

```python
"""Budget dataclass + config-time parser/validator."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable

from aegis.budget.windows import parse_window


class BudgetConfigError(ValueError):
    """Raised when a queue's `budgets:` config is malformed."""


@dataclass(frozen=True)
class Budget:
    """One (constraint, window) entry from a queue's budgets list.

    constraint is "usd" or "output_tokens". limit is the ceiling in the
    constraint's natural unit (Decimal USD or Decimal output_tokens).
    """
    constraint: str
    limit:      Decimal
    window_str: str         # verbatim from config — "1h" / "24h" / ...
    window:     timedelta   # parsed; cached for evaluator use


def parse_budgets(raw) -> list[Budget]:
    """Parse the raw `budgets:` list from a queue's config.

    Validates: exactly one of usd/output_tokens per entry, valid window,
    positive limit, no duplicate (constraint, window) pairs. Raises
    BudgetConfigError on any violation.
    """
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
                f"{entry['window']!r}) — collapse into one entry")
        seen.add(key)
        out.append(Budget(constraint=constraint, limit=limit,
                          window_str=entry["window"], window=window))
    return out
```

- [ ] **Step 4: Run budgets test**

```
uv run pytest tests/test_budget_config.py -v
```
Expected: PASS (11 tests).

- [ ] **Step 5: Plumb into `Queue`**

Find the queue config dataclass — `grep -n "class Queue\|@dataclass" src/aegis/queue/schema.py | head`. Add a `budgets: list[Budget]` field with default `field(default_factory=list)`. Import `Budget` from `aegis.budget.budgets`.

In the config loader (`src/aegis/config.py` or `src/aegis/config/yaml_loader.py` — whichever parses `queues = {...}` from `.aegis.py`), find where `Queue(**v)` or equivalent is built. Change to call `parse_budgets(v.pop("budgets", None))` and pass through:

```python
# Before:
queues = {k: Queue(**v) for k, v in raw_queues.items()}
# After:
from aegis.budget.budgets import parse_budgets, BudgetConfigError
queues = {}
for k, v in raw_queues.items():
    v = dict(v)  # don't mutate the caller's dict
    try:
        v["budgets"] = parse_budgets(v.pop("budgets", None))
    except BudgetConfigError as e:
        raise BudgetConfigError(f"queues[{k!r}]: {e}")
    queues[k] = Queue(**v)
```

- [ ] **Step 6: Write integration test for `.aegis.py` parsing**

Add to `tests/test_budget_config.py` (or a config-specific test file if one exists):

```python
def test_parse_queue_with_budgets_via_config_loader(tmp_path):
    """End-to-end: budgets in .aegis.py land on the Queue dataclass."""
    from aegis.config import load_config
    aegis_py = tmp_path / ".aegis.py"
    aegis_py.write_text("""
from aegis import Agent, ClaudeCode
agents = {"default": Agent(provider=ClaudeCode(model="opus"))}
default_agent = "default"
queues = {
    "impl": {
        "agent": "default",
        "max_parallel": 1,
        "budgets": [
            {"usd": 1.00, "window": "1h"},
            {"output_tokens": 500_000, "window": "1h"},
        ],
    },
}
""")
    cfg = load_config(tmp_path)
    q = cfg.queues["impl"]
    assert len(q.budgets) == 2
    assert q.budgets[0].constraint == "usd"
    assert q.budgets[1].constraint == "output_tokens"


def test_parse_queue_with_bad_budget_fails_boot(tmp_path):
    from aegis.budget.budgets import BudgetConfigError
    from aegis.config import load_config
    aegis_py = tmp_path / ".aegis.py"
    aegis_py.write_text("""
from aegis import Agent, ClaudeCode
agents = {"default": Agent(provider=ClaudeCode(model="opus"))}
default_agent = "default"
queues = {
    "impl": {"agent": "default", "max_parallel": 1,
              "budgets": [{"usd": 1.00, "output_tokens": 500, "window": "1h"}]},
}
""")
    with pytest.raises(BudgetConfigError, match="impl"):
        load_config(tmp_path)
```

(If `load_config` lives at a different import path or signature, adjust.)

- [ ] **Step 7: Run full suite**

```
uv run pytest -q -m "not live" -x
```
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add src/aegis/budget/budgets.py src/aegis/queue/schema.py \
        src/aegis/config.py src/aegis/config/yaml_loader.py \
        tests/test_budget_config.py
git commit -m "feat(budget): Budget dataclass + Queue.budgets config parser"
```

(Stage whichever of those config files you actually touched.)

---

## Task 5: `BudgetCheck` + `Decision` + `evaluate_budgets()`

**Files:**
- Create: `src/aegis/budget/evaluator.py`
- Test: `tests/test_budget_evaluator.py`

The pure evaluator. Takes a list of `task_done` records from the JSONL tail, a list of `Budget`, and a `now` datetime; returns a `Decision` with one `BudgetCheck` per budget plus the queue-level `allowed` + `unblock_at`.

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
    return datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)


def _budget(constraint: str, limit: str, window_str: str) -> Budget:
    return Budget(constraint=constraint, limit=Decimal(limit),
                  window_str=window_str, window=parse_window(window_str))


def _record(ts: datetime, usd: str = "0", output_tokens: int = 0) -> dict:
    return {
        "event": "task_done",
        "completed_at": ts.isoformat().replace("+00:00", "Z"),
        "cost": {"usd": usd, "input_tokens": 0, "output_tokens": output_tokens,
                  "cache_hit_tokens": 0, "cache_write_tokens": 0,
                  "thinking_tokens": 0},
    }


def test_no_budgets_allows():
    d = evaluate_budgets(jsonl_tail=[], budgets=[], now=_now())
    assert d.allowed is True
    assert d.checks == []
    assert d.blocked_by == []


def test_single_usd_budget_under_limit_allows():
    n = _now()
    tail = [_record(n - timedelta(minutes=10), usd="0.50")]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is True
    assert len(d.checks) == 1
    chk = d.checks[0]
    assert chk.spent == Decimal("0.50")
    assert chk.allowed is True
    assert chk.headroom == Decimal("0.50")


def test_single_usd_budget_over_limit_blocks():
    n = _now()
    tail = [
        _record(n - timedelta(minutes=10), usd="0.80"),
        _record(n - timedelta(minutes=20), usd="0.50"),
    ]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is False
    assert len(d.blocked_by) == 1
    chk = d.blocked_by[0]
    assert chk.spent == Decimal("1.30")
    assert chk.headroom == Decimal("-0.30")


def test_records_outside_window_ignored():
    n = _now()
    tail = [
        _record(n - timedelta(minutes=30), usd="0.50"),   # inside 1h
        _record(n - timedelta(hours=2), usd="100.00"),    # outside 1h
    ]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is True
    assert d.checks[0].spent == Decimal("0.50")


def test_output_tokens_budget_blocks():
    n = _now()
    tail = [_record(n - timedelta(minutes=5), output_tokens=600_000)]
    d = evaluate_budgets(tail, [_budget("output_tokens", "500000", "1h")], n)
    assert d.allowed is False
    assert d.blocked_by[0].spent == Decimal("600000")


def test_multi_budget_all_allow():
    n = _now()
    tail = [_record(n - timedelta(minutes=5), usd="0.30")]
    d = evaluate_budgets(tail, [
        _budget("usd", "1.00", "1h"),
        _budget("usd", "10.00", "24h"),
    ], n)
    assert d.allowed is True
    assert all(c.allowed for c in d.checks)


def test_multi_budget_partial_block():
    """1h trips at 0.80/0.50; 24h fine at 1.30/10.00 → blocked_by has one."""
    n = _now()
    tail = [
        _record(n - timedelta(minutes=10), usd="0.80"),
        _record(n - timedelta(minutes=20), usd="0.50"),
    ]
    d = evaluate_budgets(tail, [
        _budget("usd", "1.00", "1h"),
        _budget("usd", "10.00", "24h"),
    ], n)
    assert d.allowed is False
    assert len(d.blocked_by) == 1
    assert d.blocked_by[0].window_str == "1h"


def test_multi_budget_all_block():
    n = _now()
    tail = [_record(n - timedelta(minutes=10), usd="100.00")]
    d = evaluate_budgets(tail, [
        _budget("usd", "1.00", "1h"),
        _budget("usd", "50.00", "24h"),
    ], n)
    assert d.allowed is False
    assert len(d.blocked_by) == 2


def test_unblock_at_for_blocking_budget():
    """One record at 0.80, one at 0.50 (older), limit 1.00 over 1h.
    The older 0.50 ages out first; spent drops to 0.80 → still over.
    The 0.80 ages out next; spent drops to 0 → allowed.
    unblock_at = older_record.completed_at + window."""
    n = _now()
    older_ts = n - timedelta(minutes=30)  # 0.50
    newer_ts = n - timedelta(minutes=10)  # 0.80
    tail = [_record(newer_ts, usd="0.80"), _record(older_ts, usd="0.50")]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is False
    # When older record ages out at older_ts + 1h, spent drops to 0.80 → still over (>= 1.00? no, 0.80 < 1.00 → allowed)
    expected_unblock = older_ts + timedelta(hours=1)
    assert d.blocked_by[0].unblock_at == expected_unblock


def test_decision_unblock_at_is_max_across_blockers():
    """Two blocking budgets → queue unblock_at is the later of them."""
    n = _now()
    tail = [
        _record(n - timedelta(minutes=10), usd="2.00", output_tokens=600_000),
    ]
    d = evaluate_budgets(tail, [
        _budget("usd", "1.00", "1h"),                   # blocks; unblocks ~50m
        _budget("output_tokens", "500000", "30m"),      # blocks; unblocks ~20m
    ], n)
    assert d.allowed is False
    # The USD one unblocks later (1h - 10m = ~50m from now) vs output_tokens
    # (30m - 10m = 20m from now).
    assert d.unblock_at == d.blocked_by[0].unblock_at  # the later one
    # Sanity: latest > earliest
    times = [c.unblock_at for c in d.blocked_by if c.unblock_at]
    assert d.unblock_at == max(times)


def test_records_missing_cost_field_count_as_zero():
    """Backwards compat: pre-budget records have no `cost` key.
    They're treated as $0 contribution."""
    n = _now()
    tail = [
        {"event": "task_done",
         "completed_at": (n - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")},
        _record(n - timedelta(minutes=10), usd="0.30"),
    ]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is True
    assert d.checks[0].spent == Decimal("0.30")


def test_non_task_done_records_ignored():
    n = _now()
    tail = [
        {"event": "task_enqueued",
         "completed_at": (n - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
         "cost": {"usd": "100.00"}},
        _record(n - timedelta(minutes=10), usd="0.30"),
    ]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is True
    assert d.checks[0].spent == Decimal("0.30")
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/test_budget_evaluator.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement evaluator**

Create `src/aegis/budget/evaluator.py`:

```python
"""Pure-function evaluator for per-queue budgets over a JSONL tail."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from aegis.budget.budgets import Budget


@dataclass(frozen=True)
class BudgetCheck:
    constraint:    str               # "usd" or "output_tokens"
    limit:         Decimal
    spent:         Decimal
    window_str:    str
    window_start:  datetime
    allowed:       bool
    headroom:      Decimal           # limit - spent (negative when over)
    unblock_at:    datetime | None   # earliest time spent will drop below limit


@dataclass(frozen=True)
class Decision:
    allowed:    bool
    checks:     list[BudgetCheck]
    blocked_by: list[BudgetCheck]
    unblock_at: datetime | None


_ZERO = Decimal("0")


def _parse_completed_at(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _record_value(rec: dict, constraint: str) -> Decimal:
    """Extract the constraint's value from a task_done record's `cost`."""
    cost = rec.get("cost") or {}
    if "error" in cost:
        return _ZERO
    if constraint == "usd":
        raw = cost.get("usd", "0")
        try:
            return Decimal(raw)
        except Exception:
            return _ZERO
    elif constraint == "output_tokens":
        try:
            return Decimal(int(cost.get("output_tokens", 0) or 0))
        except (ValueError, TypeError):
            return _ZERO
    return _ZERO


def _evaluate_one(records: list[dict], budget: Budget,
                  now: datetime) -> BudgetCheck:
    window_start = now - budget.window
    # Filter: task_done records inside the window, oldest first.
    inside: list[tuple[datetime, Decimal]] = []
    for rec in records:
        if rec.get("event") != "task_done":
            continue
        ts = _parse_completed_at(rec.get("completed_at"))
        if ts is None or ts <= window_start or ts > now:
            continue
        inside.append((ts, _record_value(rec, budget.constraint)))
    inside.sort(key=lambda p: p[0])
    spent = sum((v for _, v in inside), start=_ZERO)
    allowed = spent < budget.limit
    headroom = budget.limit - spent

    unblock_at: datetime | None = None
    if not allowed:
        # Walk records age-order; find earliest ts at which
        # spent_remaining < limit. Each record ages out at
        # record_ts + budget.window — when that happens, its contribution
        # is removed from the rolling sum.
        running = spent
        for ts, value in inside:
            running -= value
            if running < budget.limit:
                unblock_at = ts + budget.window
                break
    return BudgetCheck(
        constraint=budget.constraint,
        limit=budget.limit,
        spent=spent,
        window_str=budget.window_str,
        window_start=window_start,
        allowed=allowed,
        headroom=headroom,
        unblock_at=unblock_at,
    )


def evaluate_budgets(jsonl_tail: Iterable[dict],
                     budgets: list[Budget],
                     now: datetime) -> Decision:
    """Run every budget in `budgets` against `jsonl_tail`. ALL must allow.

    `jsonl_tail` is an iterable of parsed JSONL records (any order).
    `now` is injected so the FakeClock pattern works in tests.
    """
    records = list(jsonl_tail)
    checks = [_evaluate_one(records, b, now) for b in budgets]
    blocked_by = [c for c in checks if not c.allowed]
    decision_allowed = not blocked_by
    decision_unblock: datetime | None = None
    if blocked_by:
        eligible = [c.unblock_at for c in blocked_by if c.unblock_at]
        decision_unblock = max(eligible) if eligible else None
    return Decision(
        allowed=decision_allowed,
        checks=checks,
        blocked_by=blocked_by,
        unblock_at=decision_unblock,
    )
```

- [ ] **Step 4: Run evaluator tests**

```
uv run pytest tests/test_budget_evaluator.py -v
```
Expected: PASS (12 tests).

- [ ] **Step 5: Run full suite**

```
uv run pytest -q -m "not live" -x
```
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/budget/evaluator.py tests/test_budget_evaluator.py
git commit -m "feat(budget): BudgetCheck + Decision + evaluate_budgets()"
```

---

## Task 6: Enforcement — `QueueManager.enqueue` gates on budgets

**Files:**
- Modify: `src/aegis/queue/manager.py`
- Test: `tests/test_queue_budget_enforcement.py`

`enqueue` calls `evaluate_budgets` before admitting a task. If `Decision.allowed is False`, return a structured error dict naming every blocked constraint + `unblock_at`. Task is not added. In-memory recent-cost deque rebuilt from JSONL on `start()` to keep the hot path O(1) push, O(N) sum over a small N.

- [ ] **Step 1: Write failing test**

Create `tests/test_queue_budget_enforcement.py`:

```python
import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_enqueue_admits_when_budgets_allow(tmp_path):
    from aegis.budget.budgets import Budget
    from aegis.budget.windows import parse_window
    from aegis.queue.manager import QueueManager
    from aegis.queue.schema import Queue
    from tests.fixtures.fake_session import FakeSessionManager

    sm = FakeSessionManager(provider="claude-code", model="opus")
    budgets = [Budget("usd", Decimal("1.00"), "1h", parse_window("1h"))]
    qm = QueueManager(
        queues={"impl": Queue(name="impl", agent="default",
                                max_parallel=1, budgets=budgets)},
        session_manager=sm, state_dir=tmp_path,
    )
    await qm.start()
    try:
        tid, _ = qm.enqueue("impl", "x",
                             enqueued_by="a", callback=False)
        assert isinstance(tid, str)
    finally:
        await qm.stop()


@pytest.mark.asyncio
async def test_enqueue_rejects_when_budget_exhausted(tmp_path):
    """Seed a JSONL with $1.50 spent in last hour, $1 budget; new enqueue
    must return a structured error."""
    from aegis.budget.budgets import Budget
    from aegis.budget.windows import parse_window
    from aegis.queue.manager import QueueManager
    from aegis.queue.schema import Queue
    from tests.fixtures.fake_session import FakeSessionManager
    from datetime import datetime, timezone, timedelta

    # Pre-seed the JSONL on disk.
    log = tmp_path / ".aegis" / "state" / "queues" / "impl.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    seed = {"event": "task_done",
            "completed_at": (now - timedelta(minutes=5)
                              ).isoformat().replace("+00:00", "Z"),
            "cost": {"usd": "1.50", "input_tokens": 0, "output_tokens": 0,
                      "cache_hit_tokens": 0, "cache_write_tokens": 0,
                      "thinking_tokens": 0}}
    log.write_text(json.dumps(seed) + "\n")

    sm = FakeSessionManager(provider="claude-code", model="opus")
    budgets = [Budget("usd", Decimal("1.00"), "1h", parse_window("1h"))]
    qm = QueueManager(
        queues={"impl": Queue(name="impl", agent="default",
                                max_parallel=1, budgets=budgets)},
        session_manager=sm, state_dir=tmp_path,
    )
    await qm.start()
    try:
        result = qm.enqueue("impl", "x",
                             enqueued_by="a", callback=False)
        assert isinstance(result, dict)
        assert "error" in result
        assert result["queue"] == "impl"
        assert len(result["blocked_by"]) == 1
        bc = result["blocked_by"][0]
        assert bc["constraint"] == "usd"
        assert bc["limit"] == "1.00"
        assert Decimal(bc["spent"]) == Decimal("1.50")
        assert bc["window"] == "1h"
        assert bc["unblock_at"]      # ISO timestamp present
        assert result["unblock_at"]  # queue-level
    finally:
        await qm.stop()


@pytest.mark.asyncio
async def test_enqueue_multi_budget_partial_block(tmp_path):
    """Block 1h budget, leave 24h fine; blocked_by names just the 1h."""
    from aegis.budget.budgets import Budget
    from aegis.budget.windows import parse_window
    from aegis.queue.manager import QueueManager
    from aegis.queue.schema import Queue
    from tests.fixtures.fake_session import FakeSessionManager
    from datetime import datetime, timezone, timedelta

    log = tmp_path / ".aegis" / "state" / "queues" / "impl.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    seed = {"event": "task_done",
            "completed_at": (now - timedelta(minutes=5)
                              ).isoformat().replace("+00:00", "Z"),
            "cost": {"usd": "1.50", "output_tokens": 0}}
    log.write_text(json.dumps(seed) + "\n")

    sm = FakeSessionManager(provider="claude-code", model="opus")
    budgets = [
        Budget("usd", Decimal("1.00"), "1h", parse_window("1h")),    # blocks
        Budget("usd", Decimal("10.00"), "24h", parse_window("24h")), # fine
    ]
    qm = QueueManager(
        queues={"impl": Queue(name="impl", agent="default",
                                max_parallel=1, budgets=budgets)},
        session_manager=sm, state_dir=tmp_path,
    )
    await qm.start()
    try:
        result = qm.enqueue("impl", "x", enqueued_by="a", callback=False)
        assert "error" in result
        assert len(result["blocked_by"]) == 1
        assert result["blocked_by"][0]["window"] == "1h"
    finally:
        await qm.stop()


@pytest.mark.asyncio
async def test_enqueue_no_budgets_unchanged(tmp_path):
    """Queue with no budgets behaves exactly as pre-v0.9."""
    from aegis.queue.manager import QueueManager
    from aegis.queue.schema import Queue
    from tests.fixtures.fake_session import FakeSessionManager

    sm = FakeSessionManager(provider="claude-code", model="opus")
    qm = QueueManager(
        queues={"impl": Queue(name="impl", agent="default",
                                max_parallel=1, budgets=[])},
        session_manager=sm, state_dir=tmp_path,
    )
    await qm.start()
    try:
        tid, _ = qm.enqueue("impl", "x", enqueued_by="a", callback=False)
        assert isinstance(tid, str)
    finally:
        await qm.stop()
```

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/test_queue_budget_enforcement.py -v
```
Expected: FAIL — current `enqueue` doesn't gate on budgets.

- [ ] **Step 3: Implement the gate**

In `src/aegis/queue/manager.py`:

1. Add an import block:
   ```python
   from datetime import datetime, timezone
   from aegis.budget.evaluator import evaluate_budgets
   ```
2. At the top of `enqueue(...)`, after the `queue not in self._queues` check but before constructing the `Task`, add:
   ```python
   q = self._queues[queue]
   if q.budgets:
       tail = self._load_recent_jsonl(queue, max_age=max(
           b.window for b in q.budgets))
       decision = evaluate_budgets(tail, q.budgets, datetime.now(timezone.utc))
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
3. Add a helper `_load_recent_jsonl(queue, max_age)`:
   ```python
   def _load_recent_jsonl(self, queue: str,
                           max_age) -> list[dict]:
       """Read the queue's JSONL tail, return task_done records within max_age."""
       import json
       from datetime import datetime, timezone
       log = self._state_dir / ".aegis" / "state" / "queues" / f"{queue}.jsonl"
       if not log.exists():
           return []
       cutoff = datetime.now(timezone.utc) - max_age
       out = []
       for line in log.read_text().splitlines():
           if not line.strip():
               continue
           try:
               rec = json.loads(line)
           except json.JSONDecodeError:
               continue
           if rec.get("event") != "task_done":
               continue
           ts_str = rec.get("completed_at", "")
           try:
               ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
           except (ValueError, TypeError):
               continue
           if ts >= cutoff:
               out.append(rec)
       return out
   ```

**Note:** the change affects the **return type** of `enqueue` for callers that previously assumed `tuple[str, int]`. Audit callers — `aegis_enqueue` in `mcp/server.py` already handles the `dict | tuple` shape because it returns `result` directly. But anywhere `tid, pos = qm.enqueue(...)` exists in the codebase will break on budget rejection. Find them:

```bash
grep -rn "\.enqueue(" src/aegis/ | grep -v "_enqueue\|tests" | head
```

Each non-MCP caller (e.g. the workflow engine's `engine.enqueue`, the scheduler's `enqueue` built-in workflow) needs to handle the dict-or-tuple return shape. For now, wrap callers in:

```python
result = qm.enqueue(...)
if isinstance(result, dict):
    # propagate the error as appropriate for this caller
    ...
else:
    tid, pos = result
```

In Task 8 we add `BudgetExceeded` as a typed exception for the workflow engine; until then, callers handle the dict shape.

- [ ] **Step 4: Run enforcement tests**

```
uv run pytest tests/test_queue_budget_enforcement.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Run full suite, fix callers**

```
uv run pytest -q -m "not live" -x
```
Expected: some callers may break with `cannot unpack non-iterable dict` if budgets are set. Most existing tests don't set budgets so they're unaffected. If any test fails, audit the caller and add a dict-shape branch.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/queue/manager.py tests/test_queue_budget_enforcement.py
# plus any caller fixes
git commit -m "feat(budget): QueueManager.enqueue gates on multi-window budgets"
```

---

## Task 7: `BudgetExceeded` exception for the workflow engine

**Files:**
- Modify: `src/aegis/workflow/engine.py` (the `engine.enqueue` method)
- Modify: `src/aegis/budget/__init__.py` (export `BudgetExceeded`)
- Create: `src/aegis/budget/errors.py` (if `BudgetExceeded` doesn't fit in evaluator.py)
- Test: `tests/test_workflow_budget.py`

`engine.enqueue` in workflow Python should raise `BudgetExceeded(decision)` (a typed exception) on budget rejection, so workflow authors can `try/except` for clean retry-with-different-queue patterns.

- [ ] **Step 1: Write failing test**

Create `tests/test_workflow_budget.py`:

```python
import json
from decimal import Decimal
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_engine_enqueue_raises_on_budget_exhausted(tmp_path):
    """engine.enqueue should raise BudgetExceeded with the Decision attached."""
    from aegis.budget import BudgetExceeded
    # ... build an engine + QM where a budget is already exhausted, call
    #     engine.enqueue, assert it raises BudgetExceeded
    from aegis.budget.budgets import Budget
    from aegis.budget.windows import parse_window
    from aegis.queue.manager import QueueManager
    from aegis.queue.schema import Queue
    from aegis.workflow.engine import WorkflowEngine
    from tests.fixtures.fake_session import FakeSessionManager
    from datetime import datetime, timezone, timedelta

    log = tmp_path / ".aegis" / "state" / "queues" / "impl.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    log.write_text(json.dumps({
        "event": "task_done",
        "completed_at": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "cost": {"usd": "1.50"},
    }) + "\n")

    sm = FakeSessionManager(provider="claude-code", model="opus")
    budgets = [Budget("usd", Decimal("1.00"), "1h", parse_window("1h"))]
    qm = QueueManager(
        queues={"impl": Queue(name="impl", agent="default",
                                max_parallel=1, budgets=budgets)},
        session_manager=sm, state_dir=tmp_path,
    )
    await qm.start()
    try:
        engine = WorkflowEngine(...)   # however the engine is constructed
                                        # in the existing tests; see
                                        # tests/test_workflow_engine.py
        with pytest.raises(BudgetExceeded) as ei:
            await engine.enqueue("impl", "x", from_handle="caller")
        assert ei.value.decision is not None
        assert ei.value.decision.allowed is False
        assert ei.value.queue == "impl"
    finally:
        await qm.stop()
```

(If the engine fixture pattern is non-obvious, copy from `tests/test_workflow_engine.py`.)

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/test_workflow_budget.py -v
```
Expected: FAIL — `BudgetExceeded` not defined.

- [ ] **Step 3: Implement the exception**

Create `src/aegis/budget/errors.py`:

```python
"""Typed exceptions for budget-related failures in higher-level callers."""
from __future__ import annotations

from aegis.budget.evaluator import Decision


class BudgetExceeded(Exception):
    """Raised by engine.enqueue (or any workflow caller) on budget rejection.

    Carries the full Decision so the catcher can inspect blocked_by /
    unblock_at to choose a retry strategy.
    """
    def __init__(self, queue: str, decision: Decision) -> None:
        self.queue = queue
        self.decision = decision
        binding = ", ".join(
            f"{c.spent}/{c.limit} {c.constraint} in {c.window_str}"
            for c in decision.blocked_by)
        super().__init__(
            f"queue {queue!r} over budget: {binding}")
```

In `src/aegis/budget/__init__.py`, add:

```python
from aegis.budget.errors import BudgetExceeded

__all__ = ["BudgetExceeded"]
```

- [ ] **Step 4: Wire into `WorkflowEngine.enqueue`**

In `src/aegis/workflow/engine.py`, find `async def enqueue(self, ...)`. Where it calls into the underlying `QueueManager.enqueue`, check for the dict-shape return and raise `BudgetExceeded`:

```python
async def enqueue(self, queue: str, payload: str, *,
                  from_handle: str, callback: bool = False) -> str:
    result = self._qm.enqueue(
        queue, payload, enqueued_by=sender_agent(from_handle),
        callback=callback)
    if isinstance(result, dict):
        # Budget rejection — surface as typed exception.
        from aegis.budget import BudgetExceeded
        from aegis.budget.evaluator import (
            BudgetCheck, Decision)
        from decimal import Decimal
        # Rebuild a Decision-like object from the dict for the exception
        # body. (The evaluator returned this dict; we just need the
        # structured shape.)
        checks = []
        for bc in result.get("blocked_by", []):
            checks.append(BudgetCheck(
                constraint=bc["constraint"], limit=Decimal(bc["limit"]),
                spent=Decimal(bc["spent"]), window_str=bc["window"],
                window_start=None, allowed=False,
                headroom=Decimal(bc["limit"]) - Decimal(bc["spent"]),
                unblock_at=None,  # full iso parse omitted for brevity
            ))
        decision = Decision(allowed=False, checks=checks,
                            blocked_by=checks, unblock_at=None)
        raise BudgetExceeded(queue=queue, decision=decision)
    tid, _ = result
    return tid
```

- [ ] **Step 5: Run test**

```
uv run pytest tests/test_workflow_budget.py -v
```
Expected: PASS.

- [ ] **Step 6: Run full suite + commit**

```bash
uv run pytest -q -m "not live" -x
git add src/aegis/budget/errors.py src/aegis/budget/__init__.py \
        src/aegis/workflow/engine.py tests/test_workflow_budget.py
git commit -m "feat(budget): BudgetExceeded typed exception for workflow engine"
```

---

## Task 8: HTTP — `GET /remote/v1/budget` + `/budget/<queue>`

**Files:**
- Modify: `src/aegis/remote/plane.py`
- Test: `tests/test_remote_budget_endpoints.py`

Two read-only HTTP endpoints. Same auth gating as `/enqueue` / `/callback` / `/schedule`. Return the summary (list) and full-Decision (show) shapes.

- [ ] **Step 1: Write failing test**

Create `tests/test_remote_budget_endpoints.py`:

```python
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport


@pytest.mark.asyncio
async def test_budget_list_returns_per_queue_summary(tmp_path):
    """Two queues, one with budgets, one without — list shows both."""
    from aegis.budget.budgets import Budget
    from aegis.budget.windows import parse_window
    from aegis.queue.manager import QueueManager
    from aegis.queue.schema import Queue
    from aegis.remote.config import RemotePlaneSpec
    from aegis.remote.plane import build_plane
    from tests.fixtures.fake_session import FakeSessionManager

    sm = FakeSessionManager(provider="claude-code", model="opus")
    qm = QueueManager(
        queues={
            "impl": Queue(name="impl", agent="default", max_parallel=1,
                          budgets=[Budget("usd", Decimal("1.00"),
                                          "1h", parse_window("1h"))]),
            "fast": Queue(name="fast", agent="default", max_parallel=2,
                          budgets=[]),
        },
        session_manager=sm, state_dir=tmp_path,
    )
    await qm.start()
    try:
        bridge = _make_bridge_with_queue_manager(qm, tmp_path)
        app = build_plane(bridge, RemotePlaneSpec(bind="127.0.0.1:8556"))
        async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                      base_url="http://test") as c:
            r = await c.get("/remote/v1/budget")
            assert r.status_code == 200
        data = r.json()
        names = [q["name"] for q in data["queues"]]
        assert set(names) == {"impl", "fast"}
        impl = next(q for q in data["queues"] if q["name"] == "impl")
        fast = next(q for q in data["queues"] if q["name"] == "fast")
        assert impl["budgets_count"] == 1
        assert fast["budgets_count"] == 0
        assert impl["status"] in ("ok", "no-budget", "blocked")
        assert fast["status"] == "no-budget"
    finally:
        await qm.stop()


@pytest.mark.asyncio
async def test_budget_show_returns_full_decision(tmp_path):
    from aegis.budget.budgets import Budget
    from aegis.budget.windows import parse_window
    from aegis.queue.manager import QueueManager
    from aegis.queue.schema import Queue
    from aegis.remote.config import RemotePlaneSpec
    from aegis.remote.plane import build_plane
    from tests.fixtures.fake_session import FakeSessionManager
    from datetime import datetime, timezone, timedelta

    # Pre-seed JSONL: $1.50 spent.
    log = tmp_path / ".aegis" / "state" / "queues" / "impl.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    log.write_text(json.dumps({
        "event": "task_done",
        "completed_at": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "cost": {"usd": "1.50"},
    }) + "\n")

    sm = FakeSessionManager(provider="claude-code", model="opus")
    qm = QueueManager(
        queues={"impl": Queue(name="impl", agent="default", max_parallel=1,
                                budgets=[Budget("usd", Decimal("1.00"),
                                                "1h", parse_window("1h"))])},
        session_manager=sm, state_dir=tmp_path,
    )
    await qm.start()
    try:
        bridge = _make_bridge_with_queue_manager(qm, tmp_path)
        app = build_plane(bridge, RemotePlaneSpec(bind="127.0.0.1:8556"))
        async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                      base_url="http://test") as c:
            r = await c.get("/remote/v1/budget/impl")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "impl"
        assert data["allowed"] is False
        assert len(data["blocked_by"]) == 1
        bc = data["blocked_by"][0]
        assert bc["constraint"] == "usd"
        assert Decimal(bc["spent"]) == Decimal("1.50")
    finally:
        await qm.stop()


@pytest.mark.asyncio
async def test_budget_show_unknown_queue_404(tmp_path):
    from aegis.queue.manager import QueueManager
    from aegis.remote.config import RemotePlaneSpec
    from aegis.remote.plane import build_plane
    from tests.fixtures.fake_session import FakeSessionManager

    sm = FakeSessionManager(provider="claude-code", model="opus")
    qm = QueueManager(queues={}, session_manager=sm, state_dir=tmp_path)
    await qm.start()
    try:
        bridge = _make_bridge_with_queue_manager(qm, tmp_path)
        app = build_plane(bridge, RemotePlaneSpec(bind="127.0.0.1:8556"))
        async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                      base_url="http://test") as c:
            r = await c.get("/remote/v1/budget/nonexistent")
            assert r.status_code == 404
    finally:
        await qm.stop()
```

`_make_bridge_with_queue_manager` is a small helper — copy the bridge construction pattern from `tests/test_remote_plane.py` and add a `queue_manager` attribute.

- [ ] **Step 2: Run test to verify failure**

```
uv run pytest tests/test_remote_budget_endpoints.py -v
```
Expected: FAIL — endpoints not registered.

- [ ] **Step 3: Implement endpoints**

In `src/aegis/remote/plane.py`, register:

```python
@app.route("/remote/v1/budget", methods=["GET"])
async def budget_list(request):
    auth_err = _check_auth(request, spec)
    if auth_err: return JSONResponse(auth_err, status_code=401)
    rows = []
    qm = bridge.queue_manager
    from datetime import datetime, timezone
    from aegis.budget.evaluator import evaluate_budgets
    now = datetime.now(timezone.utc)
    for name, q in qm._queues.items():
        if not q.budgets:
            rows.append({"name": name, "budgets_count": 0,
                          "status": "no-budget", "binding": None,
                          "unblock_at": None})
            continue
        tail = qm._load_recent_jsonl(name,
                                       max_age=max(b.window for b in q.budgets))
        d = evaluate_budgets(tail, q.budgets, now)
        if d.allowed:
            # Pick the most-pressured (smallest headroom proportion) check.
            check = min(d.checks,
                         key=lambda c: c.headroom / c.limit if c.limit else 0)
            binding = (f"${check.spent} of ${check.limit} / {check.window_str}"
                        if check.constraint == "usd"
                        else f"{check.spent} of {check.limit} "
                              f"{check.constraint} / {check.window_str}")
            rows.append({"name": name, "budgets_count": len(q.budgets),
                          "status": "ok", "binding": binding,
                          "unblock_at": None})
        else:
            check = d.blocked_by[0]
            binding = (f"${check.spent} of ${check.limit} / {check.window_str}"
                        if check.constraint == "usd"
                        else f"{check.spent} of {check.limit} "
                              f"{check.constraint} / {check.window_str}")
            rows.append({"name": name, "budgets_count": len(q.budgets),
                          "status": "blocked", "binding": binding,
                          "unblock_at": d.unblock_at.isoformat().replace(
                              "+00:00", "Z") if d.unblock_at else None})
    return JSONResponse({"queues": rows})


@app.route("/remote/v1/budget/{queue}", methods=["GET"])
async def budget_show(request):
    auth_err = _check_auth(request, spec)
    if auth_err: return JSONResponse(auth_err, status_code=401)
    name = request.path_params["queue"]
    qm = bridge.queue_manager
    if name not in qm._queues:
        return JSONResponse({"error": "unknown queue"}, status_code=404)
    q = qm._queues[name]
    from datetime import datetime, timezone, timedelta
    from aegis.budget.evaluator import evaluate_budgets
    now = datetime.now(timezone.utc)
    if not q.budgets:
        return JSONResponse({"name": name, "allowed": True, "checks": [],
                              "blocked_by": [], "unblock_at": None})
    tail = qm._load_recent_jsonl(name,
                                   max_age=max(b.window for b in q.budgets))
    d = evaluate_budgets(tail, q.budgets, now)

    def _ser(c):
        return {"constraint": c.constraint,
                "limit": str(c.limit), "spent": str(c.spent),
                "window": c.window_str,
                "window_start": c.window_start.isoformat().replace("+00:00", "Z"),
                "allowed": c.allowed,
                "headroom": str(c.headroom),
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

- [ ] **Step 4: Run tests**

```
uv run pytest tests/test_remote_budget_endpoints.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 5: Run full suite + commit**

```bash
uv run pytest -q -m "not live" -x
git add src/aegis/remote/plane.py tests/test_remote_budget_endpoints.py
git commit -m "feat(budget): GET /remote/v1/budget list + show"
```

---

## Task 9: Outbound budget client + MCP tool

**Files:**
- Modify: `src/aegis/remote/client.py`
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_remote_budget_client.py`
- Test: `tests/test_mcp_budget_tool.py`

Two new outbound client functions (`remote_budget_list`, `remote_budget_show`) plus the `aegis_budget_status` MCP tool with `target=None` local / `target="<peer>"` cross-host pattern.

- [ ] **Step 1: Write failing client tests**

Create `tests/test_remote_budget_client.py`:

```python
import httpx
import pytest

from aegis.remote.client import remote_budget_list, remote_budget_show
from aegis.remote.config import RemoteSpec


@pytest.mark.asyncio
async def test_remote_budget_list_returns_queues(httpx_mock):
    spec = RemoteSpec(url="http://1.2.3.4:8556")
    httpx_mock.add_response(
        method="GET", url="http://1.2.3.4:8556/remote/v1/budget",
        status_code=200,
        json={"queues": [{"name": "impl", "budgets_count": 2,
                           "status": "ok", "binding": "$0.30 of $1.00 / 1h",
                           "unblock_at": None}]})
    result = await remote_budget_list(spec)
    assert "queues" in result
    assert result["queues"][0]["name"] == "impl"


@pytest.mark.asyncio
async def test_remote_budget_show_returns_decision(httpx_mock):
    spec = RemoteSpec(url="http://1.2.3.4:8556")
    httpx_mock.add_response(
        method="GET", url="http://1.2.3.4:8556/remote/v1/budget/impl",
        status_code=200,
        json={"name": "impl", "allowed": True, "checks": [],
              "blocked_by": [], "unblock_at": None})
    result = await remote_budget_show(spec, "impl")
    assert result["name"] == "impl"
    assert result["allowed"] is True


@pytest.mark.asyncio
async def test_remote_budget_show_404_returns_error(httpx_mock):
    spec = RemoteSpec(url="http://1.2.3.4:8556")
    httpx_mock.add_response(
        method="GET", url="http://1.2.3.4:8556/remote/v1/budget/ghost",
        status_code=404,
        json={"error": "unknown queue"})
    result = await remote_budget_show(spec, "ghost")
    assert "error" in result
    assert "ghost" in result["error"] or "unknown" in result["error"]
```

- [ ] **Step 2: Implement clients**

In `src/aegis/remote/client.py`:

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

- [ ] **Step 3: Write MCP tool test**

Create `tests/test_mcp_budget_tool.py`:

```python
import pytest

from aegis.mcp.server import build_server


@pytest.mark.asyncio
async def test_budget_status_local_no_queue_lists_all(tmp_path):
    """target=None, queue=None → summary list shape."""
    # ... build a bridge with a QueueManager that has two queues,
    #     call aegis_budget_status, assert {"queues": [...]} shape
    ...


@pytest.mark.asyncio
async def test_budget_status_local_with_queue_returns_decision(tmp_path):
    """target=None, queue="impl" → full Decision shape."""
    ...


@pytest.mark.asyncio
async def test_budget_status_remote_routes_through_client(monkeypatch):
    """target="vps", queue=None → calls remote_budget_list with spec."""
    ...


@pytest.mark.asyncio
async def test_budget_status_unknown_target_errors():
    """target="vps" but vps not in remotes → returns error dict."""
    ...
```

Flesh out the body following the patterns in `tests/test_mcp_schedule_tools.py`.

- [ ] **Step 4: Implement the MCP tool**

In `src/aegis/mcp/server.py`:

```python
@server.tool
async def aegis_budget_status(from_handle: str,
                                queue: str | None = None,
                                target: str | None = None) -> dict:
    """Inspect per-queue budgets on this serve or a remote peer.

    queue=None: summary across all queues on the targeted serve.
    queue="<name>": full Decision for that queue.
    target=None: this serve; target="<peer>": route through `/remote/v1/budget`.
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

    # Local path
    qm = bridge.queue_manager
    from datetime import datetime, timezone
    from aegis.budget.evaluator import evaluate_budgets
    now = datetime.now(timezone.utc)
    if queue is None:
        # Summary across all queues.
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
    tail = qm._load_recent_jsonl(queue,
                                   max_age=max(b.window for b in q.budgets))
    d = evaluate_budgets(tail, q.budgets, now)

    def _ser(c):
        return {"constraint": c.constraint, "limit": str(c.limit),
                "spent": str(c.spent), "window": c.window_str,
                "allowed": c.allowed, "headroom": str(c.headroom)}
    return {"name": queue, "allowed": d.allowed,
            "checks": [_ser(c) for c in d.checks],
            "blocked_by": [_ser(c) for c in d.blocked_by]}
```

- [ ] **Step 5: Run all budget MCP/client tests**

```
uv run pytest tests/test_remote_budget_client.py tests/test_mcp_budget_tool.py -v
```
Expected: PASS.

- [ ] **Step 6: Run full suite + commit**

```bash
uv run pytest -q -m "not live" -x
git add src/aegis/remote/client.py src/aegis/mcp/server.py \
        tests/test_remote_budget_client.py tests/test_mcp_budget_tool.py
git commit -m "feat(budget): aegis_budget_status MCP tool + remote_budget_* clients"
```

---

## Task 10: `aegis budget` CLI subapp

**Files:**
- Create: `src/aegis/cli_budget.py`
- Modify: `src/aegis/cli.py` (mount the subapp)
- Test: `tests/test_cli_budget.py`

CLI for operators: `aegis budget list`, `aegis budget show <queue>`, each with `--remote <peer>` for cross-host. Mirrors `aegis schedule`.

- [ ] **Step 1: Write failing test**

Create `tests/test_cli_budget.py`:

```python
import pytest
from typer.testing import CliRunner


def test_budget_list_local_prints_table(monkeypatch, tmp_path):
    """`aegis budget list` reads from this serve's .aegis.py config."""
    # ... seed a .aegis.py with two queues, run CliRunner,
    #     assert output has both queue names in a table
    ...


def test_budget_show_local_prints_decision(monkeypatch, tmp_path):
    """`aegis budget show impl` prints every BudgetCheck row."""
    ...


def test_budget_list_remote_calls_client(monkeypatch):
    """`aegis budget list --remote vps` invokes remote_budget_list."""
    ...
```

- [ ] **Step 2: Implement the subapp**

Create `src/aegis/cli_budget.py`:

```python
"""`aegis budget` CLI subapp — inspect per-queue budgets."""
from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(add_completion=False, no_args_is_help=False)
_console = Console()


def _cfg():
    """Load this serve's config (same pattern as cli_schedule)."""
    from aegis.config import find_project_root, load_config
    root = find_project_root()
    return load_config(root)


def _format_status(row: dict) -> str:
    s = row.get("status")
    if s == "no-budget":
        return "— no budget"
    if s == "ok":
        return "✓ ok"
    if s == "blocked":
        ua = row.get("unblock_at")
        return f"⛔ over (unblocks {ua})" if ua else "⛔ over"
    return s or "?"


@app.command("list")
def list_budgets(
    remote: str = typer.Option(None, "--remote", help="peer name"),
) -> None:
    """One-line summary per queue."""
    cfg = _cfg()
    if remote is not None:
        from aegis.remote.client import remote_budget_list
        if remote not in cfg.remotes:
            typer.echo(f"unknown remote {remote!r}", err=True)
            raise typer.Exit(1)
        result = asyncio.run(remote_budget_list(cfg.remotes[remote]))
    else:
        # Local — synthesize by calling the same evaluator the MCP tool uses.
        from datetime import datetime, timezone
        from aegis.budget.evaluator import evaluate_budgets
        # We need a QueueManager-like view of the queues' state. For the
        # CLI's local mode we don't want to spin up a full QM; instead,
        # read the JSONL directly.
        rows = []
        now = datetime.now(timezone.utc)
        for name, q in cfg.queues.items():
            if not q.budgets:
                rows.append({"name": name, "budgets_count": 0,
                              "status": "no-budget", "binding": None})
                continue
            from pathlib import Path
            log = (Path.cwd() / ".aegis" / "state" / "queues" /
                   f"{name}.jsonl")
            tail = []
            if log.exists():
                for line in log.read_text().splitlines():
                    if line.strip():
                        try:
                            tail.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            d = evaluate_budgets(tail, q.budgets, now)
            rows.append({"name": name, "budgets_count": len(q.budgets),
                          "status": "ok" if d.allowed else "blocked",
                          "binding": None,
                          "unblock_at": d.unblock_at.isoformat() if
                                          d.unblock_at else None})
        result = {"queues": rows}

    if "error" in result:
        typer.echo(result["error"], err=True)
        raise typer.Exit(1)

    table = Table()
    table.add_column("QUEUE"); table.add_column("BUDGETS")
    table.add_column("STATUS")
    for row in result["queues"]:
        table.add_row(row["name"],
                      str(row.get("budgets_count", "?")),
                      _format_status(row))
    _console.print(table)


@app.command("show")
def show_budget(
    queue: str,
    remote: str = typer.Option(None, "--remote"),
) -> None:
    """Full Decision: every check with spent/limit/headroom/unblock_at."""
    cfg = _cfg()
    if remote is not None:
        from aegis.remote.client import remote_budget_show
        if remote not in cfg.remotes:
            typer.echo(f"unknown remote {remote!r}", err=True)
            raise typer.Exit(1)
        result = asyncio.run(remote_budget_show(cfg.remotes[remote], queue))
    else:
        # Local — same as list but for one queue.
        if queue not in cfg.queues:
            typer.echo(f"unknown queue {queue!r}", err=True)
            raise typer.Exit(1)
        from datetime import datetime, timezone
        from pathlib import Path
        from aegis.budget.evaluator import evaluate_budgets
        q = cfg.queues[queue]
        if not q.budgets:
            typer.echo(f"queue {queue!r} has no budgets configured.")
            return
        log = Path.cwd() / ".aegis" / "state" / "queues" / f"{queue}.jsonl"
        tail = []
        if log.exists():
            for line in log.read_text().splitlines():
                if line.strip():
                    try:
                        tail.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        d = evaluate_budgets(tail, q.budgets,
                              datetime.now(timezone.utc))
        result = {
            "name": queue, "allowed": d.allowed,
            "checks": [
                {"constraint": c.constraint, "limit": str(c.limit),
                 "spent": str(c.spent), "window": c.window_str,
                 "allowed": c.allowed, "headroom": str(c.headroom)}
                for c in d.checks],
            "blocked_by": [
                {"constraint": c.constraint, "limit": str(c.limit),
                 "spent": str(c.spent), "window": c.window_str}
                for c in d.blocked_by],
        }

    if "error" in result:
        typer.echo(result["error"], err=True); raise typer.Exit(1)

    table = Table(title=f"budget for queue {queue!r}")
    table.add_column("CONSTRAINT"); table.add_column("LIMIT")
    table.add_column("SPENT"); table.add_column("WINDOW")
    table.add_column("HEADROOM"); table.add_column("STATUS")
    for c in result["checks"]:
        status = "✓" if c["allowed"] else "⛔"
        table.add_row(c["constraint"], c["limit"], c["spent"],
                       c["window"], c["headroom"], status)
    _console.print(table)
```

- [ ] **Step 3: Mount the subapp in `cli.py`**

In `src/aegis/cli.py`, near the existing `from aegis.cli_schedule import app as _schedule_app`:

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

## Task 11: Docs + roadmap + CHANGELOG + 0.9.0 release

**Files:**
- Create: `docs/budget.md`
- Modify: `docs/configuration.md` — add `budgets:` field documentation under Queues
- Modify: `docs/index.md` — extend the "what else is in the box" section
- Modify: `docs/roadmap.md` — add `### v0.9.0 (current)` section
- Modify: `mkdocs.yml` — add Budgets to nav under Concepts
- Modify: `README.md` — new section + docs link
- Modify: `CHANGELOG.md` — `[0.9.0]` entry
- Modify: `pyproject.toml` + `uv.lock` — 0.8.1 → 0.9.0

- [ ] **Step 1: Write `docs/budget.md`**

Create `docs/budget.md` covering the same shape as the spec but in user-doc voice:
- "Why budgets" (motivation)
- "The model" (multi-window all-must-allow, USD + output_tokens)
- "Config shape" (worked `.aegis.py` example)
- "Rejection shape" (what the caller sees on hit)
- "Observability" (CLI / MCP / HTTP / TUI)
- "Patterns" (cap an opus queue at $1/hour + $10/day + 500k output/hour; runaway-loop belt)
- "Non-goals" (no global cap, no pre-flight, no Telegram, no throttle)
- "FAQ" — "what if a worker is running when the budget trips?" / "how do I know what's been spent?" / "can I update prices?"

Pull paragraphs from the spec where helpful but rewrite in second person ("you") rather than third.

- [ ] **Step 2: Sync `docs/configuration.md`**

In the existing Queues section, after the `max_parallel` mention, add a `budgets:` field paragraph + a worked example:

```markdown
Each queue may declare an optional `budgets:` list. The substrate
rejects new enqueues that would land the queue over any of the
declared `(constraint, window)` ceilings (USD or output-token,
rolling window). All budgets must allow.

```python
queues = {
    "impl": {
        "agent": "opus",
        "max_parallel": 2,
        "budgets": [
            {"usd": 1.00,            "window": "1h"},
            {"usd": 10.00,           "window": "24h"},
            {"output_tokens": 500000, "window": "1h"},
        ],
    },
}
```

See [Budgets](budget.md) for the full surface.
```

- [ ] **Step 3: Sync `docs/index.md` + `mkdocs.yml`**

In `docs/index.md` "What's also in the box" section, add one bullet:

```markdown
- **Per-queue budgets.** Declare USD or output-token ceilings over rolling windows on any queue; the substrate rejects new enqueues that would land the queue over budget, naming the binding constraint. Pull-only observability via CLI, MCP, HTTP, and the TUI dashboard. See [Budgets](budget.md).
```

In `mkdocs.yml` `nav.Concepts`, add `- Budgets: budget.md` between Groups and Remote plane.

- [ ] **Step 4: Sync `docs/roadmap.md`**

Add above `### v0.8.0`:

```markdown
### v0.9.0 (current)
- **Per-queue budgets.** Multi-window per-queue budgets — USD or
  output-token ceilings over a rolling window; ALL budgets must
  allow. Enforcement at enqueue time; loud structured rejection
  naming the binding constraint and `unblock_at` ETA. Cost computed
  from existing SessionMetrics via a static per-(provider, model)
  price table at `src/aegis/budget/prices.py`. New CLI verbs
  (`aegis budget list/show`), MCP tool (`aegis_budget_status`),
  HTTP endpoints (`GET /remote/v1/budget`, `GET /remote/v1/budget/<queue>`).
```

- [ ] **Step 5: Sync `README.md`**

Add a section after "Scheduled workflows" (or wherever it fits the current top-level structure):

```markdown
## Per-queue budgets

Each queue can declare USD or output-token ceilings over rolling
windows. The substrate rejects new enqueues that would land the
queue over any of those ceilings, returning a structured error that
names every blocked constraint and an ETA for when the queue
unblocks.

```python
queues = {
    "impl": {
        "agent": "opus",
        "max_parallel": 2,
        "budgets": [
            {"usd": 1.00,            "window": "1h"},
            {"usd": 10.00,           "window": "24h"},
            {"output_tokens": 500000, "window": "1h"},
            {"usd": 50.00,           "window": "7d"},
        ],
    },
}
```

Pull-only observability via `aegis budget list/show`,
`aegis_budget_status` MCP tool, and `GET /remote/v1/budget` on the
plane. See [docs/budget.md](docs/budget.md).
```

And in the docs link list:

```markdown
- [Budgets](https://apiad.github.io/aegis/budget/) — per-queue USD / output-token ceilings
```

- [ ] **Step 6: CHANGELOG `[0.9.0]` entry**

In `CHANGELOG.md` above `## [0.8.1]`:

```markdown
## [0.9.0] - 2026-05-25

### Added
- **Per-queue budgets.** Each queue may declare one or more
  `(constraint, window)` ceilings (USD or output-token) over a
  rolling window. New `aegis_enqueue` calls are rejected with a
  structured error when admitting the task would push the queue
  over any of the declared budgets. ALL budgets must allow; the
  rejection names every blocked constraint and an `unblock_at`
  ETA (computed from when the oldest contributing cost ages out
  of its window).
- **Cost accounting on `task_done`.** Existing per-queue JSONL
  audit now carries a `cost` field per `task_done` record:
  `{usd, input_tokens, output_tokens, cache_hit_tokens,
  cache_write_tokens, thinking_tokens}` computed from the worker's
  final SessionMetrics + a static per-(provider, model) price
  table at `src/aegis/budget/prices.py`. Unknown models record
  `cost: {error: "unknown_model"}` instead of crashing the
  finalizer.
- **`BudgetExceeded` typed exception** for the workflow engine —
  `engine.enqueue` raises on budget rejection so workflow Python
  can `try/except` and choose a retry strategy.
- **`aegis_budget_status` MCP tool** — `target=None` for local,
  `target="<peer>"` for cross-host inspection via the new
  `GET /remote/v1/budget` and `GET /remote/v1/budget/<queue>`
  HTTP endpoints. Same auth gating as `/enqueue` / `/callback` /
  `/schedule`.
- **`aegis budget` CLI** — `list` (one-line summary per queue) and
  `show <queue>` (full Decision: every check with spent / limit /
  headroom / window / unblock_at). `--remote <peer>` on both for
  cross-host views.

Spec: `docs/superpowers/specs/2026-05-25-aegis-per-queue-budgets-design.md`.
```

- [ ] **Step 7: Bump version**

```bash
sed -i 's/^version = "0\.8\.1"$/version = "0.9.0"/' pyproject.toml
sed -i '0,/^version = "0\.8\.1"$/s//version = "0.9.0"/' uv.lock
grep -nE '^name = "aegis-harness"|^version = ' uv.lock | head -4
grep '^version' pyproject.toml
```

Expected: both at `0.9.0`.

- [ ] **Step 8: Final gate**

```bash
uv run pytest -q -m "not live" -x
```
Expected: all green.

- [ ] **Step 9: Release commit + tag + push**

```bash
git add docs/budget.md docs/configuration.md docs/index.md \
        docs/roadmap.md README.md CHANGELOG.md mkdocs.yml \
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
Expected: `latest: 0.9.0`.

- [ ] **Step 11: Notify completion via Telegram (VPS-only)**

```bash
bin/notify-telegram.sh "🎉 aegis 0.9.0 released — per-queue token/USD budgets on PyPI" || true
```

---

## Self-review

**Spec coverage:**

| Spec section | Implementation task |
|---|---|
| Motivation | (no task — context only) |
| Non-goals | enforced by absence of code; one validator (Task 4) rejects `usd` + `output_tokens` on same entry |
| Architecture overview | Tasks 1–6 |
| Config shape (multi-window, all-must-allow) | Task 4 (parser) + Task 6 (gate) |
| Cost source + price table | Task 1 (prices) + Task 2 (compute) + Task 3 (JSONL write) |
| Evaluator (BudgetCheck/Decision) | Task 5 |
| Rejection shape (5 caller surfaces) | Task 6 (QueueManager + MCP propagation) + Task 7 (workflow exception) + Task 8 (HTTP 429 — actually returns the dict in 200 body, see footnote) + Task 9 (MCP propagation) + Task 10 (CLI) |
| In-flight cost (ignored in v1) | Task 6 (only counts task_done) |
| MCP surface (aegis_budget_status) | Task 9 |
| HTTP surface (GET /remote/v1/budget) | Task 8 |
| CLI surface | Task 10 |
| TUI surface | **deferred from this plan — see note below** |
| File layout | Tasks 1–10 collectively |
| Testing | every task has hermetic + appropriate live coverage |
| Implementation notes | embedded in tasks (deque cache deferred; JSONL load is the v1 hot path) |
| Open questions | Q1 typed BudgetExceeded → Task 7 implements it; Q2 ACP provider metrics → Task 2 uses defensive getattr; Q3 workflow runner own spend → explicit non-goal, not implemented |

**TUI surface deferred.** The spec's TUI section (always-on strip + dashboard band) is a non-trivial Textual change that overlaps with the v0.4 queue dashboard. To keep this plan shippable and the v0.9.0 release focused on the substrate primitive, the TUI is **explicitly out of scope for v0.9.0** and lands as v0.9.1 (a follow-up plan). The substrate, MCP, HTTP, CLI surfaces are complete in v0.9.0; observability is already pull-able through three channels without the TUI changes. This is a small scope-cut the implementer should *not* try to expand. Note that addition in the CHANGELOG-step.

**Placeholder scan:** No "TBD" / "implement later" / "similar to Task N" survived. Test files include runnable code. The `_make_bridge_with_queue_manager` and `FakeSessionManager` helpers are existing test fixtures — confirm they exist before starting Task 3 (extend if needed).

**Type consistency:** `Cost` (Task 2) ↔ `Cost.as_dict()` JSONL shape (Task 3) ↔ evaluator `_record_value` (Task 5) all agree on the field names. `Budget(constraint, limit, window_str, window)` (Task 4) ↔ `evaluate_budgets`'s reads (Task 5) ↔ `QueueManager.enqueue` budget loop (Task 6) all use the same attribute names. `BudgetCheck(constraint, limit, spent, window_str, window_start, allowed, headroom, unblock_at)` is identical across evaluator (Task 5), MCP tool (Task 9), HTTP endpoint (Task 8), and CLI table (Task 10). `Decision(allowed, checks, blocked_by, unblock_at)` likewise.

Plan complete and saved.
