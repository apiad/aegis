# aegis usage command — Implementation Plan (v1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `aegis usage` CLI that aggregates
`.aegis/state/sessions/*.jsonl` into a usage/cost dashboard plus temporal,
tool, and distribution cuts.

**Architecture:** A pure aggregation engine (`aegis.usage`) reads the
session logs, computes authoritative billed cost from segment-aware
`Result.cost_usd` and a token-priced generation/replay split, and returns a
`UsageReport`. A thin typer surface (`aegis.cli_usage`) renders it as ASCII.
No new persisted state.

**Tech Stack:** Python 3.11+, typer (CLI), `aegis.models` price registry,
stdlib (`json`, `datetime`, `collections`, `statistics`, `decimal`). Tests
via `uv run pytest`.

## Global Constraints

- Package manager is `uv` (never pip): `uv run pytest`, `uv pip install -e .`.
- Work on `main`, commit straight (no feature branch), conventional commits.
- Read-only feature: no writes to `.aegis/state`, no network, no new deps.
- Cost is `Decimal` throughout the engine — never float — to match the
  registry (`ProviderPrices` fields are `Decimal`).
- Billed cost is authoritative from `Result.cost_usd`, handled
  **segment-aware** (cumulative-with-resets). Token×price is only the
  analytical generation/replay split, never the headline (except `~est`
  fallback when `cost_usd` is absent).
- Timezone for temporal bucketing: system-local via `datetime.astimezone()`
  (no arg); `--tz <IANA>` overrides.
- Session logs live at `<root>/.aegis/state/sessions/<handle>.jsonl`;
  `<root>` = `aegis.config.find_project_root()`.
- Prices via `aegis.models.get_prices(provider, model)` →
  `ProviderPrices(input, output, cache_hit, cache_write)`; raises
  `UnknownPriceError` (a `KeyError`) on miss.

---

## File Structure

- Create `src/aegis/usage/__init__.py` — package exports.
- Create `src/aegis/usage/cost.py` — `segment_cost`, `token_cost` (the
  correctness-critical, dependency-free cost math).
- Create `src/aegis/usage/aggregate.py` — reader + `SessionUsage`,
  `TurnRecord`, `UsageReport`, `build_report`, `resolve_prices`, and the
  roll-up methods (`by_model`, `by_day`, `by_dow`, `by_hour`,
  `tool_correlation`, `distribution`).
- Create `src/aegis/cli_usage.py` — typer subapp + ASCII chart helpers.
- Modify `src/aegis/cli.py` — register the subapp with
  `app.add_typer(_usage_app, name="usage")`.
- Create `tests/test_usage_cost.py`, `tests/test_usage_aggregate.py`,
  `tests/test_usage_cli.py`.
- Create `tests/fixtures/usage_sessions/*.jsonl` — hand-written fixture
  logs (built inline by a fixture helper, see Task 3).

---

## Task 1: Cost math (`aegis.usage.cost`)

**Files:**
- Create: `src/aegis/usage/__init__.py` (empty for now)
- Create: `src/aegis/usage/cost.py`
- Test: `tests/test_usage_cost.py`

**Interfaces:**
- Produces:
  - `segment_cost(costs: list[Decimal]) -> Decimal` — sum of each
    monotonic non-decreasing segment's final value (handles
    cumulative-with-resets sequences).
  - `token_cost(usage: dict, prices: ProviderPrices) -> tuple[Decimal, Decimal]`
    — returns `(generation_usd, replay_usd)` for one per-turn usage dict
    with keys `input`, `output`, `cache_creation`, `cache_read`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_usage_cost.py
from decimal import Decimal
from aegis.usage.cost import segment_cost, token_cost
from aegis.models import ProviderPrices


def _c(*xs): return [Decimal(str(x)) for x in xs]


def test_segment_cost_monotonic_single_run():
    # one non-decreasing run → cost is its final value
    assert segment_cost(_c(0.6, 0.8, 1.0, 1.37)) == Decimal("1.37")


def test_segment_cost_single_reset():
    # run A ends at 1.0, resume run B ends at 0.5 → 1.5
    assert segment_cost(_c(0.6, 1.0, 0.3, 0.5)) == Decimal("1.5")


def test_segment_cost_multiple_resets():
    # 1.0 + 0.5 + 2.0
    assert segment_cost(_c(0.6, 1.0, 0.3, 0.5, 0.1, 2.0)) == Decimal("2.6")


def test_segment_cost_empty_and_single():
    assert segment_cost([]) == Decimal(0)
    assert segment_cost(_c(0.7)) == Decimal("0.7")


def test_token_cost_split():
    prices = ProviderPrices(
        input=Decimal("5"), output=Decimal("25"),
        cache_hit=Decimal("0.5"), cache_write=Decimal("6.25"))
    usage = {"input": 1_000_000, "output": 1_000_000,
             "cache_creation": 1_000_000, "cache_read": 1_000_000}
    gen, rep = token_cost(usage, prices)
    assert gen == Decimal("36.25")   # 5 + 6.25 + 25
    assert rep == Decimal("0.5")


def test_token_cost_missing_keys_default_zero():
    prices = ProviderPrices(input=Decimal("5"), output=Decimal("25"),
                            cache_hit=Decimal("0.5"), cache_write=Decimal("6.25"))
    gen, rep = token_cost({"output": 2_000_000}, prices)
    assert gen == Decimal("50")
    assert rep == Decimal("0")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_usage_cost.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.usage.cost'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/usage/__init__.py
```

(leave `__init__.py` empty for now)

```python
# src/aegis/usage/cost.py
"""Cost math for aegis usage aggregation.

segment_cost handles claude-code's cumulative-with-resets ``cost_usd``:
each resume restarts the running total, so a session log holds several
monotonic segments and the true cost is the sum of each segment's final
value. token_cost prices a single per-turn usage dict, splitting new
generation from context-replay (cache reads).
"""
from __future__ import annotations

from decimal import Decimal

from aegis.models import ProviderPrices

_M = Decimal(1_000_000)


def segment_cost(costs: list[Decimal]) -> Decimal:
    total = Decimal(0)
    prev: Decimal | None = None
    for x in costs:
        if prev is not None and x < prev:  # reset → close previous segment
            total += prev
        prev = x
    if prev is not None:
        total += prev
    return total


def token_cost(usage: dict, prices: ProviderPrices) -> tuple[Decimal, Decimal]:
    gen = (Decimal(usage.get("input", 0)) * prices.input
           + Decimal(usage.get("cache_creation", 0)) * prices.cache_write
           + Decimal(usage.get("output", 0)) * prices.output) / _M
    rep = Decimal(usage.get("cache_read", 0)) * prices.cache_hit / _M
    return gen, rep
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_usage_cost.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/aegis/usage/__init__.py src/aegis/usage/cost.py tests/test_usage_cost.py
git commit -m "feat(usage): segment-aware cost + token split math"
```

---

## Task 2: Price resolution (`resolve_prices` in `aggregate.py`)

**Files:**
- Create: `src/aegis/usage/aggregate.py` (starts here, grows in Task 3)
- Test: `tests/test_usage_aggregate.py`

**Interfaces:**
- Consumes: `aegis.models.get_prices`, `UnknownPriceError`.
- Produces:
  - `resolve_prices(provider: str, model: str | None) -> ProviderPrices | None`
    — exact/alias lookup first; on miss, substring-match the model name to
    a canonical family key (`opus`/`sonnet`/`haiku`/`gemini`) and retry;
    returns `None` if still unresolved (e.g. `OpenCode`, `None`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_usage_aggregate.py
from aegis.usage.aggregate import resolve_prices


def test_resolve_exact_alias():
    p = resolve_prices("claude-code", "claude-opus-4-7")
    assert p is not None and p.input > 0


def test_resolve_substring_fallback_for_newer_model():
    # claude-opus-4-8 is not in the shipped registry aliases → fall back
    # to the 'opus' family by substring.
    p = resolve_prices("claude-code", "claude-opus-4-8")
    assert p is not None and p.input > 0


def test_resolve_unknown_returns_none():
    assert resolve_prices("claude-code", "OpenCode") is None
    assert resolve_prices("claude-code", None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_usage_aggregate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.usage.aggregate'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/aegis/usage/aggregate.py
"""Aggregation engine for ``aegis usage``.

Reads per-tab session logs (``<state>/sessions/<handle>.jsonl``) and rolls
them up into a UsageReport. Billed cost is authoritative from
``Result.cost_usd`` (segment-aware); a token-priced generation/replay split
is the analytical lens.
"""
from __future__ import annotations

from aegis.models import ProviderPrices, UnknownPriceError, get_prices

# Substring → canonical family key fallback, applied when an exact/alias
# lookup misses (e.g. a model newer than the shipped registry).
_FAMILIES = ("opus", "sonnet", "haiku", "gemini")


def resolve_prices(provider: str, model: str | None) -> ProviderPrices | None:
    if not model:
        return None
    try:
        return get_prices(provider, model)
    except UnknownPriceError:
        pass
    low = model.lower()
    for fam in _FAMILIES:
        if fam in low:
            try:
                return get_prices(provider, fam)
            except UnknownPriceError:
                return None
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_usage_aggregate.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/aegis/usage/aggregate.py tests/test_usage_aggregate.py
git commit -m "feat(usage): model→price resolution with family fallback"
```

---

## Task 3: Aggregation engine (`build_report` + dataclasses + roll-ups)

**Files:**
- Modify: `src/aegis/usage/aggregate.py`
- Modify: `src/aegis/usage/__init__.py` (export public API)
- Test: `tests/test_usage_aggregate.py` (add fixture builder + tests)

**Interfaces:**
- Consumes: `segment_cost`, `token_cost` (Task 1), `resolve_prices` (Task 2).
- Produces:
  - `@dataclass SessionUsage` — fields: `handle: str`, `model: str | None`,
    `provider: str`, `turns: int`, `tools: Counter`, `tokens: dict`
    (keys `input/output/cache_creation/cache_read`), `billed_usd: Decimal`,
    `gen_usd: Decimal`, `replay_usd: Decimal`, `est: bool`,
    `duration_ms: int`, `errors: int`, `first_ts: str | None`,
    `last_ts: str | None`.
  - `@dataclass TurnRecord` — `ts: str | None`, `billed_delta: Decimal`,
    `gen_usd: Decimal`, `replay_usd: Decimal`, `tools: tuple[str, ...]`,
    `duration_ms: int`, `is_error: bool`.
  - `@dataclass UsageReport` — `sessions: list[SessionUsage]`,
    `turns: list[TurnRecord]`, `first_ts: str | None`, `last_ts: str | None`;
    methods below.
  - `build_report(state_dir, *, default_model, default_provider, since=None, handle=None) -> UsageReport`
    where `state_dir: Path` is `<root>/.aegis/state`, `default_model`/
    `default_provider` come from `.aegis.yaml` (used when a session has no
    `SystemInit.model`), `since: str | None` is an ISO date, `handle` filters
    to one session.

- [ ] **Step 1: Write the failing tests (with an inline fixture builder)**

```python
# add to tests/test_usage_aggregate.py
import json
from pathlib import Path
from decimal import Decimal
from aegis.usage.aggregate import build_report


def _ev(**event):
    return json.dumps({"v": 1, "aegis_ts": event.pop("ts"), "event": event})


def _write_sessions(tmp_path: Path) -> Path:
    sess = tmp_path / "sessions"
    sess.mkdir(parents=True)
    # Session A: opus, 2 turns, a Bash tool, cost 0.4 then 0.9 (no reset)
    (sess / "alpha.jsonl").write_text("\n".join([
        _ev(ts="2026-06-01T12:00:00.000000Z", t="SystemInit",
            session_id="a", model="claude-opus-4-7"),
        _ev(ts="2026-06-01T12:00:01.000000Z", t="ToolUse", name="Bash",
            summary="ls", usage={"input": 5, "cache_creation": 100,
                                 "cache_read": 200, "output": 0}),
        _ev(ts="2026-06-01T12:00:02.000000Z", t="Result", duration_ms=1000,
            is_error=False, cost_usd=0.4,
            usage={"input": 5, "cache_creation": 100,
                   "cache_read": 200, "output": 50}),
        _ev(ts="2026-06-01T12:05:00.000000Z", t="Result", duration_ms=2000,
            is_error=True, cost_usd=0.9,
            usage={"input": 5, "cache_creation": 0,
                   "cache_read": 500, "output": 80}),
    ]) + "\n")
    # Session B: model newer than registry, ONE reset (0.5 then 0.2)
    (sess / "beta.jsonl").write_text("\n".join([
        _ev(ts="2026-06-02T09:00:00.000000Z", t="SystemInit",
            session_id="b", model="claude-opus-4-8"),
        _ev(ts="2026-06-02T09:00:01.000000Z", t="Result", duration_ms=500,
            is_error=False, cost_usd=0.5,
            usage={"input": 1, "cache_creation": 10,
                   "cache_read": 20, "output": 10}),
        _ev(ts="2026-06-02T09:10:00.000000Z", t="Result", duration_ms=700,
            is_error=False, cost_usd=0.2,
            usage={"input": 1, "cache_creation": 0,
                   "cache_read": 30, "output": 5}),
    ]) + "\n")
    # Session C: no SystemInit.model, no cost_usd → falls back to est
    (sess / "gamma.jsonl").write_text("\n".join([
        _ev(ts="2026-06-03T08:00:00.000000Z", t="Result", duration_ms=300,
            is_error=False,
            usage={"input": 2, "cache_creation": 5,
                   "cache_read": 10, "output": 20}),
    ]) + "\n")
    # Session D: empty shell (only a hook line) → filtered out
    (sess / "delta.jsonl").write_text(
        _ev(ts="2026-06-03T08:00:00.000000Z", t="Unknown", raw="{}") + "\n")
    return tmp_path / ".aegis" / "state" if False else tmp_path


def test_build_report_basic(tmp_path):
    root = _write_sessions(tmp_path)
    r = build_report(root, default_model="claude-opus-4-7",
                     default_provider="claude-code")
    by = {s.handle: s for s in r.sessions}
    assert set(by) == {"alpha", "beta", "gamma"}          # delta filtered
    assert by["alpha"].turns == 2
    assert by["alpha"].errors == 1
    assert by["alpha"].tools["Bash"] == 1
    assert by["alpha"].billed_usd == Decimal("0.9")        # monotonic → last
    assert by["alpha"].model == "claude-opus-4-7"
    # beta: reset 0.5 then 0.2 → 0.7
    assert by["beta"].billed_usd == Decimal("0.7")
    assert by["beta"].gen_usd > 0                          # opus-4-8 resolved
    # gamma: no cost_usd → est flag, billed from token estimate
    assert by["gamma"].est is True
    assert by["gamma"].billed_usd > 0


def test_report_rollups(tmp_path):
    root = _write_sessions(tmp_path)
    r = build_report(root, default_model="claude-opus-4-7",
                     default_provider="claude-code")
    models = dict(r.by_model())
    assert "claude-opus-4-7" in models and "claude-opus-4-8" in models
    # distribution returns percentiles over per-session billed cost
    d = r.distribution()
    assert d["p50"] >= 0 and d["max"] >= d["p50"]
    # tool_correlation: Bash appeared once
    tc = dict(r.tool_correlation())
    assert "Bash" in tc
    # temporal buckets non-empty
    assert sum(v for _, v in r.by_dow()) == len(r.turns)


def test_since_filter(tmp_path):
    root = _write_sessions(tmp_path)
    r = build_report(root, default_model="claude-opus-4-7",
                     default_provider="claude-code", since="2026-06-02")
    handles = {s.handle for s in r.sessions}
    assert "alpha" not in handles and "beta" in handles
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_usage_aggregate.py -v`
Expected: FAIL — `AttributeError` / `TypeError` (`build_report` missing)

- [ ] **Step 3: Write the implementation**

Append to `src/aegis/usage/aggregate.py`:

```python
import json
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from aegis.usage.cost import segment_cost, token_cost

_TOKEN_KEYS = ("input", "output", "cache_creation", "cache_read")


@dataclass
class SessionUsage:
    handle: str
    model: str | None
    provider: str
    turns: int
    tools: Counter
    tokens: dict
    billed_usd: Decimal
    gen_usd: Decimal
    replay_usd: Decimal
    est: bool
    duration_ms: int
    errors: int
    first_ts: str | None
    last_ts: str | None


@dataclass
class TurnRecord:
    ts: str | None
    billed_delta: Decimal
    gen_usd: Decimal
    replay_usd: Decimal
    tools: tuple[str, ...]
    duration_ms: int
    is_error: bool


def _read_session(path: Path, *, default_model, default_provider):
    """Parse one .jsonl → (SessionUsage, list[TurnRecord]) or (None, []) if
    the session has no turns and no tools (empty shell)."""
    model = None
    tokens = Counter()
    tools = Counter()
    cost_seq: list[Decimal] = []
    turns = 0
    errors = 0
    dur = 0
    first_ts = last_ts = None
    turn_recs: list[TurnRecord] = []
    pending_tools: list[str] = []
    prev_cost: Decimal | None = None

    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue  # skip corrupt/partial line
        ts = rec.get("aegis_ts")
        ev = rec.get("event") or {}
        t = ev.get("t")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        if t == "SystemInit":
            model = model or ev.get("model")
        elif t == "ToolUse":
            name = ev.get("name", "?")
            tools[name] += 1
            pending_tools.append(name)
        elif t == "Result":
            turns += 1
            if ev.get("is_error"):
                errors += 1
            dur += ev.get("duration_ms") or 0
            u = ev.get("usage") or {}
            for k in _TOKEN_KEYS:
                tokens[k] += u.get(k, 0)
            prices = resolve_prices(
                default_provider, model or default_model)
            gen, rep = (token_cost(u, prices) if prices
                        else (Decimal(0), Decimal(0)))
            billed_delta = Decimal(0)
            c = ev.get("cost_usd")
            if c is not None:
                c = Decimal(str(c))
                cost_seq.append(c)
                # per-turn increment (segment-aware): reset → whole value
                billed_delta = c if (prev_cost is None or c < prev_cost) \
                    else c - prev_cost
                prev_cost = c
            turn_recs.append(TurnRecord(
                ts=ts, billed_delta=billed_delta, gen_usd=gen,
                replay_usd=rep, tools=tuple(pending_tools),
                duration_ms=ev.get("duration_ms") or 0,
                is_error=bool(ev.get("is_error"))))
            pending_tools = []

    if turns == 0 and sum(tools.values()) == 0:
        return None, []

    resolved_model = model or default_model
    prices = resolve_prices(default_provider, resolved_model)
    gen_total = sum((tr.gen_usd for tr in turn_recs), Decimal(0))
    rep_total = sum((tr.replay_usd for tr in turn_recs), Decimal(0))
    if cost_seq:
        billed = segment_cost(cost_seq)
        est = False
    else:
        billed = gen_total + rep_total   # token estimate fallback
        est = True

    su = SessionUsage(
        handle=path.stem, model=model, provider=default_provider,
        turns=turns, tools=tools, tokens=dict(tokens), billed_usd=billed,
        gen_usd=gen_total, replay_usd=rep_total, est=est,
        duration_ms=dur, errors=errors, first_ts=first_ts, last_ts=last_ts)
    return su, turn_recs


def build_report(state_dir: Path, *, default_model: str,
                 default_provider: str, since: str | None = None,
                 handle: str | None = None) -> "UsageReport":
    sess_dir = state_dir / "sessions"
    sessions: list[SessionUsage] = []
    turns: list[TurnRecord] = []
    if sess_dir.is_dir():
        for p in sorted(sess_dir.glob("*.jsonl")):
            if handle and p.stem != handle:
                continue
            su, trs = _read_session(
                p, default_model=default_model,
                default_provider=default_provider)
            if su is None:
                continue
            if since and (su.last_ts or "") < since:
                continue
            sessions.append(su)
            turns.extend(trs)
    first = min((s.first_ts for s in sessions if s.first_ts), default=None)
    last = max((s.last_ts for s in sessions if s.last_ts), default=None)
    return UsageReport(sessions=sessions, turns=turns,
                       first_ts=first, last_ts=last)
```

Now append the `UsageReport` dataclass with roll-up methods:

```python
_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class UsageReport:
    sessions: list[SessionUsage]
    turns: list[TurnRecord]
    first_ts: str | None
    last_ts: str | None

    # ---- totals ----
    def total_billed(self) -> Decimal:
        return sum((s.billed_usd for s in self.sessions), Decimal(0))

    def total_gen(self) -> Decimal:
        return sum((s.gen_usd for s in self.sessions), Decimal(0))

    def total_replay(self) -> Decimal:
        return sum((s.replay_usd for s in self.sessions), Decimal(0))

    def total_turns(self) -> int:
        return sum(s.turns for s in self.sessions)

    def total_tools(self) -> Counter:
        c = Counter()
        for s in self.sessions:
            c.update(s.tools)
        return c

    def total_errors(self) -> int:
        return sum(s.errors for s in self.sessions)

    # ---- breakdowns ----
    def by_model(self) -> list[tuple[str, dict]]:
        agg: dict[str, dict] = {}
        for s in self.sessions:
            k = s.model or "unknown"
            a = agg.setdefault(k, {"billed": Decimal(0), "turns": 0,
                                   "sessions": 0})
            a["billed"] += s.billed_usd
            a["turns"] += s.turns
            a["sessions"] += 1
        return sorted(agg.items(), key=lambda kv: -kv[1]["billed"])

    def distribution(self) -> dict:
        cs = sorted(float(s.billed_usd) for s in self.sessions)
        if not cs:
            return {"n": 0, "min": 0, "p50": 0, "p90": 0, "p99": 0,
                    "max": 0, "mean": 0}

        def pct(p):
            return cs[min(len(cs) - 1, int(p / 100 * len(cs)))]
        return {"n": len(cs), "min": cs[0], "p50": pct(50), "p90": pct(90),
                "p99": pct(99), "max": cs[-1], "mean": statistics.mean(cs)}

    def tool_correlation(self, min_turns: int = 1) -> list[tuple[str, float, int]]:
        buckets: dict[str, list[float]] = {}
        for tr in self.turns:
            for name in set(tr.tools):
                buckets.setdefault(name, []).append(
                    float(tr.gen_usd + tr.replay_usd))
        rows = [(n, statistics.mean(v), len(v))
                for n, v in buckets.items() if len(v) >= min_turns]
        return sorted(rows, key=lambda r: -r[1])

    # ---- temporal (local tz) ----
    def _local(self, ts: str, tz: ZoneInfo | None):
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(tz)  # tz=None → system local

    def by_bucket(self, kind: str, tz: ZoneInfo | None = None
                  ) -> list[tuple[str, int]]:
        counts: Counter = Counter()
        order: list[str] = []
        for tr in self.turns:
            if not tr.ts:
                continue
            dt = self._local(tr.ts, tz)
            if kind == "month":
                key = dt.strftime("%Y-%m")
            elif kind == "dow":
                key = _DOW[dt.weekday()]
            elif kind == "hour":
                key = f"{dt.hour:02d}:00"
            else:
                raise ValueError(kind)
            counts[key] += 1
        if kind == "dow":
            keys = [d for d in _DOW if d in counts]
        elif kind == "hour":
            keys = [f"{h:02d}:00" for h in range(24)
                    if f"{h:02d}:00" in counts]
        else:
            keys = sorted(counts)
        return [(k, counts[k]) for k in keys]

    def by_dow(self, tz=None):
        return self.by_bucket("dow", tz)

    def by_month(self, tz=None):
        return self.by_bucket("month", tz)

    def by_hour(self, tz=None):
        return self.by_bucket("hour", tz)

    def by_day(self, tz: ZoneInfo | None = None) -> list[tuple[str, float]]:
        daily: dict[str, float] = {}
        for tr in self.turns:
            if not tr.ts:
                continue
            k = self._local(tr.ts, tz).strftime("%Y-%m-%d")
            daily[k] = daily.get(k, 0.0) + float(tr.billed_delta)
        return sorted(daily.items())
```

Update `src/aegis/usage/__init__.py`:

```python
from aegis.usage.aggregate import (
    SessionUsage, TurnRecord, UsageReport, build_report, resolve_prices,
)
from aegis.usage.cost import segment_cost, token_cost

__all__ = [
    "SessionUsage", "TurnRecord", "UsageReport", "build_report",
    "resolve_prices", "segment_cost", "token_cost",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_usage_aggregate.py -v`
Expected: PASS (6 passed — 3 from Task 2, 3 new)

- [ ] **Step 5: Commit**

```bash
git add src/aegis/usage/aggregate.py src/aegis/usage/__init__.py tests/test_usage_aggregate.py
git commit -m "feat(usage): aggregation engine — sessions, turns, rollups"
```

---

## Task 4: CLI surface (`aegis usage`)

**Files:**
- Create: `src/aegis/cli_usage.py`
- Modify: `src/aegis/cli.py` (register subapp)
- Test: `tests/test_usage_cli.py`

**Interfaces:**
- Consumes: `build_report` + `UsageReport` roll-ups (Task 3);
  `aegis.config.find_project_root`; `aegis.tui.metrics._fmt_cost`,
  `_fmt_tokens`, `_fmt_time`.
- Produces: a typer app `app` (imported into `cli.py` as `_usage_app`) with
  one command taking options `--by`, `--sessions`, `--tools`, `--since`,
  `--session`, `--model`, `--tz`.

- [ ] **Step 1: Write the failing smoke tests**

```python
# tests/test_usage_cli.py
import json
from pathlib import Path
from typer.testing import CliRunner
from aegis.cli_usage import app

runner = CliRunner()


def _ev(**event):
    return json.dumps({"v": 1, "aegis_ts": event.pop("ts"), "event": event})


def _mk(tmp_path: Path):
    sess = tmp_path / ".aegis" / "state" / "sessions"
    sess.mkdir(parents=True)
    (sess / "alpha.jsonl").write_text("\n".join([
        _ev(ts="2026-06-01T12:00:00.000000Z", t="SystemInit",
            session_id="a", model="claude-opus-4-7"),
        _ev(ts="2026-06-01T12:00:01.000000Z", t="ToolUse", name="Bash",
            summary="ls", usage={"input": 5, "cache_creation": 100,
                                 "cache_read": 200, "output": 0}),
        _ev(ts="2026-06-01T12:00:02.000000Z", t="Result", duration_ms=1000,
            is_error=False, cost_usd=0.4,
            usage={"input": 5, "cache_creation": 100,
                   "cache_read": 200, "output": 50}),
    ]) + "\n")
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  opus:\n    provider: claude-code\n    model: opus\n"
        "default_agent: opus\n")
    return tmp_path


def test_dashboard_runs(tmp_path, monkeypatch):
    root = _mk(tmp_path)
    monkeypatch.chdir(root)
    res = runner.invoke(app, [])
    assert res.exit_code == 0, res.output
    assert "AEGIS USAGE" in res.output
    assert "alpha" in res.output


def test_flags_run(tmp_path, monkeypatch):
    root = _mk(tmp_path)
    monkeypatch.chdir(root)
    for args in (["--by", "dow"], ["--sessions"], ["--tools"],
                 ["--session", "alpha"]):
        res = runner.invoke(app, args)
        assert res.exit_code == 0, (args, res.output)


def test_no_sessions_message(tmp_path, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  opus:\n    provider: claude-code\n    model: opus\n"
        "default_agent: opus\n")
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, [])
    assert res.exit_code == 0
    assert "no session logs" in res.output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_usage_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.cli_usage'`

- [ ] **Step 3: Write the implementation**

```python
# src/aegis/cli_usage.py
"""``aegis usage`` — session usage & cost analytics (read-only)."""
from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import typer
import yaml

from aegis.tui.metrics import _fmt_cost, _fmt_time, _fmt_tokens
from aegis.usage import build_report

app = typer.Typer(add_completion=False, no_args_is_help=False)

_BLOCKS = " ▁▂▃▄▅▆▇█"


def _bar(v: float, mx: float, width: int = 34) -> str:
    return "█" * (int(v / mx * width) if mx else 0)


def _resolve_default_agent() -> tuple[str, str]:
    from aegis.config import find_project_root
    root = find_project_root() or Path.cwd()
    cfg = {}
    p = root / ".aegis.yaml"
    if p.exists():
        cfg = yaml.safe_load(p.read_text()) or {}
    da = cfg.get("default_agent")
    agent = (cfg.get("agents") or {}).get(da, {}) if da else {}
    return agent.get("model", "opus"), agent.get("provider", "claude-code")


def _state_dir() -> Path:
    from aegis.config import find_project_root
    root = find_project_root() or Path.cwd()
    return root / ".aegis" / "state"


@app.command()
def usage(
    by: str = typer.Option(None, "--by", help="month|dow|hour"),
    sessions: bool = typer.Option(False, "--sessions",
                                  help="cost distribution + top sessions"),
    tools: bool = typer.Option(False, "--tools",
                               help="tool→cost correlation"),
    since: str = typer.Option(None, "--since", help="ISO date lower bound"),
    session: str = typer.Option(None, "--session", help="single handle"),
    model: str = typer.Option(None, "--model", help="filter to one model"),
    tz: str = typer.Option(None, "--tz", help="IANA tz (default: system)"),
) -> None:
    zone = ZoneInfo(tz) if tz else None
    dmodel, dprovider = _resolve_default_agent()
    report = build_report(_state_dir(), default_model=dmodel,
                          default_provider=dprovider, since=since,
                          handle=session)
    if model:
        report.sessions = [s for s in report.sessions if s.model == model]
    if not report.sessions:
        typer.echo("No session logs found.")
        raise typer.Exit(0)

    if by:
        _render_temporal(report, by, zone)
    elif sessions:
        _render_sessions(report)
    elif tools:
        _render_tools(report)
    else:
        _render_dashboard(report, zone)


def _render_dashboard(r, zone) -> None:
    n = len(r.sessions)
    turns = r.total_turns()
    billed, gen, rep = r.total_billed(), r.total_gen(), r.total_replay()
    typer.echo("═" * 60)
    typer.echo(f"  AEGIS USAGE   {n} sessions · {turns:,} turns")
    typer.echo(f"  window: {(r.first_ts or '?')[:10]} → {(r.last_ts or '?')[:10]}")
    typer.echo("═" * 60)
    typer.echo("\nCOST")
    typer.echo(f"  billed (authoritative)   {_fmt_cost(billed):>12}")
    split = gen + rep
    pct = float(rep / split * 100) if split else 0
    typer.echo(f"    ├ generation           {_fmt_cost(gen):>12}")
    typer.echo(f"    └ context replay       {_fmt_cost(rep):>12}   ({pct:.0f}% of split)")
    est = [s.handle for s in r.sessions if s.est]
    if est:
        typer.echo(f"  ~est (no cost_usd): {len(est)} session(s)")
    typer.echo("\nAVERAGES")
    typer.echo(f"  per session   {_fmt_cost(billed / n)}")
    typer.echo(f"  per turn      {_fmt_cost(billed / turns) if turns else '$0'}"
               f" · {_fmt_time(_avg_turn_secs(r))}")
    typer.echo(f"  error rate    {r.total_errors()}/{turns}")
    typer.echo("\nBY MODEL")
    for mdl, a in r.by_model():
        typer.echo(f"  {mdl:22} {_fmt_cost(a['billed']):>10} · "
                   f"{a['turns']:>5} turns · {a['sessions']} sess")
    typer.echo("\nTOOLS (top 12)")
    tc = r.total_tools()
    mx = max(tc.values()) if tc else 1
    for name, c in tc.most_common(12):
        typer.echo(f"  {name:16} {c:>6}  {_bar(c, mx)}")
    typer.echo("\nTOP 5 SESSIONS (billed)")
    for s in sorted(r.sessions, key=lambda s: -s.billed_usd)[:5]:
        typer.echo(f"  {s.handle:28} {_fmt_cost(s.billed_usd):>10} · "
                   f"{s.turns} turns")
    _render_sparkline(r, zone)


def _avg_turn_secs(r) -> float:
    durs = [tr.duration_ms for tr in r.turns if tr.duration_ms]
    return (sum(durs) / len(durs) / 1000) if durs else 0.0


def _render_sparkline(r, zone) -> None:
    days = r.by_day(zone)
    if not days:
        return
    mx = max(v for _, v in days) or 1
    typer.echo("\nDAILY BILLED (last 28d)")
    line = "".join(_BLOCKS[min(8, int(v / mx * 8))] for _, v in days[-28:])
    typer.echo(f"  {line}   peak {_fmt_cost(mx)}/day")


def _render_temporal(r, kind, zone) -> None:
    if kind == "hour":
        data = r.by_hour(zone)
    elif kind == "month":
        data = r.by_month(zone)
    elif kind == "dow":
        data = r.by_dow(zone)
    else:
        typer.echo("--by must be one of: month, dow, hour")
        raise typer.Exit(1)
    mx = max((v for _, v in data), default=1)
    typer.echo(f"TURNS BY {kind.upper()}")
    for lab, v in data:
        typer.echo(f"  {lab:9} {_bar(v, mx, 40):<40} {v}")


def _render_sessions(r) -> None:
    d = r.distribution()
    typer.echo("COST-PER-SESSION DISTRIBUTION (billed)")
    typer.echo(f"  n={d['n']}  min={_fmt_cost(d['min'])}  "
               f"p50={_fmt_cost(d['p50'])}  p90={_fmt_cost(d['p90'])}  "
               f"p99={_fmt_cost(d['p99'])}  max={_fmt_cost(d['max'])}")
    typer.echo(f"  mean={_fmt_cost(d['mean'])}")
    typer.echo("\nTOP 15 SESSIONS")
    for s in sorted(r.sessions, key=lambda s: -s.billed_usd)[:15]:
        flag = " ~est" if s.est else ""
        typer.echo(f"  {s.handle:28} {_fmt_cost(s.billed_usd):>10} · "
                   f"{s.turns:>4} turns · {sum(s.tools.values()):>4} tools{flag}")


def _render_tools(r) -> None:
    base_turns = [float(tr.gen_usd + tr.replay_usd) for tr in r.turns]
    import statistics
    base = statistics.mean(base_turns) if base_turns else 0.0
    typer.echo(f"TOOL → COST CORRELATION   baseline turn {_fmt_cost(base)}")
    for name, avg, cnt in r.tool_correlation(min_turns=1)[:15]:
        mult = (avg / base) if base else 0
        typer.echo(f"  {name:16} {_fmt_cost(avg):>10} ({mult:>4.1f}x) · "
                   f"{cnt} turns")
```

Register in `cli.py` — add next to the other `add_typer` calls (near line
32, after the `models` subapp):

```python
from aegis.cli_usage import app as _usage_app
app.add_typer(_usage_app, name="usage")
```

Note: `_fmt_cost` accepts a `Decimal`; pass `Decimal` values directly. If
`_fmt_cost` requires a specific type, wrap with `Decimal(str(...))` at the
call site — verify by reading `src/aegis/tui/metrics.py:40` before relying
on it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_usage_cli.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the whole usage suite + a real smoke**

Run: `uv run pytest tests/test_usage_cost.py tests/test_usage_aggregate.py tests/test_usage_cli.py -v`
Expected: all PASS.

Run against real data (from a checkout that has `.aegis/state/sessions`):
`cd /home/apiad/Workspace && uv run --project repos/aegis python -m aegis usage`
Expected: dashboard prints with a billed total near the validated ~$10.9k;
sanity-check `--by hour`, `--sessions`, `--tools`.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/cli_usage.py src/aegis/cli.py tests/test_usage_cli.py
git commit -m "feat(usage): aegis usage CLI — dashboard + temporal/session/tool cuts"
```

---

## Task 5: Docs

**Files:**
- Modify: `docs/usage.md` (or add a section) and `AGENTS.md` CLI list.

- [ ] **Step 1: Document the command** in `docs/usage.md`: what `aegis usage`
  shows, the billed-vs-analytical cost model (one paragraph), and each flag.
- [ ] **Step 2: Add `aegis usage`** to the `aegis.cli.py` entrypoint list in
  `AGENTS.md` (the "Layout" bullet that enumerates subcommands).
- [ ] **Step 3: Commit**

```bash
git add docs/usage.md AGENTS.md
git commit -m "docs(usage): document aegis usage command"
```

---

## Self-Review

**Spec coverage:**
- Two-layer cost model → Task 1 (`token_cost`) + Task 3 (billed vs
  gen/replay in `SessionUsage`) + Task 4 dashboard. ✓
- Segment-aware `cost_usd` → Task 1 `segment_cost`, tested with resets. ✓
- Model attribution from `SystemInit.model` + config fallback → Task 3
  `_read_session` / `build_report`. ✓
- Price fallback for newer models → Task 2 `resolve_prices`. ✓
- Temporal (month/dow/hour) + system-local tz + `--tz` → Task 3 `by_bucket`,
  Task 4 `_render_temporal`. ✓
- Distribution + tool correlation → Task 3 methods, Task 4 renderers. ✓
- Edge cases: corrupt lines, missing cost_usd (`~est`), empty shells,
  non-claude driver (prices None → billed excluded/est), no sessions → all
  in Task 3 `_read_session` / Task 4 no-sessions message. ✓
- `--json` and TUI modal are v2/v3 — correctly out of this plan. ✓

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `build_report(state_dir, *, default_model,
default_provider, since, handle)` used identically in Tasks 3 and 4;
`resolve_prices(provider, model|None) -> ProviderPrices|None` consistent
across Tasks 2–3; roll-up method names (`by_model`, `by_day`, `by_dow`,
`by_hour`, `by_month`, `distribution`, `tool_correlation`, `total_*`) match
between Task 3 definitions and Task 4 call sites. Cost values are `Decimal`
end-to-end into `_fmt_cost`.

**One verify-at-implementation note:** confirm `_fmt_cost` signature in
`src/aegis/tui/metrics.py:40` accepts `Decimal` (Task 4 Step 3 flags this).
