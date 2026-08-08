# F3 Side Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `F3` toggle a full-height sidebar that holds the plan, queues, monitors and every status-bar segment, leaving the main column as transcript and input.

**Architecture:** A pure `render_sidebar(model, palette, width) -> Text` plus a thin `Sidebar(VerticalScroll)` widget, in the pure-renderer-plus-widget shape every strip in this codebase already uses. The mode switch is one CSS class on `ConversationPane`; the collapsed mode is byte-for-byte today's pane. `PlanDock` is absorbed — its section calls `render_plan_dock` verbatim rather than re-implementing rows.

**Tech Stack:** Python 3.13+, Textual 8.x, Rich, pytest (`uv run python -m pytest`).

**Spec:** `docs/superpowers/specs/2026-08-07-aegis-f3-side-dashboard-design.md`

## Global Constraints

- Python 3.13+. Package management is `uv` — `uv pip install -e .`, `uv run python -m pytest`. Never `pip`.
- TDD: failing test first, minimal implementation, commit per logical unit.
- Run the fast suite as `uv run python -m pytest -q -m "not live"`. **Never** `-k "not live"` — it matches `live` as a substring and silently eats unrelated test names.
- **A failing test is a real failure.** The suite does not flake since 0.25.0; do not re-roll a red run.
- Pure renderers take no Textual import and read no clock. Every function that needs a time takes it as an explicit parameter — that is what makes a replayed log reproduce live numbers.
- Widths are measured in **cells**, not `len()`. Use `rich.cells.cell_len` (or `fit.plain_width` for markup-bearing ASCII segments). One emoji is one character and two columns.
- Scope is the **TUI only**. Do not touch `src/aegis/web/` or `src/aegis/web/static/`.
- Commit messages: conventional commits, scope `sidebar`. End every commit message with:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- Stage explicit paths. Never `git add -A`, `git add .`, or `git add -u` — this is a shared checkout with concurrent agents.

## File Structure

| File | Responsibility |
|---|---|
| `src/aegis/tui/fit.py` *(modify)* | Add `fit_rows` beside `fit`. Pure width composition — one line vs one row per segment. |
| `src/aegis/tui/sidebar.py` *(create)* | `SidebarModel`, the pure section renderers, `render_sidebar`, and the `Sidebar` widget. |
| `src/aegis/tui/strip.py` *(modify)* | Publish `format_q` (was `_format_q`) so the sidebar's QUEUES section shares the strip's queue formatting. |
| `src/aegis/tui/monitor_strip.py` *(modify)* | Publish `format_mon` (was `_format_mon`) for the same reason. |
| `src/aegis/tui/plan_strip.py` *(modify)* | Switch from imperative `self.display` to the `-empty` class idiom. **Required** — see the trap in Task 4. |
| `src/aegis/tui/plan_dock.py` *(delete)* | Absorbed by `sidebar.py`. |
| `src/aegis/tui/pane.py` *(modify)* | Layout, the class toggle, and the push sites that now feed the sidebar too. |
| `src/aegis/tui/app.py` *(modify)* | The `F3` binding label. |
| `tests/test_statusbar_fit.py` *(modify)* | `fit_rows` cases, beside the existing `fit` cases for the same module. |
| `tests/test_sidebar_render.py` *(create)* | The pure renderer: sections, order, omission, width degradation. |
| `tests/test_sidebar_toggle.py` *(create)* | The mode switch, asserted on real widget visibility in a live app. |

---

### Task 1: `fit_rows` — the pure vertical fitter

`fit()` composes segments into one line and degrades by priority until it
fits. `fit_rows` gives each segment its own row and picks, per segment, the
widest tier that fits the column. `priority` is unused — in a vertical
layout segments do not compete for the same space, so there is nothing to
rank. The field stays on `Segment` because both functions consume the same
values.

**Files:**
- Modify: `src/aegis/tui/fit.py`
- Test: `tests/test_statusbar_fit.py`

**Interfaces:**
- Consumes: `Segment(key, tiers, priority)` and `plain_width`, both already in `fit.py`.
- Produces: `fit_rows(segments: Sequence[Segment], width: int) -> list[str]`.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_statusbar_fit.py`:

```python
from aegis.tui.fit import fit_rows


def _rowsegs():
    return [
        Segment("identity", ("aegis 0.32.0 opus high", "opus high", "opus"), 20),
        Segment("metrics", ("142k/200k · $1.84 · 12 turns", "$1.84"), 30),
        Segment("empty", (), 50),
    ]


def test_rows_unmeasured_width_renders_widest():
    assert fit_rows(_rowsegs(), 0) == [
        "aegis 0.32.0 opus high", "142k/200k · $1.84 · 12 turns"]


def test_rows_picks_the_widest_tier_that_fits():
    # 26 columns: identity's widest is 22 and fits; metrics' widest is 28
    # and does not, so it falls to its second tier.
    assert fit_rows(_rowsegs(), 26) == ["aegis 0.32.0 opus high", "$1.84"]


def test_rows_keeps_a_tier_that_is_exactly_the_width():
    seg = [Segment("exact", ("abcde",), 10)]
    assert fit_rows(seg, 5) == ["abcde"]


def test_rows_drops_a_segment_whose_narrowest_still_overflows():
    """Half a number is worse than no number — the segment goes."""
    seg = [Segment("wide", ("aaaaaaaa", "aaaaaa"), 10),
           Segment("fits", ("ok",), 10)]
    assert fit_rows(seg, 4) == ["ok"]


def test_rows_skips_a_segment_with_no_tiers():
    assert fit_rows([Segment("none", (), 10)], 80) == []


def test_rows_ignores_markup_when_measuring():
    seg = [Segment("m", ("[dim]12345[/]",), 10)]
    assert fit_rows(seg, 5) == ["[dim]12345[/]"]
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_statusbar_fit.py -q`
Expected: FAIL — `ImportError: cannot import name 'fit_rows'`.

- [x] **Step 3: Write the implementation**

Append to `src/aegis/tui/fit.py`:

```python
def fit_rows(segments: Sequence[Segment], width: int) -> list[str]:
    """Compose ``segments`` one per row, each at the widest tier that fits.

    The vertical counterpart of ``fit``. Because rows do not share
    horizontal space, segments do not compete and ``priority`` is not
    consulted — each is considered on its own. A segment whose *narrowest*
    tier still overflows is dropped rather than truncated: half a number
    reads as a number and is worse than no row at all.

    ``width <= 0`` means "unmeasured" (the widget has not been laid out
    yet) and renders every segment at tier 0, matching ``fit``.
    """
    rows: list[str] = []
    for seg in segments:
        tiers = [t for t in seg.tiers if t]
        if not tiers:
            continue
        if width <= 0:
            rows.append(tiers[0])
            continue
        pick = next((t for t in tiers if plain_width(t) <= width), None)
        if pick is not None:
            rows.append(pick)
    return rows
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_statusbar_fit.py -q`
Expected: PASS, including the pre-existing `fit` tests.

- [x] **Step 5: Commit**

```bash
git add src/aegis/tui/fit.py tests/test_statusbar_fit.py
git commit -m "feat(sidebar): fit_rows — one segment per row, widest tier that fits

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `SidebarModel` and the composition frame

The dataclass carrying everything the sidebar renders, plus
`render_sidebar` with its section ordering, blank-line joining and
empty-section omission — proved with the two sections that use `fit_rows`
(SESSION and CONTEXT). The remaining four sections land in Task 3.

**Files:**
- Create: `src/aegis/tui/sidebar.py`
- Test: `tests/test_sidebar_render.py`

**Interfaces:**
- Consumes: `fit_rows`, `Segment` (Task 1); `MonitorView` from `aegis.monitor.schema`; `PlanState` from `aegis.plan.models`; `Snapshot` from `aegis.queue.digest`; `AegisColors` from `aegis.themes`.
- Produces:
  - `SidebarModel` — the dataclass below, every field defaulted.
  - `render_sidebar(model: SidebarModel, palette, width: int) -> Text`.
  - `heading(text: str, palette, width: int, right: str = "") -> Text`.
  - `SECTIONS: tuple[Callable[[SidebarModel, object, int], Text | None], ...]` — the ordered section renderers; Task 3 extends it.

- [x] **Step 1: Write the failing tests**

Create `tests/test_sidebar_render.py`:

```python
"""The pure sidebar renderer.

Sections are ordered by volatility, highest first: on a short terminal the
panel scrolls, and what you see without scrolling should be what moves.
An empty section renders nothing at all — not a heading over a blank.
"""
from aegis.tui.sidebar import SidebarModel, heading, render_sidebar
from aegis.tui.themes import INK, aegis_colors

C = aegis_colors(INK)          # house pattern — see tests/test_render_event.py


def as_text(renderable) -> str:
    return renderable.plain


def test_an_empty_model_renders_nothing():
    assert as_text(render_sidebar(SidebarModel(), C, 40)) == ""


def test_session_section_renders_title_identity_and_state():
    m = SidebarModel(title="fix the eviction race",
                     identity=("opus · high · local",),
                     state_label="✻ working…")
    out = as_text(render_sidebar(m, C, 40))
    assert "SESSION" in out
    assert "fix the eviction race" in out
    assert "opus · high · local" in out
    assert "✻ working…" in out


def test_a_section_with_no_content_omits_its_heading():
    """CONTEXT has no metrics and no quota, so the word never appears."""
    m = SidebarModel(state_label="idle")
    out = as_text(render_sidebar(m, C, 40))
    assert "SESSION" in out
    assert "CONTEXT" not in out


def test_sections_are_separated_by_one_blank_row():
    m = SidebarModel(state_label="idle", metrics=("$1.84",))
    lines = as_text(render_sidebar(m, C, 40)).split("\n")
    assert "" in lines
    assert lines.count("") == 1


def test_connection_warning_leads_the_session_section():
    """A disconnected session is a fact about the session, and burying it
    under its own heading at some scroll offset would be worse than the
    status bar it replaces."""
    m = SidebarModel(state_label="idle",
                     connection=("⚠ disconnected — reconnecting…",
                                 "⚠ disconnected"))
    lines = [ln for ln in as_text(render_sidebar(m, C, 40)).split("\n") if ln]
    assert lines[0] == "SESSION"
    assert lines[1].startswith("⚠ disconnected")


def test_a_narrow_column_takes_a_narrower_tier():
    m = SidebarModel(connection=("⚠ disconnected — reconnecting…",
                                 "⚠ disconnected"),
                     state_label="idle")
    assert "⚠ disconnected — reconnecting…" in as_text(
        render_sidebar(m, C, 40))
    assert "⚠ disconnected — reconnecting…" not in as_text(
        render_sidebar(m, C, 20))
    assert "⚠ disconnected" in as_text(render_sidebar(m, C, 20))


def test_heading_right_aligns_its_counter():
    assert as_text(heading("PLAN", C, 20, right="3/7")) == \
        "PLAN             3/7"


def test_heading_without_a_counter_is_just_the_word():
    assert as_text(heading("SESSION", C, 20)) == "SESSION"
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_sidebar_render.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.tui.sidebar'`.

- [x] **Step 3: Write the implementation**

Create `src/aegis/tui/sidebar.py`:

```python
"""Sidebar — the F3 dashboard column.

F3 toggles a *mode*, not a widget. Open, this column holds every ambient
surface a pane has (plan, queues, monitors, and all eight status-bar
segments) and the main column is transcript and input. Closed, the pane is
byte-for-byte what it was before this file existed.

The four collapsed surfaces each solve the same problem separately — too
much state, one row — and each solves it by throwing information away.
A terminal is usually much taller than it is full; this spends the
vertical axis instead.

Two pieces, the shape every strip here already uses:
* ``render_sidebar(model, palette, width)`` — pure Rich Text renderer.
* ``Sidebar`` — the Textual widget (Task 4).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from rich.cells import cell_len
from rich.text import Text

from aegis.monitor.schema import MonitorView
from aegis.plan.models import PlanState
from aegis.tui.fit import Segment, fit_rows


@dataclass
class SidebarModel:
    """Everything the sidebar renders, assembled from the sources the
    strips and the status bar already read. No new data path."""

    # SESSION
    connection: tuple[str, ...] = ()
    title: str = ""
    identity: tuple[str, ...] = ()
    state_label: str = ""
    loop: tuple[str, ...] = ()
    # CONTEXT
    metrics: tuple[str, ...] = ()
    quota: tuple[str, ...] = ()
    # PLAN
    plan: PlanState | None = None
    subplans: dict = field(default_factory=dict)
    plan_working: bool = False
    plan_frame: int = 0
    # QUEUES
    queues: object | None = None          # aegis.queue.digest.Snapshot
    # MONITORS
    monitors: list[MonitorView] = field(default_factory=list)
    # SYSTEM
    system: tuple[str, ...] = ()


def heading(text: str, palette, width: int, right: str = "") -> Text:
    """A section heading, optionally with a right-aligned counter.

    Padded in cells rather than characters — a counter is ASCII but the
    budget it is padded against is shared with rows that are not.
    """
    out = Text(text, style=f"bold {palette.muted}")
    if right:
        pad = width - cell_len(text) - cell_len(right)
        if pad >= 1:
            out.append(" " * pad)
            out.append(right, style=palette.muted)
    return out


def _rows(segments, palette, width: int) -> list[Text]:
    return [Text.from_markup(r) for r in fit_rows(segments, width)]


def _session(m: SidebarModel, palette, width: int) -> Text | None:
    segs = [
        Segment("connection", m.connection, 0),
        Segment("title", (m.title,) if m.title else (), 0),
        Segment("identity", m.identity, 0),
        Segment("state", (m.state_label,) if m.state_label else (), 0),
        Segment("loop", m.loop, 0),
    ]
    rows = _rows(segs, palette, width)
    if not rows:
        return None
    return _block(heading("SESSION", palette, width), rows)


def _context(m: SidebarModel, palette, width: int) -> Text | None:
    segs = [Segment("metrics", m.metrics, 0),
            Segment("quota", m.quota, 0)]
    rows = _rows(segs, palette, width)
    if not rows:
        return None
    return _block(heading("CONTEXT", palette, width), rows)


def _block(head: Text, rows: list[Text]) -> Text:
    out = Text()
    out.append_text(head)
    for r in rows:
        out.append("\n")
        out.append_text(r)
    return out


# Ordered by volatility, highest first. Task 3 appends the remaining four.
SECTIONS: tuple[Callable[[SidebarModel, object, int], Text | None], ...] = (
    _session, _context,
)


def render_sidebar(model: SidebarModel, palette, width: int) -> Text:
    """The whole column. Sections that render ``None`` contribute nothing —
    not a heading, not a blank row."""
    blocks = [b for b in (s(model, palette, width) for s in SECTIONS)
              if b is not None]
    out = Text()
    for i, b in enumerate(blocks):
        if i:
            out.append("\n\n")
        out.append_text(b)
    return out
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_sidebar_render.py -q`
Expected: PASS (8 tests).

- [x] **Step 5: Commit**

```bash
git add src/aegis/tui/sidebar.py tests/test_sidebar_render.py
git commit -m "feat(sidebar): SidebarModel and the composition frame

Sections ordered by volatility; an empty section renders nothing at all.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The remaining four sections — PLAN, QUEUES, MONITORS, SYSTEM

Each reuses the renderer that already draws that thing for its strip, so a
change to a glyph or a bar shows up in both surfaces. PLAN in particular
calls `render_plan_dock` **verbatim** — it already solves the East-Asian-
Ambiguous circle spacing and the `width - 9` label budget, and a fresh
implementation would re-pay both bugs.

**Files:**
- Modify: `src/aegis/tui/sidebar.py`
- Modify: `src/aegis/tui/strip.py` (publish `format_q`)
- Modify: `src/aegis/tui/monitor_strip.py` (publish `format_mon`)
- Test: `tests/test_sidebar_render.py`

**Interfaces:**
- Consumes: `render_plan_dock(state, colors, *, working, frame, width, subplans) -> Text` from `aegis.plan.render`; `Snapshot`/`QueueView` from `aegis.queue.digest`; `MonitorView` from `aegis.monitor.schema`.
- Produces: `format_q(q: QueueView, palette) -> Text` in `strip.py`; `format_mon(v: MonitorView, palette) -> Text` in `monitor_strip.py`; `SECTIONS` extended to six.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_sidebar_render.py`:

```python
from aegis.monitor.schema import MonitorView
from aegis.plan import PlanState, PlanTask
from aegis.queue.digest import QueueView, Snapshot
from aegis.tui.sidebar import SidebarModel, render_sidebar


def _plan():
    # PlanTask is the *tracker* model — key/subject/status, and `tasks` is a
    # tuple because PlanState is frozen. Not PlanEntry, which is the parsed
    # event shape and does use `content`.
    return PlanState(tasks=(
        PlanTask(key="1", subject="parse the header", status="completed"),
        PlanTask(key="2", subject="writing the parser", status="in_progress"),
        PlanTask(key="3", subject="wire the strip", status="pending"),
    ))


def test_plan_section_shows_the_count_in_its_heading():
    out = as_text(render_sidebar(SidebarModel(plan=_plan()), C, 40))
    assert "PLAN" in out
    assert "1/3" in out


def test_plan_section_lists_the_tasks():
    out = as_text(render_sidebar(SidebarModel(plan=_plan()), C, 40))
    assert "writing the parser" in out


def test_queues_section_lists_each_queue():
    snap = Snapshot(queues=[
        QueueView(name="build", agent="opus", max_parallel=2,
                  running=1, queued=3, ok=5, err=0),
        QueueView(name="review", agent="opus", max_parallel=1,
                  running=0, queued=0, ok=0, err=0),
    ])
    out = as_text(render_sidebar(SidebarModel(queues=snap), C, 40))
    assert "QUEUES" in out
    assert "build" in out and "review" in out
    assert "●1" in out


def test_monitors_section_shows_the_bar():
    v = MonitorView(id="m1", description="pytest", state="running",
                    pct=62.0, eta_s=100.0, elapsed_s=30.0)
    out = as_text(render_sidebar(SidebarModel(monitors=[v]), C, 40))
    assert "MONITORS" in out
    assert "pytest" in out
    assert "62%" in out


def test_system_section_is_last():
    m = SidebarModel(state_label="idle", system=("cpu 34% ram 61%",))
    lines = [ln for ln in as_text(render_sidebar(m, C, 40)).split("\n") if ln]
    assert lines[0] == "SESSION"
    assert "SYSTEM" in lines
    assert lines.index("SYSTEM") == len(lines) - 2


def test_full_model_renders_every_section_in_volatility_order():
    m = SidebarModel(
        title="fix the eviction race", identity=("opus · high",),
        state_label="✻ working…", metrics=("$1.84",), plan=_plan(),
        queues=Snapshot(queues=[QueueView(
            name="build", agent="opus", max_parallel=2,
            running=1, queued=0, ok=0, err=0)]),
        monitors=[MonitorView(id="m1", description="pytest", state="running",
                              pct=62.0, eta_s=None, elapsed_s=30.0)],
        system=("cpu 34%",))
    lines = [ln for ln in as_text(render_sidebar(m, C, 40)).split("\n") if ln]
    heads = [ln.split()[0] for ln in lines
             if ln.split() and ln.split()[0].isupper()]
    assert heads == ["SESSION", "CONTEXT", "PLAN", "QUEUES",
                     "MONITORS", "SYSTEM"]
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_sidebar_render.py -q`
Expected: FAIL — the six new tests fail on missing sections (`assert "PLAN" in out`); the eight from Task 2 still pass.

- [x] **Step 3: Publish the two shared formatters**

In `src/aegis/tui/strip.py`, rename `_format_q` to `format_q` and update
its one caller inside `render_strip`:

```python
def format_q(q: QueueView, palette) -> Text:
    """One queue's counters. Shared with the sidebar's QUEUES section, so a
    glyph change shows in both surfaces."""
    t = Text()
    t.append(q.name, style=palette.ink)
    t.append(f" ●{q.running}", style=palette.work)
    t.append(f"/{q.max_parallel}", style=palette.muted)
    if q.queued:
        t.append(f" ○{q.queued}", style=palette.muted)
    if q.ok:
        t.append(f" ✓{q.ok}", style=palette.ok)
    if q.err:
        t.append(f" ✗{q.err}", style=palette.err)
    return t
```

and in `render_strip` change `_format_q(q, palette)` to `format_q(q, palette)`
(one occurrence, in the `n <= 3` branch).

In `src/aegis/tui/monitor_strip.py`, rename `_format_mon` to `format_mon`
with the same body, and update its one caller inside `render_monitors`
(`out.append_text(_format_mon(v, palette))` → `format_mon(v, palette)`).

- [x] **Step 4: Write the four section renderers**

In `src/aegis/tui/sidebar.py`, add these imports at the top:

```python
from aegis.plan.render import render_plan_dock
from aegis.tui.monitor_strip import format_mon
from aegis.tui.strip import format_q
```

and add the sections before `SECTIONS`:

```python
def _plan(m: SidebarModel, palette, width: int) -> Text | None:
    if not m.plan and not m.subplans:
        return None
    # PlanState already computes both — do not re-derive them.
    done = m.plan.done if m.plan else 0
    total = m.plan.total if m.plan else 0
    # render_plan_dock verbatim: it already space-separates the circles
    # (East Asian Ambiguous — Rich measures one cell, terminals draw two)
    # and budgets labels at width - 9. Re-implementing rows here re-pays
    # both bugs.
    body = render_plan_dock(m.plan or PlanState(), palette,
                            working=m.plan_working, frame=m.plan_frame,
                            width=width, subplans=m.subplans)
    head = heading("PLAN", palette, width,
                   right=f"{done}/{total}" if total else "")
    return _block(head, [body])


def _queues(m: SidebarModel, palette, width: int) -> Text | None:
    snap = m.queues
    if snap is None or not snap.queues:
        return None
    return _block(heading("QUEUES", palette, width),
                  [format_q(q, palette) for q in snap.queues])


def _monitors(m: SidebarModel, palette, width: int) -> Text | None:
    if not m.monitors:
        return None
    # One monitor per row rather than sharing a line — the strip's rule,
    # for the same reason: a long description must not push another
    # monitor's bar off the edge.
    return _block(heading("MONITORS", palette, width),
                  [format_mon(v, palette) for v in m.monitors])


def _system(m: SidebarModel, palette, width: int) -> Text | None:
    rows = _rows([Segment("system", m.system, 0)], palette, width)
    if not rows:
        return None
    return _block(heading("SYSTEM", palette, width), rows)
```

and extend the tuple:

```python
SECTIONS: tuple[Callable[[SidebarModel, object, int], Text | None], ...] = (
    _session, _context, _plan, _queues, _monitors, _system,
)
```

- [x] **Step 5: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_sidebar_render.py tests/test_tui_strip.py tests/test_monitor_strip.py tests/test_plan_render.py -q`
Expected: PASS. The strip tests confirm the two renames did not change strip output.

- [x] **Step 6: Commit**

```bash
git add src/aegis/tui/sidebar.py src/aegis/tui/strip.py \
        src/aegis/tui/monitor_strip.py tests/test_sidebar_render.py
git commit -m "feat(sidebar): PLAN, QUEUES, MONITORS and SYSTEM sections

PLAN calls render_plan_dock verbatim — it already solves the circle
spacing and the width-9 label budget. format_q and format_mon go public
so a glyph change shows in both the strip and the sidebar.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The widget, the layout and the mode switch

`Sidebar` replaces `PlanDock`; the pane's compose becomes a two-column
`Horizontal`; `F3` toggles one class. After this task the sidebar works and
shows the plan — the rest of the data moves in in Task 5.

**The trap that makes this task fail if skipped:** `PlanStrip` sets
`self.display = bool(state)` imperatively (`plan_strip.py:49`), and an
imperative `display` is an **inline style that beats CSS**. So
`ConversationPane.-sidebar PlanStrip { display: none }` would be silently
overridden every time a plan updates. `PlanStrip` must move to the
`-empty` class idiom its two sibling strips already use. `QueueStrip`,
`MonitorStrip` and `StatusBar` set no inline display and need no change
(verified across `src/aegis/tui/*.py`).

**Files:**
- Modify: `src/aegis/tui/sidebar.py` (add the widget)
- Modify: `src/aegis/tui/plan_strip.py`
- Modify: `src/aegis/tui/pane.py` (`DEFAULT_CSS`, `compose`, `toggle_task_dock`, `_refresh_plan_surfaces`, `set_palette`)
- Modify: `src/aegis/tui/app.py:279` (binding label)
- Modify: `AGENTS.md` (the `src/aegis/tui/` layout entry)
- Delete: `src/aegis/tui/plan_dock.py`
- Test: `tests/test_sidebar_toggle.py`

**Interfaces:**
- Consumes: `render_sidebar`, `SidebarModel` (Tasks 2–3).
- Produces:
  - `Sidebar(palette, **kw)` — a `VerticalScroll` with `toggle() -> bool`, `is_open: bool`, and `refresh_model(model: SidebarModel) -> None`.
  - `ConversationPane.toggle_task_dock() -> bool` — unchanged name and signature, now toggling the sidebar.

- [x] **Step 1: Write the failing tests**

Create `tests/test_sidebar_toggle.py`:

```python
"""F3 toggles a mode, not a widget.

These assertions are on real widget visibility, never on the presence of
the CSS class: a class-name assertion passes against a rule that no longer
hides anything, which is exactly the failure the rule exists to prevent.
Step 6 mutation-checks that.
"""
from __future__ import annotations

import pytest
from aegis.config import Agent
from aegis.events import AssistantText, Result
from aegis.tui.app import AegisApp
from aegis.tui.monitor_strip import MonitorStrip
from aegis.tui.plan_strip import PlanStrip
from aegis.tui.sidebar import Sidebar
from aegis.tui.strip import QueueStrip
from aegis.tui.widgets import StatusBar


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    session_id = "sid-1"

    def __init__(self):
        self.sent = []

    async def start(self): pass
    async def send(self, text): self.sent.append(text)

    async def events(self):
        yield AssistantText("ok")
        yield Result(duration_ms=1, is_error=False)

    async def close(self): pass


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): self.bound = bridge
    async def start(self): pass
    async def stop(self): pass


def _app():
    def make(agent, mcp_url, handle, **kw):
        return FakeSession()
    return AegisApp({"default": _agent()}, "default", make, FakeMCP())


COLLAPSED = (QueueStrip, MonitorStrip, PlanStrip, StatusBar)


@pytest.mark.asyncio
async def test_closed_is_todays_pane():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await pilot.pause()
        assert not pane.query_one(Sidebar).display
        assert pane.query_one(StatusBar).display


@pytest.mark.asyncio
async def test_opening_shows_the_sidebar_and_hides_every_collapsed_surface():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()

        assert pane.query_one(Sidebar).display
        still_visible = [w.__class__.__name__
                         for cls in COLLAPSED
                         for w in pane.query(cls) if w.display]
        assert still_visible == []


@pytest.mark.asyncio
async def test_closing_restores_every_collapsed_surface():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        pane.toggle_task_dock()
        await pilot.pause()

        assert not pane.query_one(Sidebar).display
        assert pane.query_one(StatusBar).display


@pytest.mark.asyncio
async def test_a_plan_update_cannot_reopen_a_hidden_plan_strip():
    """PlanStrip used to set .display imperatively, and an inline style
    beats CSS — so a plan arriving while the sidebar was open would put
    the strip back on screen underneath it."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        pane.query_one(PlanStrip).refresh_plan(
            _one_task_plan(), working=True)
        await pilot.pause()
        assert not pane.query_one(PlanStrip).display


def _one_task_plan():
    from aegis.plan import PlanState, PlanTask
    return PlanState(tasks=(
        PlanTask(key="1", subject="a", status="in_progress"),))
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_sidebar_toggle.py -q`
Expected: FAIL — `ImportError: cannot import name 'Sidebar'`.

- [x] **Step 3: Add the widget**

Append to `src/aegis/tui/sidebar.py`:

```python
from textual.containers import VerticalScroll
from textual.widgets import Static

_TICK = 0.25
# Proportional, not a magic number, and inherited from the dock this
# replaces: across 344 real task subjects the median is 35 characters and
# p90 is 49, so no fixed width works. A share of the pane adapts, and the
# bounds keep it useful on an 80-col terminal without eating a 200-col one.
SIDEBAR_PCT = 34
SIDEBAR_MIN = 26
SIDEBAR_MAX = 60


class Sidebar(VerticalScroll):
    """The F3 column. Scrolls: fully populated it is ~25 rows and an 80x24
    terminal has about twenty to give."""

    DEFAULT_CSS = f"""
    Sidebar {{
        width: {SIDEBAR_PCT}%;
        min-width: {SIDEBAR_MIN};
        max-width: {SIDEBAR_MAX};
        padding: 0 1;
        scrollbar-size: 0 0;
    }}
    """

    def __init__(self, palette, **kw) -> None:
        super().__init__(**kw)
        self._palette = palette
        self._model = SidebarModel()
        self._body = Static("")
        self._open = False
        self._paints = 0        # test seam for the closed-mode no-op
        self.display = False

    def compose(self):
        yield self._body

    def on_mount(self) -> None:
        self.set_interval(_TICK, self._tick)

    def set_palette(self, palette) -> None:
        self._palette = palette
        if self._open:
            self._paint()

    def on_resize(self) -> None:
        """Repaint at the real width.

        A widget that was display:none has not been laid out, so size.width
        is still 0 and the first paint after a toggle falls back to the
        minimum — truncating far harder than the real width requires.
        """
        if self._open:
            self._paint()

    def _tick(self) -> None:
        # Repaint only while a task is actually running: a settled panel is
        # static and must not burn a redraw four times a second.
        if self._open and self._model.plan_working:
            self._model.plan_frame += 1
            self._paint()

    def toggle(self) -> bool:
        self._open = not self._open
        self.display = self._open
        if self._open:
            self._paint()
        return self._open

    @property
    def is_open(self) -> bool:
        return self._open

    def refresh_model(self, model: SidebarModel) -> None:
        """Replace the snapshot; repaint only when open.

        The model is stored either way — it is a dataclass, and dropping it
        while closed would make the first frame after a toggle stale. What
        the closed mode skips is the *render*, which is the expensive half.
        Same discipline PlanDock.refresh_plan used.
        """
        model.plan_frame = self._model.plan_frame
        self._model = model
        if self._open:
            self._paint()

    def _paint(self) -> None:
        self._paints += 1
        # `size` is already the content box — Textual excludes padding from
        # it — so the `padding: 0 1` above must NOT be subtracted again.
        self._body.update(render_sidebar(
            self._model, self._palette,
            self.size.width or SIDEBAR_MIN - 2))

    def plain(self) -> str:
        """The current column as plain text. A test seam: reaching into a
        Static's renderable couples the tests to Textual's internals."""
        return render_sidebar(
            self._model, self._palette,
            self.size.width or SIDEBAR_MIN - 2).plain
```

- [x] **Step 4: Move `PlanStrip` off the inline display**

In `src/aegis/tui/plan_strip.py`:

- add `PlanStrip.-empty { display: none; }` to `DEFAULT_CSS`;
- replace `self.display = False` in `__init__` with `self.add_class("-empty")`;
- replace `self.display = bool(state)` in `refresh_plan` with
  `self.set_class(not state, "-empty")`.

The class docstring line "Hidden (display:none) until the session has a
plan" becomes "Hidden via `-empty` until the session has a plan — an
inline `display` would beat the sidebar's CSS."

- [x] **Step 5: Rewire the pane**

In `src/aegis/tui/pane.py`:

Replace the `PlanDock` import (`from aegis.tui.plan_dock import PlanDock`)
with `from aegis.tui.sidebar import Sidebar, SidebarModel`.

Replace `compose`:

```python
    def compose(self) -> ComposeResult:
        with Horizontal(id="pane-row"):
            with Vertical(id="main-column"):
                yield VerticalScroll(id="transcript")
                if self._digest is not None:
                    yield QueueStrip(self._digest, self._palette)
                if self._monitor_manager is not None:
                    yield MonitorStrip(self._monitor_manager, self._palette,
                                       handle_of=lambda: self.handle)
                # In remote mode, agent may be None; fall back to empty
                # strings.
                _model = getattr(self._agent, "model", "") \
                    if self._agent else ""
                _eff_raw = getattr(self._agent, "effort", "") \
                    if self._agent else ""
                _eff = getattr(_eff_raw, "value", _eff_raw)  # Effort → str
                yield PlanStrip(self._palette, id="plan-strip")
                yield StatusBar(_model, _eff, self._palette)
                yield CommandPalette(self._palette)
                yield PendingStrip(self._palette)
                yield GrowingInput(placeholder="type a message…")
            yield Sidebar(self._palette, id="sidebar")
```

In `DEFAULT_CSS`, replace the `#transcript-row` rule with the new layout
plus the mode switch:

```css
    ConversationPane #pane-row { height: 1fr; }
    ConversationPane #main-column { width: 1fr; height: 1fr; }
    /* The mode switch. One class, not five imperative display flags:
       widgets toggled by hand drift out of sync with each other, a class
       cannot, and the closed mode is defined by the class's absence. */
    ConversationPane.-sidebar QueueStrip,
    ConversationPane.-sidebar MonitorStrip,
    ConversationPane.-sidebar PlanStrip,
    ConversationPane.-sidebar StatusBar { display: none; }
```

Replace `toggle_task_dock`:

```python
    def toggle_task_dock(self) -> bool:
        """Open/close the sidebar. Bound to F3 and to `/tasks`."""
        try:
            bar = self.query_one("#sidebar", Sidebar)
        except Exception:
            return False
        self._refresh_plan_surfaces()
        opened = bar.toggle()
        self.set_class(opened, "-sidebar")
        return opened
```

In `_refresh_plan_surfaces`, replace the `PlanDock` half with the sidebar:

```python
        try:
            bar = self.query_one("#sidebar", Sidebar)
        except Exception:
            return
        bar.refresh_model(self._sidebar_model())
```

and add the assembler, which Task 5 fills out:

```python
    def _sidebar_model(self) -> SidebarModel:
        """The sidebar's snapshot. Task 5 adds the status-bar segments and
        the queue/monitor sources; the plan is enough to prove the mode."""
        core = self._core
        return SidebarModel(
            plan=core.plan_state(), subplans=core.subplan_states(),
            plan_working=core.plan.working)
```

In `set_palette`, add the sidebar beside the strips:

```python
        for w in self.query(Sidebar):
            w.set_palette(palette)
```

- [x] **Step 6: Delete the dock and update the binding and docs**

```bash
git rm src/aegis/tui/plan_dock.py
```

Grep for stragglers and fix each: `grep -rn "plan_dock\|PlanDock" src tests docs AGENTS.md`.

In `src/aegis/tui/app.py:279`, change the binding label:

```python
        Binding("f3", "toggle_tasks", "Dashboard", priority=True),
```

and its docstring at `action_toggle_tasks` (`app.py:1287`) to
`"""Show or hide the active pane's sidebar (also `/tasks`)."""`.

In `AGENTS.md`, in the `src/aegis/tui/` layout entry, replace the
`PlanDock` mention with:

> `Sidebar` — the F3 dashboard column (`sidebar.py`), holding the plan,
> queues, monitors and every status-bar segment. F3 toggles a *mode*: one
> `-sidebar` class on the pane hides the four collapsed surfaces by CSS.
> `PlanStrip` therefore must not set `display` inline — an inline style
> beats the rule.

- [x] **Step 7: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_sidebar_toggle.py -q`
Expected: PASS (4 tests).

Then the blast radius: `uv run python -m pytest tests/ -q -m "not live" -k "pane or strip or plan or tui or app"`
Expected: PASS.

- [x] **Step 8: Mutation-check the toggle test**

This is the test that matters — the pure renderers will be right, but a
green suite could ship a pane with both a status bar and a sidebar on
screen. Prove the test can fail:

1. In `pane.py`, comment out the four selectors in the
   `ConversationPane.-sidebar …{ display: none; }` rule.
2. Run: `uv run python -m pytest tests/test_sidebar_toggle.py -q`
3. Expected: **FAIL** on
   `test_opening_shows_the_sidebar_and_hides_every_collapsed_surface`.
4. Restore the rule and confirm green again.

If step 3 passes, the test is vacuous — most likely it is asserting on
something other than `.display`. Fix the test before continuing.

- [x] **Step 9: Commit**

```bash
git add src/aegis/tui/sidebar.py src/aegis/tui/plan_strip.py \
        src/aegis/tui/pane.py src/aegis/tui/app.py AGENTS.md \
        tests/test_sidebar_toggle.py
git rm --cached src/aegis/tui/plan_dock.py 2>/dev/null || true
git commit -m "feat(sidebar): F3 toggles a mode; PlanDock is absorbed

One -sidebar class hides the four collapsed surfaces by CSS. PlanStrip
moves off its inline display, which would otherwise beat the rule.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Move the rest of the data in

After Task 4 the sidebar shows the plan. This task feeds it the six pushed
segments and the two subscribed sources, so the open mode carries
everything the collapsed one did.

The pane already funnels every ambient update through a small set of
methods; each grows one line. Nothing subscribes twice and no observer is
added.

**Files:**
- Modify: `src/aegis/tui/pane.py` (`_sidebar_model`, `refresh_metrics`, `refresh_title`, `set_system`, `set_quota`, `_on_core_state`, the loop push at `:1965`)
- Test: `tests/test_sidebar_toggle.py`

**Interfaces:**
- Consumes: `SidebarModel` (Task 2); `SessionMetrics.render_tiers(now) -> tuple[str, str, str, str]`; `AgentState.label -> str`; `QueueDigest.snapshot() -> Snapshot`; `MonitorManager.snapshot(for_handle=…) -> list[MonitorView]`.
- Produces: a `ConversationPane._sidebar_model()` that fills every `SidebarModel` field.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_sidebar_toggle.py`:

```python
from aegis.tui.sidebar import Sidebar


def _sidebar_text(pane) -> str:
    return pane.query_one(Sidebar).plain()


@pytest.mark.asyncio
async def test_the_open_sidebar_carries_the_status_bar_segments():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.set_system(("cpu 34% ram 61% disk 82%",))
        pane.toggle_task_dock()
        await pilot.pause()
        text = _sidebar_text(pane)
        assert "SESSION" in text
        assert "SYSTEM" in text
        assert "cpu 34%" in text


@pytest.mark.asyncio
async def test_the_open_sidebar_shows_a_monitor_armed_for_this_pane():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        app.monitor_manager.start_monitor(
            from_handle=pane.handle, description="full suite",
            done="false", autorun=False)
        await pilot.pause()
        assert "full suite" in _sidebar_text(pane)


@pytest.mark.asyncio
async def test_a_closed_sidebar_stores_but_never_renders():
    """The closed mode costs one branch per event, not a second render
    tree. The model still updates — dropping it would make the first frame
    after a toggle stale — but nothing paints."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        sidebar = pane.query_one(Sidebar)
        sidebar._paints = 0
        pane.set_system(("cpu 99%",))
        pane.refresh_metrics()
        await pilot.pause()
        assert sidebar._paints == 0
        assert sidebar._model.system == ("cpu 99%",)


@pytest.mark.asyncio
async def test_the_first_frame_after_opening_is_not_stale():
    """The corollary: an update that arrived while closed must be on
    screen the moment it opens."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.set_system(("cpu 99%",))
        pane.toggle_task_dock()
        await pilot.pause()
        assert "cpu 99%" in _sidebar_text(pane)
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_sidebar_toggle.py -q`
Expected: FAIL on the first two — the sidebar renders only the PLAN
section, so `SYSTEM` and `full suite` are absent. The third already passes
(the no-op guard shipped in Task 4); keep it as a regression guard.

- [x] **Step 3: Fill out the model assembler**

In `src/aegis/tui/pane.py`, replace `_sidebar_model` with:

```python
    def _sidebar_model(self) -> SidebarModel:
        """The sidebar's snapshot, from the sources the strips and the bar
        already read — no new data path, no second subscription."""
        import time

        core = self._core
        bar = self._bar()
        _model = getattr(self._agent, "model", "") if self._agent else ""
        _eff_raw = getattr(self._agent, "effort", "") if self._agent else ""
        _eff = getattr(_eff_raw, "value", _eff_raw)
        ident = f"{_model} · {_eff}" if _model else ""
        if self._host != "local":
            ident = f"{ident} · {self._host}" if ident else self._host

        return SidebarModel(
            connection=self._connection_tiers,
            title=getattr(core, "title", "") or "",
            identity=(ident,) if ident else (),
            state_label=core.state.label if core is not None else "",
            loop=self._loop_tiers,
            metrics=core.metrics.render_tiers(time.monotonic())
            if core is not None else (),
            quota=self._quota_tiers,
            plan=core.plan_state(), subplans=core.subplan_states(),
            plan_working=core.plan.working,
            queues=self._digest.snapshot()
            if self._digest is not None else None,
            monitors=self._monitor_manager.snapshot(for_handle=self.handle)
            if self._monitor_manager is not None else [],
            system=self._system_tiers,
        )
```

Add the three caches to `__init__` (beside `self._status_bar = None`), so
app-pushed segments survive until the next repaint:

```python
        # App-pushed status segments, cached for the sidebar: the bar is
        # write-only and the sidebar rebuilds its whole model on refresh.
        self._system_tiers: tuple[str, ...] = ()
        self._quota_tiers: tuple[str, ...] = ()
        self._loop_tiers: tuple[str, ...] = ()
        self._connection_tiers: tuple[str, ...] = ()
```

Connection has no pane-level hook today — `AegisApp._on_ws_connection`
(`app.py:1815`) reaches past the pane and calls
`p.query_one(StatusBar).set_connection_state(up)` directly. Give the pane
the hook rather than reading `StatusBar._connection`, which is private and
would couple the sidebar to the bar's internals. Add to `pane.py`:

```python
    def set_connection_state(self, up: bool, reason: str = "") -> None:
        """WS connect/disconnect. Cached for the sidebar, which shows it at
        the head of SESSION rather than in a section of its own."""
        self._connection_tiers = () if up else (
            "⚠ disconnected — reconnecting…", "⚠ disconnected")
        bar = self._bar()
        if bar is not None:
            bar.set_connection_state(up, reason)
        self._refresh_sidebar()
```

and in `app.py:_on_ws_connection`, route through it:

```python
    def _on_ws_connection(self, up: bool) -> None:
        """Propagate WS connect/disconnect state to all live panes."""
        for p in self._panes:
            if isinstance(p, ConversationPane):
                try:
                    p.set_connection_state(up)
                except Exception:  # noqa: BLE001 — pane may not be mounted
                    pass
```

The `from aegis.tui.widgets import StatusBar` import inside that method
becomes unused — remove it.

- [x] **Step 4: Push to the sidebar on the same calls**

Add one line to each existing push site in `pane.py`. Extract the repaint
so the sites stay one-liners — add beside `_refresh_plan_surfaces`:

```python
    def _refresh_sidebar(self) -> None:
        """Repaint the sidebar if it is open. Cheap when closed: the widget
        keeps the model and returns without rendering."""
        try:
            bar = self.query_one("#sidebar", Sidebar)
        except Exception:
            return
        bar.refresh_model(self._sidebar_model())
```

Then append `self._refresh_sidebar()` to the body of each of:

- `refresh_metrics` (after `bar.set_metrics(...)`)
- `refresh_title` (after `bar.set_session_title(...)`)
- `set_system` — first cache it: `self._system_tiers = tuple(text or ())`
- `set_quota` — first cache it: `self._quota_tiers = tuple(tiers or ())`
- `_on_core_state` (after the existing `bar.set_state(...)`)
- the loop push at `:1965` — first cache
  `self._loop_tiers = bar._loop` after `bar.set_loop(...)`, which reuses
  the bar's own tier construction rather than duplicating the format string

`_refresh_plan_surfaces` already calls `bar.refresh_model(...)` from Task 4;
change it to call `self._refresh_sidebar()` so there is one assembler call
site.

Subscribe to the two push sources so a queue or monitor event repaints an
open sidebar. In `on_mount`, after the existing body:

```python
        # The strips subscribe to these themselves; the sidebar rides the
        # same sources rather than a second observer chain.
        if self._digest is not None:
            self._digest._manager.subscribe(
                lambda _ev: self._refresh_sidebar())
        if self._monitor_manager is not None:
            self._monitor_manager.subscribe(self._refresh_sidebar)
```

- [x] **Step 5: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_sidebar_toggle.py -q`
Expected: PASS (7 tests).

- [x] **Step 6: Run the full fast suite**

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS. A red run here is a regression to investigate, not noise.

- [x] **Step 7: Commit**

```bash
git add src/aegis/tui/pane.py tests/test_sidebar_toggle.py
git commit -m "feat(sidebar): the open mode carries every status segment

Six push sites gain one line each and the sidebar rides the digest and
monitor subscriptions the strips already use. Closed, it still no-ops.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Live verification and the record

Unit tests could not see either of the two defects the live task list
shipped with — the strip that did not fit its width, and the plan that did
not survive a restart. Both were found by driving a real pane. This task is
that pass, plus the documentation the feature owes.

**Files:**
- Modify: `TASKS.md`
- Modify: `docs/superpowers/specs/2026-08-07-aegis-f3-side-dashboard-design.md` (status header)

- [x] **Step 1: Drive it by hand**

Start a real TUI in the aegis checkout: `uv run aegis`

Check each and note what you see:

1. `F3` opens the sidebar; the four collapsed rows disappear; the input
   stays usable and full-width in the left column.
2. `F3` again restores exactly the previous pane.
3. Send a message that produces a plan. The PLAN section fills, the
   spinner ticks, the circles do **not** visibly overlap.
4. Resize the terminal narrow (≈80 cols) and wide (≈200 cols) with the
   sidebar open. Rows re-fit; nothing wraps to a second line; no dead
   column appears on the right of the plan rows.
5. Open it on a **fresh tab that has never been shown** (`Ctrl+N`, then
   `F3` immediately). This is the `size.width == 0` path — labels must not
   come out truncated to the minimum.
6. `/tasks` toggles the same thing as `F3`.

**What the pass actually covered (2026-08-07).** Driven through the real
`AegisApp` in a Textual pilot at 80, 100 and 160 columns, reading the
*composited screen* (`screen._compositor.render_strips()`) rather than a
widget's own text, and cross-checked against PNG exports of
`export_screenshot`. That covers 1, 3 and 4. Item 5 is covered by a unit
test instead (`test_the_render_width_excludes_the_padding_it_grew`
asserts the `size == 0` fallback directly, which is more precise than
eyeballing a fresh tab). **Items 2 and 6 were not exercised** — restoring
the previous pane and the `/tasks` alias both have toggle tests from Task
4, but neither was driven in an interactive TTY.

One thing the PNG exports showed that the terminal does not: right-aligned
cells appearing a second time at the far left of the frame. That is
`cairosvg` mis-placing right-anchored SVG text, and the composited screen
is clean. Read the compositor, not the screenshot, before filing a layout
bug from this rig.

- [x] **Step 2: Fix anything step 1 surfaced, with a test first**

Not clean — three defects, each with a failing test first, all in
`e36b636`:

1. **The column had no background.** It read as text floating beside the
   transcript rather than as the surface replacing four strips that all
   sat on `$panel`. Also had no vertical padding.
2. **The padding was charged to the content.** Adding `padding: 1 2` to
   the existing bounds took an 80-col terminal's content box to 22 cells,
   below the widest system segment — and `fit_rows` answers "no tier
   fits" by dropping the segment, so SYSTEM disappeared entirely. The
   bounds now carry the padding on top of the content budget.
3. **The PLAN section inherited the dock's framing.** `render_plan_dock`
   was free-standing: it opens with its own `tasks d/t` line and
   newline-terminates its last row. In a section that printed the counter
   twice and left two blank rows after PLAN where every sibling has one.

The first two were also what Alex reported from a live `F3`. The blank
panel he saw was something else entirely and not a defect in this code:
his aegis process had been started before Task 4 committed, so it was
running a `_sidebar_model` stub that returned only the plan. A restart is
what fixes that.

Worth noting for the tests: the tint assertion passed on the *first* try
against the untinted sidebar, because `styles.background` is the
*declared* value and reads transparent on any widget that never set one.
It only became a real gate once it compared `background_colors[1]`, the
colour the widget composites to.

**Second pass, same day.** Asked what the plan had missed, four more
things, three of them defects. All in `f19bd0c`, each with a failing test
first and each mutation-checked.

4. **The clock could outgrow its own column.** `_CLOCK_W` is 6 and every
   row lines its right edge up on it, but `:>6` pads to *at least* six and
   never truncates — so `fmt_working`'s `H:MM:SS` form spent seven and an
   hour-plus task wrapped its row. Reachable in an hour of work on one
   task, which is ordinary here. The hour form is now `1h06`, which also
   cannot be misread as the `1:06` minute form one row above. The existing
   width test missed it because none of its tasks ever entered
   `in_progress`, so every clock in it was the one-cell `—`.
5. **QUEUES and MONITORS never fit the column.** Task 3 published
   `format_q` and `format_mon` so the two surfaces would share them, but
   both were written for a *full-width strip* where the strip does the
   fitting — neither took a width. In a 26-cell column one monitor
   rendered 68 cells and wrapped to three rows. Both now take an optional
   width (unbounded by default, so the strips are unchanged), and
   `format_mon` degrades through a tier ladder: the bar goes first, then
   the ETA, and only then is the description cut, with a 14-cell floor.
   Cutting from the right instead would have kept a description already
   legible at half the width and thrown away the bar, the percentage and
   the ETA.
6. **The renderer suite had no "every row fits" invariant** — the single
   assertion a fixed-width column most needs, and the one that would have
   caught 5 the day it was written. `tests/test_sidebar_render.py` now has
   it across 26/33/40/60.
7. Not a defect: the PLAN trim from `e36b636` drops only the dock's own
   first line, so a `└ subagent d/t` header survives and nested rows still
   land on the same right edge. Now asserted rather than assumed.

Items 2 and 6 of step 1 are also closed, as unit tests rather than by
hand: `test_closing_restores_every_collapsed_surface` and
`test_slash_tasks_toggles_the_same_mode_as_f3`. The latter drives the real
command seam end to end, because `_apply_command_effect` ignores unknown
effect kinds by design — so a renamed effect would leave `/tasks`
reporting ok while doing nothing, with no test failing.

- [x] **Step 3: Record it in `TASKS.md`**

Under `## Recently shipped`, above the *Live task list* entry, add:

```markdown
### F3 side dashboard *(shipped 2026-08-07)*

`F3` toggles a mode, not a widget. Open, a full-height sidebar carries the
plan, the queues, the monitors and all eight status-bar segments, and the
main column is transcript and input; closed, the pane is what it was.
`PlanDock` is gone — the sidebar's PLAN section calls `render_plan_dock`
verbatim rather than re-implementing rows, which is what keeps the circle
spacing and the `width - 9` label budget from being re-paid.

One trap worth knowing before you add a fifth collapsed surface: the mode
switch is a single `-sidebar` class and a CSS `display: none`, so a
surface that sets `display` **imperatively** silently wins over it.
`PlanStrip` did, and moved to the `-empty` class idiom its two sibling
strips already used. The toggle test asserts on real widget visibility,
never on the class, and is mutation-checked.

**Outstanding, deliberately: the web client renders no sidebar.** Same
call as the live task list's Task 12 and the session-title web gap above —
three TUI-first features now owe the PWA the same debt, and `AGENTS.md`
calls the two UIs co-equal. Worth doing as one web slice rather than three.

- Spec: `docs/superpowers/specs/2026-08-07-aegis-f3-side-dashboard-design.md`
- Plan: `docs/superpowers/plans/2026-08-07-aegis-f3-side-dashboard.md`
```

- [x] **Step 4: Flip the spec status header**

In the spec, change `**Status:** approved 2026-08-07, no plan yet` to
`**Status:** implemented <commit>` with the Task 5 commit sha. A stale
status header corrupts the next `/workon` briefing.

- [x] **Step 5: Commit**

```bash
git add TASKS.md docs/superpowers/specs/2026-08-07-aegis-f3-side-dashboard-design.md
git commit -m "docs(sidebar): record the F3 dashboard and its web debt

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [x] **Step 6: Push**

```bash
git push origin main
```

---

## Notes for the implementer

**Things that are already paid for — do not re-derive them.**

- **Circles are always space-separated.** They are East Asian Ambiguous:
  Rich measures one cell, terminals draw two, neighbours overlap. This is
  why the PLAN section calls `render_plan_dock` instead of formatting rows.
- **`size` is already the content box.** Textual excludes padding from it,
  so a widget's own `padding` must not be subtracted again. This bug and an
  off-by-one in the row arithmetic were live at once and cancelled at some
  widths, which is how they reached a real terminal.
- **A widget that was `display: none` has not been laid out**, so
  `size.width` is 0 on the first paint after a toggle. `on_resize` is what
  corrects it.
- **An imperative `.display` is an inline style and beats CSS.** That is
  the whole reason Task 4 moves `PlanStrip` to a class.
- **The tracker never reads a clock** — every method takes an explicit
  `ts`. The sidebar's `_tick` increments a frame counter and does not
  introduce one.

**Do not:**

- Touch `src/aegis/web/`. The web half is deliberately out of scope and
  recorded as debt in Task 6.
- Re-implement plan rows, queue counters or monitor bars. Three renderers
  already exist and Task 3 makes two of them public precisely so they are
  shared.
- Assert on the `-sidebar` class in a test. Assert on `.display`.
