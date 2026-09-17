# Fleet Dashboard v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace F10's card grid with a gauge band (host and quota bars, counters by attention), a session list whose items keep `did`/`now` in full, a detail pane for the selected session with live monitors and ETA, frame-driven motion, and an auto mode that rotates the detail to sessions with something new.

**Architecture:** Pure renderers in `aegis/fleet/render.py` take a snapshot and a frame number and return Rich `Text`; a pure `Rotator` in `aegis/fleet/rotation.py` decides what auto mode shows; `FleetScreen` composes three Textual widgets (band, list, detail), runs a 1 s snapshot clock and a 0.5 s frame clock, and owns keys and clicks. The snapshot gains the numbers the gauges need; the app hands raw system stats and quota states instead of formatted tiers.

**Tech Stack:** Python 3.13, Textual, Rich, psutil, pytest (`make test` = `uv run pytest -q -n auto -m "not slow" --max-unmarked-duration=3`).

**Spec:** `docs/superpowers/specs/2026-09-17-aegis-fleet-dashboard-v2-design.md`, with `docs/superpowers/specs/2026-09-17-aegis-turn-attention-design.md` for the categories.

**Precondition:** `docs/superpowers/plans/2026-09-17-aegis-turn-attention.md` is fully implemented. This plan uses `aegis.attention` (`mark`, `LABELS`, `is_pending`, `style_for`), `AgentSession.effective_attention`, `AgentSession.attention_seq` and `ConversationPane.attention_acked`.

## Global Constraints

- Only the current palette; no theme gains a colour.
- The list takes 44% of the width; under 110 columns the detail stacks under the list.
- Each list item is at least three lines: header, where, then `now` (working) or `did` in full; the command tail only when neither exists. No line is cut with an ellipsis.
- Detail section order: header, title, where and uptime, NOW, DID, MONITORS, GAUGES, PLAN, ACTIVITY, SPEND · COORDINATION; empty sections are omitted.
- Motion frames advance every 0.5 s: pulse (working glyph alternates state colour and muted), blink (error glyph, `needs_input` glyph and a critical quota percentage alternate visible and blank, keeping their cells), sweep (an indeterminate monitor bar's block moves one step).
- Auto mode: on when the screen opens, resumes 120 s after the last key or click; a session stays at least 20 s; with nothing new, step through working sessions every 30 s; with none working, hold. Priority among new sessions: `needs_input`, `error`, a monitor that ended, `review`, a new `did` or plan movement, a state change, a new `now`; ties to the session shown longest ago.
- The frame clock redraws from the held snapshot and never rebuilds it.
- Quotas are read from `QuotaService.current()`; F10 never polls them. Screenshot scripts must stub `AegisApp._quota_tick`.
- Shared checkout: `git commit -F - -- <paths>`, never `git add -A`, never `--amend`. `uv run ruff format` only on `src/` files.
- The gate is `make test` verbatim, once, at the end; a task runs only its own test files.

## File map

| file | change |
|---|---|
| `src/aegis/tui/sysmeter.py` | `SystemStats` gains `ram_used_gb`, `ram_total_gb` |
| `src/aegis/usage/quota.py` | new `quota_gauges(readings, *, now) -> tuple[QuotaGauge, ...]` |
| `src/aegis/fleet/models.py` | new `QuotaGauge`, `MonitorRow`; `CardView` and `BandView` gain fields |
| `src/aegis/fleet/snapshot.py` | fills the new card fields |
| `src/aegis/fleet/render.py` | rewritten: `bar`, `sweep_bar`, `render_band`, `render_item`, `render_detail` |
| `src/aegis/fleet/rotation.py` | new `Rotator` |
| `src/aegis/tui/fleet_screen.py` | rewritten: three widgets, two clocks, rotation |
| `src/aegis/tui/app.py` | `_tick` keeps `SystemStats`; `_fleet_system_row` hands raw data; `_fleet_snapshot` sets each card's pending attention |
| `tests/test_fleet_render.py`, `tests/test_fleet_screen.py` | grid tests replaced |

---

### Task 1: The data the gauges need

**Files:**
- Modify: `src/aegis/tui/sysmeter.py` (`SystemStats`, `sample_system`)
- Modify: `src/aegis/usage/quota.py` (new `quota_gauges`)
- Modify: `src/aegis/fleet/models.py`
- Modify: `src/aegis/fleet/snapshot.py` (`_card`)
- Test: `tests/test_fleet_v2_data.py`

**Interfaces:**
- Produces:
  - `SystemStats(cpu: float, ram: float, disk: float, ram_used_gb: float = 0.0, ram_total_gb: float = 0.0)`
  - `QuotaGauge(label: str, percent: float, severity: str, resets_in_s: float | None)` (frozen dataclass in `fleet/models.py`)
  - `quota_gauges(readings: Iterable[tuple[QuotaProvider, QuotaState]], *, now: datetime) -> tuple[QuotaGauge, ...]`; label is `f"{provider.label} {window_label}"` (`cc 5h`); providers with no snapshot are skipped
  - `MonitorRow(id: str, description: str, pct: float | None, eta_s: float | None, elapsed_s: float)` (frozen dataclass)
  - `CardView` new fields: `plan_tasks: tuple[PlanTask, ...] = ()`, `monitors: tuple[MonitorRow, ...] = ()`, `avg_turn_s: float = 0.0`, `ctx_tokens: int = 0`, `ctx_window: int = 0`, `attention: str = ""` (the *pending* category for the viewing view; `""` when nothing is pending)
  - `BandView` new fields: `stats: SystemStats | None = None`, `gauges: tuple[QuotaGauge, ...] = ()`

- [ ] **Step 1: Write the failing tests**

```python
from datetime import datetime, timedelta, timezone

from aegis.fleet.models import CardView, MonitorRow, QuotaGauge
from aegis.tui.sysmeter import SystemStats, sample_system
from aegis.usage.quota import QuotaSnapshot, QuotaState, QuotaWindow, quota_gauges
from aegis.usage.quota_providers import PROVIDERS

NOW = datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc)


def test_system_stats_carry_ram_in_gigabytes(tmp_path):
    s = sample_system(tmp_path)
    assert s.ram_total_gb > 0 and 0 < s.ram_used_gb <= s.ram_total_gb


def _state(*windows):
    return QuotaState(snapshot=QuotaSnapshot(windows=tuple(windows), fetched_at=0.0))


def test_quota_gauges_follow_each_providers_bar_windows():
    claude, opencode = PROVIDERS
    readings = [
        (claude, _state(
            QuotaWindow("session", 38.0, "normal", NOW + timedelta(hours=2), True),
            QuotaWindow("weekly_all", 81.0, "warning", None, True),
        )),
        (opencode, QuotaState(failure="no_credentials")),
    ]
    got = quota_gauges(readings, now=NOW)
    assert got == (
        QuotaGauge(label="cc 5h", percent=38.0, severity="normal", resets_in_s=7200.0),
        QuotaGauge(label="cc wk", percent=81.0, severity="warning", resets_in_s=None),
    )


def test_a_card_defaults_to_no_detail():
    c = CardView(handle="a")
    assert c.plan_tasks == () and c.monitors == () and c.attention == ""


def test_the_snapshot_carries_monitors_and_plan_tasks():
    from types import SimpleNamespace

    from aegis.fleet.snapshot import build_snapshot
    from aegis.monitor.schema import MonitorView
    from aegis.tui.metrics import SessionMetrics
    from aegis.tui.state import AgentState

    class _MM:
        def snapshot(self, *, for_handle=None):
            return [MonitorView(id="m1", description="pytest", state="watching",
                                pct=60.0, eta_s=32.0, elapsed_s=48.0)] if for_handle == "a" else []

    plan = SimpleNamespace(snapshot=lambda now: None)
    s = SimpleNamespace(handle="a", state=AgentState.ready, metrics=SessionMetrics(),
                        plan=plan, plan_state=lambda: SimpleNamespace(tasks=("t",)))
    snap = build_snapshot(SimpleNamespace(_sessions=[s], monitor_manager=_MM()), now=0.0)
    card = snap.cards[0]
    assert card.monitors == (MonitorRow(id="m1", description="pytest", pct=60.0, eta_s=32.0, elapsed_s=48.0),)
    assert card.plan_tasks == ("t",)
```

If `MonitorView`'s constructor or `SessionMetrics()` needs other arguments, read `src/aegis/monitor/schema.py:151` and `tests/test_fleet_snapshot.py` and build them the way those do.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q tests/test_fleet_v2_data.py`
Expected: FAIL (`cannot import name 'MonitorRow'`)

- [ ] **Step 3: Implement**

`sysmeter.py`:

```python
@dataclass(frozen=True)
class SystemStats:
    cpu: float  # system-wide CPU utilisation, 0–100
    ram: float  # virtual-memory utilisation, 0–100
    disk: float  # usage of the project-root filesystem, 0–100
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
```

In `sample_system`, read `vm = psutil.virtual_memory()` once and pass `ram=float(vm.percent), ram_used_gb=(vm.total - vm.available) / 2**30, ram_total_gb=vm.total / 2**30`.

`fleet/models.py` (add near `EventLine`; `PlanTask` imported under `TYPE_CHECKING` is not enough for a dataclass default, so import it normally from `aegis.plan.models`):

```python
@dataclass(frozen=True)
class QuotaGauge:
    label: str  # "cc 5h"
    percent: float
    severity: str  # normal | warning | critical
    resets_in_s: float | None


@dataclass(frozen=True)
class MonitorRow:
    id: str
    description: str
    pct: float | None  # None: no progress condition, drawn as a sweep
    eta_s: float | None
    elapsed_s: float
```

Add the `CardView` and `BandView` fields listed under Interfaces (`stats` typed `"SystemStats | None"` with the import from `aegis.tui.sysmeter` under `TYPE_CHECKING` and `from __future__ import annotations` already present).

`usage/quota.py`:

```python
def quota_gauges(readings, *, now: datetime) -> tuple["QuotaGauge", ...]:
    """One gauge per bar window of every provider with a reading, in
    ``bar_windows`` order. A provider with no snapshot (no credentials, or
    nothing fetched yet) draws nothing rather than an empty bar."""
    from aegis.fleet.models import QuotaGauge

    out = []
    for provider, state in readings:
        snap = state.snapshot
        if snap is None:
            continue
        for kind, short in provider.bar_windows:
            w = snap.window(kind)
            if w is None:
                continue
            resets = (w.resets_at - now).total_seconds() if w.resets_at else None
            out.append(QuotaGauge(label=f"{provider.label} {short}", percent=w.percent,
                                  severity=w.severity, resets_in_s=resets))
    return tuple(out)
```

`fleet/snapshot.py` `_card`: add

```python
        plan_tasks=tuple(getattr(s.plan_state(), "tasks", ())) if hasattr(s, "plan_state") else (),
        monitors=tuple(
            MonitorRow(id=v.id, description=v.description, pct=v.pct,
                       eta_s=v.eta_s, elapsed_s=v.elapsed_s)
            for v in _monitors_for(manager, s.handle)
        ),
        avg_turn_s=_avg_turn(m),
        ctx_tokens=m.last_true_input,
        ctx_window=m.context_window,
```

with

```python
def _avg_turn(m) -> float:
    """Mean seconds over the metrics' recent generating turns; 0 when none."""
    rates = list(getattr(m, "_turn_rates", ()))
    return sum(sec for _out, sec in rates) / len(rates) if rates else 0.0
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest -q tests/test_fleet_v2_data.py tests/test_fleet_snapshot.py tests/test_sidebar_system.py`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/aegis/tui/sysmeter.py src/aegis/usage/quota.py src/aegis/fleet/models.py src/aegis/fleet/snapshot.py
git add tests/test_fleet_v2_data.py
git commit -F - -- src/aegis/tui/sysmeter.py src/aegis/usage/quota.py src/aegis/fleet/models.py src/aegis/fleet/snapshot.py tests/test_fleet_v2_data.py <<'EOF'
feat(fleet): the snapshot carries what the v2 gauges draw

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 2: Bars and motion primitives

**Files:**
- Modify: `src/aegis/fleet/render.py` (add helpers; keep the existing grid functions until Task 6)
- Test: `tests/test_fleet_v2_motion.py`

**Interfaces:**
- Produces:
  - `bar(pct: float, cells: int, style: str, pal) -> Text` — `cells` wide, filled `█` in `style`, rest `░` in `pal.rule`
  - `sweep_bar(cells: int, frame: int, pal) -> Text` — a block of `max(1, cells // 4)` cells at offset `frame % cells`, wrapping
  - `pulse(style: str, frame: int, pal) -> str` — `style` on even frames, `pal.muted` on odd
  - `blink(text: str, frame: int) -> str` — `text` on even frames, the same number of cells of spaces on odd
  - `severity_style(severity: str, pal) -> str` — `normal` → `pal.ready`, `warning` → `pal.accent`, `critical` → `pal.error`
  - `ctx_style(pct: float, pal) -> str` — `pal.error` above 80, `pal.accent` above 60, else `pal.ready`

- [ ] **Step 1: Write the failing tests**

```python
from rich.cells import cell_len

from aegis.fleet.render import blink, bar, ctx_style, pulse, severity_style, sweep_bar
from aegis.tui.themes import INK, aegis_colors

P = aegis_colors(INK)


def test_a_bar_is_exactly_its_width_and_fills_proportionally():
    t = bar(60, 10, P.accent, P)
    assert t.cell_len == 10
    assert t.plain == "██████░░░░"


def test_a_bar_clamps_out_of_range_values():
    assert bar(-5, 4, P.ready, P).plain == "░░░░"
    assert bar(250, 4, P.ready, P).plain == "████"


def test_a_sweep_moves_one_step_per_frame_and_keeps_its_width():
    a, b = sweep_bar(12, 0, P), sweep_bar(12, 1, P)
    assert a.cell_len == b.cell_len == 12
    assert a.plain != b.plain
    assert a.plain.index("█") + 1 == b.plain.index("█")


def test_pulse_alternates_with_muted():
    assert pulse(P.working, 0, P) == P.working
    assert pulse(P.working, 1, P) == P.muted


def test_blink_keeps_the_cells():
    assert blink("94%", 0) == "94%"
    assert blink("94%", 1) == "   "
    assert cell_len(blink("✗", 1)) == cell_len("✗")


def test_severity_and_context_colours():
    assert severity_style("critical", P) == P.error
    assert severity_style("warning", P) == P.accent
    assert severity_style("normal", P) == P.ready
    assert ctx_style(92, P) == P.error and ctx_style(65, P) == P.accent and ctx_style(10, P) == P.ready
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q tests/test_fleet_v2_motion.py`
Expected: FAIL (import error)

- [ ] **Step 3: Implement** (in `render.py`, after the imports)

```python
def bar(pct: float, cells: int, style: str, pal) -> Text:
    cells = max(0, cells)
    filled = round(cells * max(0.0, min(100.0, pct)) / 100)
    t = Text(_BAR * filled, style=style)
    t.append(_EMPTY * (cells - filled), style=pal.rule)
    return t


def sweep_bar(cells: int, frame: int, pal) -> Text:
    """An indeterminate bar: a block that moves one cell per frame."""
    cells = max(1, cells)
    block = max(1, cells // 4)
    start = frame % cells
    lit = {(start + i) % cells for i in range(block)}
    t = Text()
    for i in range(cells):
        t.append(_BAR if i in lit else _EMPTY, style=pal.accent if i in lit else pal.rule)
    return t


def pulse(style: str, frame: int, pal) -> str:
    return style if frame % 2 == 0 else pal.muted


def blink(text: str, frame: int) -> str:
    return text if frame % 2 == 0 else " " * cell_len(text)


def severity_style(severity: str, pal) -> str:
    return {"warning": pal.accent, "critical": pal.error}.get(severity, pal.ready)


def ctx_style(pct: float, pal) -> str:
    if pct > 80:
        return pal.error
    if pct > 60:
        return pal.accent
    return pal.ready
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest -q tests/test_fleet_v2_motion.py`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/aegis/fleet/render.py
git add tests/test_fleet_v2_motion.py
git commit -F - -- src/aegis/fleet/render.py tests/test_fleet_v2_motion.py <<'EOF'
feat(fleet): bars, sweep, pulse and blink as pure helpers

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 3: The band

**Files:**
- Modify: `src/aegis/fleet/render.py` (new `render_band`)
- Test: `tests/test_fleet_v2_band.py`

**Interfaces:**
- Consumes: Task 1 models, Task 2 helpers, `aegis.attention.mark`
- Produces: `render_band(snapshot: FleetSnapshot, pal, width: int, frame: int) -> Text`. Counters are computed from `snapshot.cards` (live ones, `ghost_since is None`): `working` (state working), then by `card.attention`: `needs_input`, `error` (also `state == "error"`), `review`, `waiting`, and `done` for every other ready card.

Layout, one line each:
1. host gauges: `CPU`, `RAM` (+ `used/totalG`), `DSK`, `CTX` (`band.ctx_avg`, `avg`); each takes a quarter of `width`: label (5 cells), a bar filling the rest minus the value text.
2. quota gauges, `len(band.gauges)` equal slots, value `NN%` coloured by `severity_style`, blinking when `critical`, then `↻ 2h14m` from `resets_in_s` (`_age`-style: `3h51m`, `12m`, `45s`); omitted when `band.gauges` is empty.
3. counters and totals: `host · ✻ N working · ? N need you · ✗ N error · ◆ N review · ⧗ N waiting · ✓ N done · $X live · recap $Y / N · queues r/c · monitors N · build · clock`; `✻` pulses when `working > 0`; a zero `need you` or `error` counter is drawn muted and does not blink.

Under 110 columns, lines 1 and 2 put two gauges per line instead of all on one.

- [ ] **Step 1: Write the failing tests**

```python
from aegis.fleet.models import BandView, CardView, FleetSnapshot, QuotaGauge
from aegis.fleet.render import render_band
from aegis.tui.sysmeter import SystemStats
from aegis.tui.themes import INK, aegis_colors

P = aegis_colors(INK)
BAND = BandView(host="zion", stats=SystemStats(64, 56, 32, 17.9, 32.0), ctx_avg=47.0,
                gauges=(QuotaGauge("cc 5h", 38, "normal", 8040), QuotaGauge("oc mo", 94, "critical", None)),
                cost_live=41.2, clock="01:12")
CARDS = (
    CardView(handle="a", state="working"),
    CardView(handle="b", state="ready", attention="needs_input"),
    CardView(handle="c", state="ready", attention="error"),
    CardView(handle="d", state="ready", attention="review"),
    CardView(handle="e", state="ready", attention="waiting"),
    CardView(handle="f", state="ready"),
    CardView(handle="g", state="ready", ghost_since=1.0),
)


def _lines(width=160, frame=0):
    return render_band(FleetSnapshot(band=BAND, cards=CARDS), P, width, frame).plain.split("\n")


def test_three_rows_host_quota_counters():
    lines = _lines()
    assert lines[0].startswith("CPU") and "RAM" in lines[0] and "17.9/32G" in lines[0] and "CTX" in lines[0]
    assert "cc 5h" in lines[1] and "↻ 2h14m" in lines[1] and "oc mo" in lines[1]
    assert "zion" in lines[2]


def test_counters_follow_attention_and_skip_ghosts():
    row = _lines()[2]
    for expected in ("1 working", "1 need you", "1 error", "1 review", "1 waiting", "1 done"):
        assert expected in row


def test_no_row_is_wider_than_the_screen():
    for width in (100, 160, 220):
        assert all(len(line) <= width for line in _lines(width))


def test_a_critical_quota_blinks_and_a_normal_one_does_not():
    on, off = _lines(frame=0)[1], _lines(frame=1)[1]
    assert "94%" in on and "94%" not in off
    assert "38%" in on and "38%" in off


def test_no_quota_draws_no_quota_row():
    from dataclasses import replace

    t = render_band(FleetSnapshot(band=replace(BAND, gauges=()), cards=CARDS), P, 160, 0)
    assert "cc 5h" not in t.plain and len(t.plain.rstrip("\n").split("\n")) == 2


def test_a_narrow_screen_puts_two_gauges_per_line():
    lines = _lines(width=100)
    assert lines[0].startswith("CPU") and "DSK" not in lines[0]
    assert any(line.startswith("DSK") for line in lines)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q tests/test_fleet_v2_band.py`
Expected: FAIL (`cannot import name 'render_band'`)

- [ ] **Step 3: Implement** `render_band` in `render.py`:

```python
def _gauge(label: str, pct: float, value: str, style: str, cells: int, pal,
           *, value_style: str | None = None, tail: str = "") -> Text:
    """``label bar value tail`` in exactly ``cells`` cells (or fewer, never more)."""
    head = f"{label:<6}"
    rest = f" {value}" + (f" {tail}" if tail else "")
    bar_cells = max(3, cells - cell_len(head) - cell_len(rest) - 1)
    t = Text(head, style=pal.muted)
    t.append_text(bar(pct, bar_cells, style, pal))
    t.append(f" {value}", style=value_style or pal.ink)
    if tail:
        t.append(f" {tail}", style=pal.muted)
    t.truncate(cells)
    return t


def _rows_of(gauges: list[Text], per_row: int, width: int) -> Text:
    t = Text()
    for i in range(0, len(gauges), per_row):
        if i:
            t.append("\n")
        for j, g in enumerate(gauges[i : i + per_row]):
            if j:
                t.append("  ")
            t.append_text(g)
    return t


def _reset(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = max(0, int(seconds))
    if s >= 3600:
        return f"↻ {s // 3600}h{(s % 3600) // 60:02d}m"
    if s >= 60:
        return f"↻ {s // 60}m"
    return f"↻ {s}s"


def render_band(snapshot: FleetSnapshot, pal, width: int, frame: int) -> Text:
    from aegis.attention import mark, style_for

    band = snapshot.band
    narrow = width < 110
    t = Text()

    host: list[Text] = []
    per = 2 if narrow else 4
    cells = (width - 2 * (per - 1)) // per
    if band.stats is not None:
        s = band.stats
        ram_tail = f"{s.ram_used_gb:.1f}/{s.ram_total_gb:.0f}G" if s.ram_total_gb else ""
        host += [
            _gauge("CPU", s.cpu, f"{s.cpu:.0f}%", ctx_style(s.cpu, pal), cells, pal),
            _gauge("RAM", s.ram, f"{s.ram:.0f}%", ctx_style(s.ram, pal), cells, pal, tail=ram_tail),
            _gauge("DSK", s.disk, f"{s.disk:.0f}%", ctx_style(s.disk, pal), cells, pal),
        ]
    host.append(_gauge("CTX", band.ctx_avg, f"{band.ctx_avg:.0f}%", ctx_style(band.ctx_avg, pal), cells, pal, tail="avg"))
    t.append_text(_rows_of(host, per, width))
    t.append("\n")

    if band.gauges:
        per_q = 2 if narrow else len(band.gauges)
        qcells = (width - 2 * (per_q - 1)) // per_q
        quota = []
        for g in band.gauges:
            style = severity_style(g.severity, pal)
            value = f"{g.percent:.0f}%"
            if g.severity == "critical":
                value = blink(value, frame)
            quota.append(_gauge(g.label, g.percent, value, style, qcells, pal,
                                value_style=style, tail=_reset(g.resets_in_s)))
        t.append_text(_rows_of(quota, per_q, width))
        t.append("\n")

    live = [c for c in snapshot.cards if c.ghost_since is None]
    working = sum(1 for c in live if c.state == "working")
    idle = [c for c in live if c.state != "working"]

    def count(cat: str) -> int:
        if cat == "error":
            return sum(1 for c in idle if c.attention == "error" or c.state == "error")
        if cat == "done":
            return sum(1 for c in idle if c.state != "error" and c.attention in ("", "done"))
        return sum(1 for c in idle if c.attention == cat and c.state != "error")

    sep = (" · ", pal.muted)
    t_counts = Text()
    t_counts.append(band.host, style=f"bold {pal.accent}")
    t_counts.append(" · ", style=pal.muted)
    t_counts.append(f"✻ {working} working", style=pulse(pal.working, frame, pal) if working else pal.muted)
    t_counts.append(" · ", style=pal.muted)
    for cat, noun in _COUNTERS:
        n = count(cat)
        if n and cat in ("needs_input", "error"):
            blink_off = cat == "needs_input" and frame % 2 == 1
            t_counts.append_text(Text.from_markup(mark(cat, pal, blink_off=blink_off)))
            t_counts.append(f" {n} {noun}", style=style_for(cat, pal))
        else:
            t_counts.append(f"{_ATTN_GLYPH[cat]} {n} {noun}", style=pal.ink if n else pal.muted)
        t_counts.append(" · ", style=pal.muted)
    tail = f"${band.cost_live:.2f} live"
    if band.recap_calls:
        tail += f" · recap ${band.recap_cost:.2f} / {band.recap_calls}"
    running, configured = band.queues
    tail += f" · queues {running}/{configured} · monitors {band.monitors}"
    if band.build:
        tail += f" · {Text.from_markup(band.build[-1]).plain}"
    if band.clock:
        tail += f" · {band.clock}"
    t_counts.append(tail, style=pal.muted)
    t_counts.truncate(width)
    t.append_text(t_counts)
    return t
```

with, at module level:

```python
_ATTN_GLYPH = {"needs_input": "?", "error": "✗", "review": "◆", "waiting": "⧗", "done": "✓"}
_COUNTERS = (
    ("needs_input", "need you"), ("error", "error"), ("review", "review"),
    ("waiting", "waiting"), ("done", "done"),
)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest -q tests/test_fleet_v2_band.py`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/aegis/fleet/render.py
git add tests/test_fleet_v2_band.py
git commit -F - -- src/aegis/fleet/render.py tests/test_fleet_v2_band.py <<'EOF'
feat(fleet): the v2 band, host and quota gauges and attention counters

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 4: The list item and the detail pane

**Files:**
- Modify: `src/aegis/fleet/render.py` (new `render_item`, `render_detail`)
- Test: `tests/test_fleet_v2_panes.py`

**Interfaces:**
- Consumes: Tasks 1–2, `aegis.attention.mark`, `LABELS`, `style_for`; existing `_one_line`, `_age`, `_identity`, `_origin_line`, `_event` in `render.py`
- Produces:
  - `render_item(card: CardView, pal, frame: int) -> Text` — no width: the widget wraps. Line 1 is sanitized with `_one_line`; the `did`/`now` body keeps its words and is not cut.
  - `render_detail(card: CardView, pal, width: int, frame: int) -> Text` — `width` sizes the gauge bars only.

Item lines:
1. lead mark: pulsing `●` in `pal.working` while working; else `mark(card.attention, pal, blink_off=frame odd)` when `card.attention`; else `●` in `pal.ready` (`pal.error` when `state == "error"`). Then the tab number (or `⏱` for an ephemeral card), the handle bold, then right-hand facts separated by spaces: a 6-cell context bar with `NN%`, `◉N` when `card.monitors`, and the attention label (`needs you`) when pending, else `working 4m12s` / idle age from `uptime_s`. Ghosts: `closed Ns ago`.
2. where: `_origin_line(card.origin)` for ephemeral cards, else `_identity(card)`.
3. `now <doing>` in `pal.working` when working and `card.doing`; else `did <did>` in `pal.ink`; if neither, up to three `_event` lines in `pal.muted`.

Detail sections, each preceded by an uppercase `pal.muted` heading line and separated by a blank line; a section with no content is skipped:
- header: handle bold accent, then the attention mark and label (or the pulsing state), then `turn 4m12s` while working;
- title (bold), where and `up <age> · tab N` (muted);
- `NOW` (`card.doing`), `DID` (`card.did`);
- `MONITORS · N`: one line per `MonitorRow`: pulsing `◉`, description (one line), a bar (`sweep_bar` when `pct is None`), `NN%`, `48s`, `ETA 32s` or `no ETA`;
- `GAUGES`: `ctx` bar with `NN%` and `280k/1M` when `ctx_window`; `plan` bar `done/total` when `plan_total`; `turn` bar of `turn_s / max(avg_turn_s, turn_s)` with `avg 2m10s` when `avg_turn_s`;
- `PLAN`: one line per `plan_tasks` item: `●` done (`status == "completed"`), `◐` in progress, `○` pending, subject;
- `ACTIVITY`: `_event` for each of `card.events[-3:]`;
- `SPEND · COORDINATION`: `$cost · N claims · ← spoke_with[0] · → waiting_on[0]`.

- [ ] **Step 1: Write the failing tests**

```python
from aegis.fleet.models import CardView, EventLine, MonitorRow, Origin
from aegis.fleet.render import render_detail, render_item
from aegis.plan.models import PlanTask
from aegis.tui.themes import INK, aegis_colors

P = aegis_colors(INK)
LONG = ("Fixed the F10 grid: a newline inside a Bash summary split one card row across two "
        "terminal lines; every session text is now sanitized before drawing, with a failing test first.")


def _card(**kw):
    base = dict(handle="fleet-dashboard-f10", title="Fleet dashboard F10", agent_slug="opus",
                repo="aegis · main +2 ~5", state="ready", did=LONG, ctx_pct=92.0,
                ctx_tokens=920_000, ctx_window=1_000_000, uptime_s=68_400, tab_index=1, cost_usd=168.02)
    base.update(kw)
    return CardView(**base)


def test_an_item_is_at_least_three_lines_and_keeps_did_whole():
    text = render_item(_card(), P, 0).plain
    lines = text.split("\n")
    assert len(lines) >= 3
    assert LONG in text.replace("\n", " ")
    assert "…" not in text


def test_a_working_item_shows_now_instead_of_did():
    text = render_item(_card(state="working", doing="Running the suite."), P, 0).plain
    assert "now Running the suite." in text and "did " not in text


def test_an_item_with_no_recap_falls_back_to_commands():
    ev = EventLine(at=0.0, tool="Bash", summary="uv run pytest")
    text = render_item(_card(did="", events=(ev,)), P, 0).plain
    assert "uv run pytest" in text


def test_a_pending_needs_input_leads_the_item_and_blinks():
    on = render_item(_card(attention="needs_input"), P, 0).plain
    off = render_item(_card(attention="needs_input"), P, 1).plain
    assert "?" in on.split("\n")[0] and "?" not in off.split("\n")[0]
    assert "needs you" in on.split("\n")[0]


def test_an_ephemeral_item_names_its_origin():
    card = _card(origin=Origin(kind="queue", by="tasks", returns_to="une-tools-tasks"))
    assert "tasks" in render_item(card, P, 0).plain.split("\n")[1]


def test_the_detail_orders_its_sections():
    card = _card(state="working", doing="Running the suite.",
                 monitors=(MonitorRow("m1", "pytest -n auto", 60.0, 32.0, 48.0),),
                 plan_done=3, plan_total=5,
                 plan_tasks=(PlanTask(key="1", subject="Write the spec", status="completed"),),
                 events=(EventLine(at=0.0, tool="Edit", summary="render.py"),))
    text = render_detail(card, P, 90, 0).plain
    order = ["NOW", "DID", "MONITORS", "GAUGES", "PLAN", "ACTIVITY", "SPEND"]
    positions = [text.index(h) for h in order]
    assert positions == sorted(positions)
    assert "ETA 32s" in text and "60%" in text


def test_an_indeterminate_monitor_sweeps_and_says_no_eta():
    card = _card(monitors=(MonitorRow("m1", "release CI", None, None, 190.0),))
    a = render_detail(card, P, 90, 0).plain
    b = render_detail(card, P, 90, 1).plain
    assert "no ETA" in a and a != b


def test_an_empty_section_is_omitted():
    text = render_detail(_card(doing="", monitors=(), plan_tasks=(), events=()), P, 90, 0).plain
    assert "NOW" not in text and "MONITORS" not in text and "PLAN" not in text
```

Check `PlanTask`'s required fields in `src/aegis/plan/models.py:13` before running and adjust the constructor.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q tests/test_fleet_v2_panes.py`
Expected: FAIL (import error)

- [ ] **Step 3: Implement** `render_item` and `render_detail` in `render.py` following the layout above. Skeleton, to be completed field by field:

```python
def _lead(card: CardView, pal, frame: int) -> Text:
    from aegis.attention import mark

    if card.state == "working":
        return Text("●", style=pulse(pal.working, frame, pal))
    if card.attention:
        return Text.from_markup(mark(card.attention, pal, blink_off=frame % 2 == 1))
    return Text("●", style=pal.error if card.state == "error" else pal.ready)


def render_item(card: CardView, pal, frame: int) -> Text:
    from aegis.attention import LABELS, style_for

    t = _lead(card, pal, frame)
    num = "⏱" if card.origin.ephemeral else str(card.tab_index or "")
    t.append(f" {num} ", style=pal.muted)
    t.append(_one_line(card.handle), style=f"bold {pal.ink}")
    t.append("  ")
    t.append_text(bar(card.ctx_pct, 6, ctx_style(card.ctx_pct, pal), pal))
    t.append(f" {card.ctx_pct:.0f}%", style=pal.muted)
    if card.monitors:
        t.append(f" ◉{len(card.monitors)}", style=pal.accent)
    if card.ghost_since is not None:
        t.append(f"  closed {_age(card.ghost_s)} ago", style=pal.muted)
    elif card.attention and card.state != "working":
        t.append(f"  {LABELS[card.attention]}", style=style_for(card.attention, pal))
    elif card.state == "working":
        t.append(f"  working {_age(card.turn_s)}", style=pal.working)
    t.append("\n")
    where = _origin_line(card.origin) if card.origin.ephemeral else _identity(card)
    t.append(_one_line(where), style=pal.accent if card.origin.ephemeral else pal.muted)
    t.append("\n")
    if card.state == "working" and card.doing:
        t.append("now ", style=pal.muted)
        t.append(card.doing, style=pal.working)
    elif card.did:
        t.append("did ", style=pal.muted)
        t.append(card.did, style=pal.ink)
    else:
        for i, ev in enumerate(card.events[-_EVENTS:]):
            if i:
                t.append("\n")
            t.append(_one_line(_event(ev)), style=pal.muted)
    return t
```

`render_detail` builds a list of `(heading, Text)` pairs in the section order, skips empty bodies, and joins them with `"\n\n"`. Monitor rows use `bar(pct, cells, pal.accent, pal)` or `sweep_bar(cells, frame, pal)` with `cells = max(10, width - 60)`; gauges use `cells = max(10, width - 30)`.

Check `_identity`'s signature (`render.py:137`) and `_origin_line` (`render.py:129`) before using them.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest -q tests/test_fleet_v2_panes.py`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/aegis/fleet/render.py
git add tests/test_fleet_v2_panes.py
git commit -F - -- src/aegis/fleet/render.py tests/test_fleet_v2_panes.py <<'EOF'
feat(fleet): v2 list items and the detail pane, did/now never cut

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 5: The rotator

**Files:**
- Create: `src/aegis/fleet/rotation.py`
- Test: `tests/test_fleet_rotation.py`

**Interfaces:**
- Consumes: `CardView`, `FleetSnapshot`
- Produces:
  - `Rotator(*, idle_s: float = 120.0, dwell_s: float = 20.0, cycle_s: float = 30.0)`
  - `touch(now: float) -> None` — a key or click; auto off until `now + idle_s`
  - `is_auto(now: float) -> bool`
  - `observe(snapshot: FleetSnapshot, now: float) -> None`
  - `pick(now: float, current: str | None) -> str | None` — the handle to show (may equal `current`); calling it with a handle different from `current` records that handle as shown at `now`
  - `countdown(now: float) -> float | None` — seconds until the next possible switch, `None` when not auto
  - `fingerprint(card: CardView) -> tuple` (module function)

Rules (from the spec): newness is a fingerprint `(state, did, doing, plan_done, plan_total, plan_current, attention, frozenset(m.id for m in monitors))` differing from the one recorded when the handle was last shown. Priority rank of a new card: 0 `attention == "needs_input"`, 1 `attention == "error"` or `state == "error"`, 2 a monitor id recorded at last show is gone, 3 `attention == "review"`, 4 `did`/plan fields changed, 5 `state` changed, 6 only `doing` changed. Ghosts are never picked.

- [ ] **Step 1: Write the failing tests**

```python
from dataclasses import replace

from aegis.fleet.models import CardView, FleetSnapshot, MonitorRow
from aegis.fleet.rotation import Rotator


def snap(*cards):
    return FleetSnapshot(cards=tuple(cards))


A = CardView(handle="a", state="ready", did="x")
B = CardView(handle="b", state="ready", did="y")
W = CardView(handle="w", state="working", doing="z")


def test_auto_is_on_at_open_and_off_for_two_minutes_after_a_key():
    r = Rotator()
    assert r.is_auto(0.0)
    r.touch(10.0)
    assert not r.is_auto(129.0)
    assert r.is_auto(130.0)


def test_holds_at_least_the_dwell_even_when_something_is_new():
    r = Rotator()
    r.observe(snap(A, B), 0.0)
    assert r.pick(0.0, current=None) in ("a", "b")
    first = r.pick(0.0, current=None)
    r.observe(snap(replace(A, did="new a"), replace(B, did="new b")), 5.0)
    assert r.pick(19.0, current=first) == first
    assert r.pick(21.0, current=first) != first


def test_needs_input_beats_error_beats_a_new_did():
    r = Rotator()
    base = snap(A, B, W)
    r.observe(base, 0.0)
    for c in base.cards:
        r._record(c, 0.0)
    r.observe(snap(replace(A, did="changed"), replace(B, attention="error"), replace(W, attention="needs_input")), 1.0)
    assert r.pick(30.0, current="zzz") == "w"


def test_a_shown_session_is_not_new_until_it_changes_again():
    r = Rotator()
    r.observe(snap(A), 0.0)
    for c in (A,):
        r._record(c, 0.0)
    r.observe(snap(replace(A, did="changed")), 1.0)
    assert r.pick(30.0, current=None) == "a"
    r.observe(snap(replace(A, did="changed")), 31.0)
    assert r.pick(60.0, current="a") == "a"


def test_with_nothing_new_it_cycles_working_sessions_every_thirty_seconds():
    r = Rotator()
    w2 = CardView(handle="w2", state="working", doing="q")
    s = snap(A, W, w2)
    r.observe(s, 0.0)
    for c in s.cards:
        r._record(c, 0.0)
    r._show("w", 0.0)
    assert r.pick(25.0, current="w") == "w"
    assert r.pick(31.0, current="w") == "w2"


def test_with_nothing_new_and_nothing_working_it_holds():
    r = Rotator()
    s = snap(A, B)
    r.observe(s, 0.0)
    for c in s.cards:
        r._record(c, 0.0)
    assert r.pick(300.0, current="a") == "a"


def test_a_finished_monitor_ranks_above_review():
    r = Rotator()
    m = MonitorRow("m1", "pytest", 50.0, 10.0, 5.0)
    s = snap(replace(A, monitors=(m,)), B)
    r.observe(s, 0.0)
    for c in s.cards:
        r._record(c, 0.0)
    r.observe(snap(replace(A, monitors=()), replace(B, attention="review")), 1.0)
    assert r.pick(30.0, current="zzz") == "a"


def test_ghosts_are_never_picked():
    r = Rotator()
    g = CardView(handle="g", state="ready", did="new", ghost_since=1.0)
    r.observe(snap(g), 0.0)
    assert r.pick(30.0, current=None) is None


def test_countdown_is_none_outside_auto():
    r = Rotator()
    r.touch(0.0)
    assert r.countdown(1.0) is None
```

The tests reach two privates on purpose: `_record(card, now)` marks a card as seen without showing it, and `_show(handle, now)` makes a handle the one on screen since `now`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q tests/test_fleet_rotation.py`
Expected: FAIL (`No module named 'aegis.fleet.rotation'`)

- [ ] **Step 3: Implement**

```python
"""What F10 shows when nobody is driving it.

Left open on a large screen, the dashboard should move to whatever just
changed and stay long enough to be read. Pure: a clock is passed in, so
the rules are tested without Textual or real time.
"""

from __future__ import annotations

from aegis.fleet.models import CardView, FleetSnapshot


def fingerprint(card: CardView) -> tuple:
    return (
        card.state, card.did, card.doing, card.plan_done, card.plan_total,
        card.plan_current, card.attention, frozenset(m.id for m in card.monitors),
    )


class Rotator:
    def __init__(self, *, idle_s: float = 120.0, dwell_s: float = 20.0, cycle_s: float = 30.0) -> None:
        self.idle_s, self.dwell_s, self.cycle_s = idle_s, dwell_s, cycle_s
        self._touched: float | None = None
        self._cards: dict[str, CardView] = {}
        self._seen: dict[str, tuple] = {}
        self._seen_at: dict[str, float] = {}
        self._shown_since: float = float("-inf")

    def touch(self, now: float) -> None:
        self._touched = now

    def is_auto(self, now: float) -> bool:
        return self._touched is None or now - self._touched >= self.idle_s

    def observe(self, snapshot: FleetSnapshot, now: float) -> None:
        self._cards = {c.handle: c for c in snapshot.cards if c.ghost_since is None}

    def _record(self, card: CardView, now: float) -> None:
        self._seen[card.handle] = fingerprint(card)
        self._seen_at[card.handle] = now

    def _rank(self, card: CardView) -> int | None:
        old = self._seen.get(card.handle)
        new = fingerprint(card)
        if old == new:
            return None
        if card.attention == "needs_input":
            return 0
        if card.attention == "error" or card.state == "error":
            return 1
        if old is not None and old[7] - new[7]:
            return 2
        if card.attention == "review":
            return 3
        if old is None or old[1] != new[1] or old[3:6] != new[3:6]:
            return 4
        if old[0] != new[0]:
            return 5
        return 6

    def _show(self, handle: str, now: float) -> str:
        self._record(self._cards[handle], now)
        self._shown_since = now
        return handle

    def pick(self, now: float, current: str | None) -> str | None:
        if not self._cards:
            return None
        if current not in self._cards:
            current = None
        if current is not None and now - self._shown_since < self.dwell_s:
            return current
        ranked = sorted(
            ((rank, self._seen_at.get(h, float("-inf")), h)
             for h, c in self._cards.items()
             if h != current and (rank := self._rank(c)) is not None),
        )
        if ranked:
            return self._show(ranked[0][2], now)
        working = sorted(h for h, c in self._cards.items() if c.state == "working")
        if working and (current is None or now - self._shown_since >= self.cycle_s):
            if current in working:
                nxt = working[(working.index(current) + 1) % len(working)]
            else:
                nxt = working[0]
            if nxt != current:
                return self._show(nxt, now)
        if current is None:
            return self._show(next(iter(self._cards)), now)
        return current

    def countdown(self, now: float) -> float | None:
        if not self.is_auto(now):
            return None
        return max(0.0, self.dwell_s - (now - self._shown_since))
```

Note `_rank` compares fingerprint tuple positions: index 0 state, 1 did, 2 doing, 3–5 plan, 6 attention, 7 monitor ids. Keep that comment in the code.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest -q tests/test_fleet_rotation.py`
Expected: all pass. If `test_holds_at_least_the_dwell...` fails because the first `pick` already recorded both cards as seen, the fix belongs in the test's setup (observe changed cards after the first pick), not in the dwell rule.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/aegis/fleet/rotation.py
git add src/aegis/fleet/rotation.py tests/test_fleet_rotation.py
git commit -F - -- src/aegis/fleet/rotation.py tests/test_fleet_rotation.py <<'EOF'
feat(fleet): a rotator that shows what changed, at a readable pace

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 6: The screen, rebuilt

**Files:**
- Modify: `src/aegis/tui/fleet_screen.py` (rewrite the layout; keep `in_tab_order`, `COALESCE_S`, the observer hooking, `poke`, `action_pick`, `action_open`)
- Modify: `src/aegis/fleet/render.py` (delete `render_card`, `render_fleet`, `columns_for`, `CARD_WIDTH`, `GUTTER`, `_band`, `_cap`, `_row`, `_fit`, `_footer`, `_bar`, `_state_style` once nothing imports them)
- Modify: `tests/test_fleet_screen.py`, `tests/test_fleet_render.py`, `tests/test_fleet_card_restore.py` (grid tests out, v2 tests in)
- Test: `tests/test_fleet_screen.py`

**Interfaces:**
- Consumes: `render_band`, `render_item`, `render_detail`, `Rotator`
- Produces:
  - widgets: `#fleet-band` (`Static`), `#fleet-list` (`VerticalScroll`) of `_Item(Static)` with `handle`, `#fleet-detail` (`VerticalScroll`) holding `#fleet-detail-body` (`Static`), `#fleet-footer` (`Static`)
  - `FleetScreen.selected: int` (1-based, into `snapshot.cards`), `FleetScreen.frame: int`, `FleetScreen.rotator: Rotator`
  - `action_move(delta: int)`: moves `selected`, clamped, and calls `rotator.touch(time.monotonic())`
  - `select_handle(handle: str) -> None`
  - `refresh_fleet()` (1 s), `advance_frame()` (0.5 s)

Behaviour:
- `on_mount`: `set_interval(1.0, self.refresh_fleet)`, `set_interval(0.5, self.advance_frame)`, and the first `refresh_fleet` after refresh.
- `refresh_fleet`: as today (hook events, ghosts, band system row), then `self.rotator.observe(snap, now)`; if `self.rotator.is_auto(now)`, `handle = self.rotator.pick(now, current=<selected handle>)` and select it; then `_draw()`.
- `advance_frame`: `self.frame += 1`; `_draw()` without rebuilding the snapshot.
- `_draw`: band `update(render_band(snap, pal, width, frame))`; reconcile `_Item` widgets to `snap.cards` by handle (mount new, remove gone, reorder), `update(render_item(card, pal, frame))` on each, add the CSS class `-selected` to the selected one and `scroll_to_widget` it; detail body `update(render_detail(card, pal, detail_width, frame))`; footer text `↑↓ select   enter open tab   1-9 tab   esc/F10 close` plus, right-aligned, `auto · next in {n}s` when `rotator.countdown(now)` is not `None`.
- Layout CSS: `#fleet-body` is `Horizontal`; `#fleet-list { width: 44%; }`, `#fleet-detail { width: 1fr; border: round $accent; }`; `_Item { border: round $panel-lighten-2; padding: 0 1; margin-bottom: 1; height: auto; }`, `_Item.-selected { border: round $accent; background: $boost; }`, `_Item.-ghost { border: dashed $panel-lighten-2; }`. `on_resize` toggles a `-narrow` class on the screen under 110 columns, whose CSS sets `#fleet-body { layout: vertical; }` and `#fleet-list { width: 100%; height: auto; max-height: 50%; }`.
- Keys: `up`/`down` → `move(-1)`/`move(1)`; `enter` → `open`; `1`–`9` → `pick(n)`. Remove the `left`/`right` bindings and `move_row`.
- Click on an `_Item`: `select_handle(item.handle)` and `rotator.touch(...)`; a second click on the already selected item opens it.

- [ ] **Step 1: Replace the grid tests.** In `tests/test_fleet_screen.py` delete `test_every_rect_sits_on_its_own_cards_top_border`, `test_card_at_maps_a_cell_to_its_card_and_the_gutter_to_none`, and the `render_fleet`/`card_rects`/`CARD_WIDTH` imports. In `tests/test_fleet_render.py` delete every test that imports `render_card`, `render_fleet` or `columns_for` (all of them except none: the whole file goes; `tests/test_fleet_v2_*.py` replace it). In `tests/test_fleet_card_restore.py` rewrite the two card tests to use `render_item`:

```python
def test_a_card_with_a_recap_hides_the_command_tail():
    ev = EventLine(at=0.0, tool="Bash", summary="SENTINEL-CMD")
    assert "SENTINEL-CMD" not in render_item(CardView(handle="a", did="landed x", events=(ev,)), C, 0).plain


def test_a_card_without_a_recap_falls_back_to_the_commands():
    ev = EventLine(at=0.0, tool="Bash", summary="SENTINEL-CMD")
    assert "SENTINEL-CMD" in render_item(CardView(handle="a", events=(ev,)), C, 0).plain
```

Add to `tests/test_fleet_screen.py`:

```python
import time


def test_up_and_down_move_the_detail_and_stop_auto():
    scr = FleetScreen(lambda: SNAP)
    assert scr.rotator.is_auto(0.0)
    scr.action_move(1)
    assert scr.selected == 2
    assert not scr.rotator.is_auto(time.monotonic())


def test_select_handle_moves_the_selection():
    scr = FleetScreen(lambda: SNAP)
    scr._current = SNAP
    scr.select_handle("s4")
    assert scr.selected == 5


def test_the_frame_advances_without_rebuilding_the_snapshot():
    built = []

    def snap(**_kw):
        built.append(1)
        return SNAP

    scr = FleetScreen(snap)
    scr._current = SNAP
    scr.advance_frame()
    scr.advance_frame()
    assert scr.frame == 2 and built == []
```

Keep and run the pilot tests at lines 207–539 (F10 opens, number keys open tabs, escape closes, burst coalescing, observers released, band system row, opaque modal); fix any that query `#fleet-grid` to query `#fleet-band`/`#fleet-list` instead. Add one pilot test: open F10 with two sessions, press `down`, assert `#fleet-detail-body` renders the second session's handle; press `enter`, assert the active tab is the second session.

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest -q tests/test_fleet_screen.py`
Expected: FAIL (`FleetScreen` has no `rotator`)

- [ ] **Step 3: Rewrite `fleet_screen.py`** per the Behaviour list. Keep the module docstring's opening sentence and replace the grid paragraph with: the screen composes a band, a list and a detail; the renderers are pure and take a frame number; two clocks drive it, the 1 s snapshot and the 0.5 s frame; a rotator chooses the detail while nobody drives the screen.

`system_row` keeps its signature; `AegisApp._fleet_system_row` (Task 7) now returns `{"stats": SystemStats | None, "gauges": tuple[QuotaGauge, ...], "build": tuple[str, ...]}`, which `refresh_fleet` spreads into `replace(snap.band, clock=..., **row)` exactly as today.

- [ ] **Step 4: Delete the dead grid code** from `render.py` and run `grep -rn 'render_card\|render_fleet\|columns_for\|CARD_WIDTH\|card_rects' src tests` — expected: no output.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest -q tests/test_fleet_screen.py tests/test_fleet_card_restore.py tests/test_fleet_dash.py tests/test_fleet_watching.py tests/test_fleet_v2_band.py tests/test_fleet_v2_panes.py`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
uv run ruff format src/aegis/tui/fleet_screen.py src/aegis/fleet/render.py
git rm -q tests/test_fleet_render.py
git commit -F - -- src/aegis/tui/fleet_screen.py src/aegis/fleet/render.py tests/test_fleet_screen.py tests/test_fleet_render.py tests/test_fleet_card_restore.py <<'EOF'
feat(tui): F10 v2, a gauge band, a session list and a detail pane

The card grid is gone. Items keep did/now whole, the detail shows the
selected session's monitors with ETA, motion runs on a 0.5 s frame clock,
and a rotator takes the detail over after two minutes without input.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 7: The app hands raw data and pending attention

**Files:**
- Modify: `src/aegis/tui/app.py` (`_tick`, `_quota_tick`, `_fleet_system_row`, `_fleet_snapshot`)
- Test: `tests/test_fleet_app_wiring.py`

**Interfaces:**
- Consumes: `SystemStats`, `quota_gauges`, `aegis.attention.is_pending`, `ConversationPane.attention_acked`
- Produces:
  - `AegisApp._system_stats: SystemStats | None` (set in `_tick` next to `_system_last`)
  - `_fleet_system_row() -> {"stats": ..., "gauges": ..., "build": ...}`
  - `_fleet_snapshot` sets `CardView.attention` to the category pending in this view (`""` otherwise)

- [ ] **Step 1: Write the failing tests**

```python
"""F10 reads the app's own numbers and this view's seen-ness."""

from types import SimpleNamespace

from aegis.fleet.models import CardView, FleetSnapshot


def test_pending_attention_is_per_view():
    from aegis.tui.app import _with_attention

    snap = FleetSnapshot(cards=(CardView(handle="a"), CardView(handle="b")))
    cores = {
        "a": SimpleNamespace(effective_attention="needs_input", attention_seq=2),
        "b": SimpleNamespace(effective_attention="review", attention_seq=1),
    }
    acked = {"a": 1, "b": 1}
    got = _with_attention(snap, cores, acked)
    assert [c.attention for c in got.cards] == ["needs_input", ""]
```

Add a pilot test modelled on `tests/test_fleet_screen.py::test_the_build_shows_before_the_first_tick` (read it): with `AegisApp._quota_tick` stubbed, after one `_tick`, `app._fleet_system_row()["stats"]` is a `SystemStats` and `"gauges"` is a tuple.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q tests/test_fleet_app_wiring.py`
Expected: FAIL (`cannot import name '_with_attention'`)

- [ ] **Step 3: Implement**

Module-level in `app.py`:

```python
def _with_attention(snapshot, cores: dict, acked: dict):
    """Each live card's category as pending in THIS view, or ``""``.
    Seen-ness is per view, so the brain's snapshot cannot know it."""
    from dataclasses import replace

    from aegis.attention import is_pending

    cards = []
    for c in snapshot.cards:
        core = cores.get(c.handle)
        category = getattr(core, "effective_attention", "") or ""
        seq = getattr(core, "attention_seq", 0)
        pending = is_pending(category, seq, acked.get(c.handle, 0))
        cards.append(replace(c, attention=category if pending else ""))
    return replace(snapshot, cards=tuple(cards))
```

In `_fleet_snapshot`, after `in_tab_order`, build `cores` and `acked` from `self._panes` (`p._core` and `p.attention_acked` for `ConversationPane`s) and return `_with_attention(ordered, cores, acked)`.

In `_tick`, inside the existing `with contextlib.suppress(Exception):` after `stats = sample_system(self._cwd)`, add `self._system_stats = stats`; initialise `_system_stats = None` next to `_quota_last = None` at class level.

`_fleet_system_row`:

```python
    def _fleet_system_row(self) -> dict:
        """The band's raw numbers: the last tick's system sample, every
        provider's last quota reading as gauges, and the build."""
        from datetime import datetime, timezone

        from aegis.tui.sysmeter import format_build
        from aegis.usage.quota import quota_gauges
        from aegis.usage.quota_providers import PROVIDERS

        services = getattr(self, "quota_services", {}) or {}
        readings = [(p, services[p.name].current()) for p in PROVIDERS if p.name in services]
        return {
            "stats": self._system_stats,
            "gauges": quota_gauges(readings, now=datetime.now(timezone.utc)),
            "build": format_build(self._palette),
        }
```

Remove `system` and `quota` from `BandView` only if nothing else reads them (`grep -rn 'band.system\|band.quota' src tests`); otherwise leave them.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest -q tests/test_fleet_app_wiring.py tests/test_fleet_screen.py`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/aegis/tui/app.py
git add tests/test_fleet_app_wiring.py
git commit -F - -- src/aegis/tui/app.py tests/test_fleet_app_wiring.py <<'EOF'
feat(tui): F10 gets raw system and quota numbers and per-view attention

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 8: Docs, gate, and the real screen against the mockup

**Files:**
- Modify: `CHANGELOG.md` (rewrite the Unreleased F10 entry)
- Modify: `docs/usage.md` (the F10 section)
- Modify: `docs/superpowers/specs/2026-09-17-aegis-fleet-dashboard-v2-design.md` (status)
- Create: `.playground/fleet-render/shoot_f10_v2.py` (workspace playground, not committed)

- [ ] **Step 1: CHANGELOG.** Replace the first paragraph of the Unreleased `F10 shows every session at once` entry with the v2 description: band of host and quota gauges and counters by attention; a list whose items keep did/now whole; a detail pane with monitors and ETA, plan, activity and spend; pulse, blink and sweep; auto rotation after two minutes without input, at most one switch every 20 s, preferring sessions that need you. Keep the cost paragraph.

- [ ] **Step 2: usage.md.** Update the F10 section: keys (`↑↓`, `enter`, `1`–`9`, click), auto mode and its pace, what each band row shows, the narrow layout under 110 columns.

- [ ] **Step 3: Gate.**

Run: `make test`
Expected: rc 0, then `make check` with `ty` at its pre-plan count.

- [ ] **Step 4: See it.** Write `.playground/fleet-render/shoot_f10_v2.py` from `.playground/fleet-render/shoot_f10.py`: stub `AegisApp._quota_tick`; give the app five sessions matching the mockup (working with a monitor at 60% and one without progress; `needs_input`; `error`; `review`; an ephemeral queue ghost); set `app._system_stats` and stub `quota_services` with `QuotaService`-shaped objects whose `current()` returns fixed `QuotaState`s; open F10; export SVG and PNG at 220×55 and at 100×45, at frame 0 and frame 1. Read the PNGs and compare with `layout-v4.html` and `attention-both-screens.html` screen 2: band rows, list at 44%, three-line items with whole did/now, detail section order, monitor bars with ETA, the narrow stacking. List every difference found and fix those that contradict the spec before calling the task done.

- [ ] **Step 5: Status and commit.** Set the v2 spec status to `Implemented 2026-09-17, per docs/superpowers/plans/2026-09-17-aegis-fleet-dashboard-v2.md.`

```bash
git commit -F - -- CHANGELOG.md docs/usage.md docs/superpowers/specs/2026-09-17-aegis-fleet-dashboard-v2-design.md docs/superpowers/plans/2026-09-17-aegis-fleet-dashboard-v2.md <<'EOF'
docs(fleet): F10 v2 in the changelog and usage, spec marked implemented

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git push
```
