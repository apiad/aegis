# F3 Sidebar Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the open `F3` sidebar from 35 rows to 28–29 on the same session, and spend the rows bought on progress bars, so a 40-row terminal has slack instead of two rows of headroom.

**Architecture:** Three independent moves. Rule headings replace heading-plus-blank-line, which buys six rows. Every fraction becomes a one-row gauge reusing `aegis/fleet/render.py`'s renderer, so a bar replaces a text row instead of adding one — which requires a data-path change, because `SidebarModel` is handed already-rendered markup today and cannot draw a bar for a percentage it received as the string `"quota 47%"`. `PLAN` gets a window so a long plan stops evicting the four sections below it.

**Tech Stack:** Python 3.13+, `uv`, Textual, Rich, pytest (`pytest-asyncio`, `pytest-xdist`).

**Spec:** `docs/superpowers/specs/2026-09-22-aegis-f3-sidebar-redesign-design.md`

## Global Constraints

- Python 3.13 or newer. Use `uv`, never pip.
- The gate is `make check`. It runs `format lint lint-docs typecheck test`, and `test` is `uv run pytest -q -n auto -m "not slow" --max-unmarked-duration=3`. Run the target verbatim — a bare `pytest` omits `--max-unmarked-duration=3` and goes green on a suite `make test` fails.
- This is a shared checkout. Stage and commit named paths only (`git commit -- <paths>`), never `git add -A`, never `--amend`.
- Commit on `main`. No branch, no PR, unless asked.
- Conventional commits, English, one logical change each.
- A section that has nothing to render contributes nothing: no heading, no blank row. This predates the plan and must survive it (`test_a_section_with_no_content_omits_its_heading`).
- Every renderer touched here is pure: a model in, a `Text` out, no Textual object, no clock read, no manager. Motion is a `frame` integer passed in.
- Widths to hold: 56 (the 34% default at 165 columns), 40, and 26 (`SIDEBAR_MIN` minus padding is 26). No row may exceed the width it was given, in **cells** — `cell_len`, not `len`.
- A user-visible change needs a `CHANGELOG.md` entry. This one is user-visible.

## Review Focus

Five conditions the spec implies but does not enumerate, each pinned to a test in the task that owns the code:

1. **`context_window == 0`** — a harness that never reported a window. `SessionMetrics.gauge()` must return `None`, not divide by zero, and `CONTEXT` must fall back to its text tier. (Task 4, Task 6)
2. **A width below the gauge's minimum** — at 22 cells a label, a bar, a value and a tail do not fit. `gauge` must truncate to `cells` and never emit a wider row, at every width down to 10. (Task 1)
3. **A plan with no `in_progress` task** — all completed, or all pending. The window has nothing to centre on and must still return rows without raising. (Task 10)
4. **No quota reading** — no credentials configured, so `quota_gauges()` returns `()`. `CONTEXT` must fall back to the tier row, never draw a 0% bar, which would claim a reading of zero rather than no reading. (Task 6)
5. **A remote pane with no numbers** — `stats is None`, `ctx is None`, `quota_gauges` empty, but the tier tuples populated. Every section degrades to today's text rows and nothing renders blank. (Task 5, Task 6, Task 7)

---

## File Structure

| file | responsibility | change |
|---|---|---|
| `src/aegis/fleet/render.py` | the shared gauge renderer | promote 3 private helpers |
| `src/aegis/tui/metrics.py` | session token/cost accounting | add `ContextGauge` + `SessionMetrics.gauge()` |
| `src/aegis/tui/sidebar.py` | the F3 column | the bulk: heading, every section |
| `src/aegis/plan/window.py` | which plan tasks to show | **new**, pure |
| `src/aegis/tui/monitor_strip.py` | monitor rows, shared with the strip | drop `_bar`, call `bar` |
| `src/aegis/tui/pane.py` | assembles `SidebarModel` | carry 4 new values |
| `src/aegis/tui/app.py` | samples stats and quota per tick | push the values it already holds |

Tests live beside the existing ones: `tests/test_sidebar_render.py` (pure renderer), `tests/test_sidebar_system.py` (in a running app), `tests/test_plan_window.py` (new, pure), `tests/test_fleet_v2_band.py` (gauge promotion).

---

### Task 1: Promote the fleet gauge helpers

`sidebar.py` becomes a second caller of three helpers that are private today. Promote them in place rather than moving them to a new shared module: a new file whose only justification is a second caller is churn, and the fleet band is the older and larger caller.

**Files:**
- Modify: `src/aegis/fleet/render.py` — `_gauge` (line 139), `_reset` (line 172), `_rows_of` (line 160), and their call sites inside `render_band`
- Test: `tests/test_fleet_v2_band.py` (the band renderer's own test file; its palette fixture is `P = aegis_colors(INK)`)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `gauge(label: str, pct: float, value: str, style: str, cells: int, pal, *, value_style: str | None = None, tail: str = "") -> Text`
  - `rows_of(gauges: list[Text], per_row: int) -> Text`
  - `reset_in(seconds: float | None) -> str`
  - `bar(pct: float, cells: int, style: str, pal) -> Text` (already public)
  - `ctx_style(pct: float, pal) -> str` (already public)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_fleet_v2_band.py`:

```python
from aegis.fleet.render import gauge, reset_in, rows_of


def test_a_gauge_is_one_row_and_never_wider_than_its_budget():
    """The property the whole sidebar redesign rests on: a gauge replaces a
    text row rather than adding one, so it must be exactly one row, and it
    must fit the column it was given or the section below it shifts."""
    for cells in range(10, 61):
        g = gauge("CTX", 71.0, "71%", P.accent, cells, P, tail="142k/200k")
        assert "\n" not in g.plain
        assert cell_len(g.plain) <= cells, f"overflowed at {cells}"


def test_reset_in_is_empty_for_an_unknown_reset():
    assert reset_in(None) == ""
    assert reset_in(3840) == "↻ 1h04m"


def test_rows_of_pairs_gauges_two_to_a_line():
    a, b, c = (Text("a"), Text("b"), Text("c"))
    assert rows_of([a, b, c], 2).plain.split("\n") == ["a  b", "c"]
```

Add the imports the file needs at the top if absent: `from rich.cells import cell_len` and `from rich.text import Text`. The palette fixture in this file is already `P = aegis_colors(INK)`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_fleet_v2_band.py -q -k "gauge or reset_in or rows_of"`
Expected: FAIL with `ImportError: cannot import name 'gauge' from 'aegis.fleet.render'`

- [ ] **Step 3: Rename the three helpers and their call sites**

In `src/aegis/fleet/render.py`, rename the definitions `_gauge` → `gauge`, `_rows_of` → `rows_of`, `_reset` → `reset_in`. Then update every call inside `render_band`: there are four `_gauge(` calls in the host-gauge block, one in the quota loop, two `_rows_of(` calls, and one `_reset(` call.

```bash
uv run python - <<'PY'
import pathlib, re
p = pathlib.Path("src/aegis/fleet/render.py")
s = p.read_text()
for old, new in (("_gauge", "gauge"), ("_rows_of", "rows_of"), ("_reset", "reset_in")):
    s = re.sub(rf"(?<![A-Za-z0-9_]){old}(?![A-Za-z0-9_])", new, s)
p.write_text(s)
PY
uv run rg -n "_gauge|_rows_of|_reset\b" src/ tests/
```

The `rg` must print nothing. If it prints a hit in another file, that file imported a private name and needs the same rename.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fleet_v2_band.py tests/test_fleet_v2_motion.py tests/test_fleet_screen.py -q`
Expected: PASS, including every pre-existing fleet test — this is a rename, so any red here means a call site was missed.

- [ ] **Step 5: Commit**

```bash
git commit -F - -- src/aegis/fleet/render.py tests/test_fleet_v2_band.py <<'MSG'
refactor(fleet): the gauge helpers gain a second caller

`sidebar.py` is about to draw gauges. Promote `_gauge`, `_rows_of` and
`_reset` in place rather than moving them to a new module whose only
justification would be that two callers exist.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 2: `heading()` draws a rule, and the blank separators go

The single largest win: six rows bought, and the structure stops being dimmer than its contents.

**Files:**
- Modify: `src/aegis/tui/sidebar.py` — `heading()` (line 71), `render_sidebar()` (line 227)
- Test: `tests/test_sidebar_render.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `RULE_FLOOR: int = 3`
  - `heading(text: str, palette, width: int, right: str = "") -> Text` — same signature, new output
  - `heading_fits(text: str, width: int, right: str) -> bool` — whether `right` will be accepted into the heading, used by Task 3

- [ ] **Step 1: Write the failing test**

Replace the two existing heading tests in `tests/test_sidebar_render.py` (`test_heading_right_aligns_its_counter` and `test_heading_without_a_counter_is_just_the_word`) with:

```python
def test_a_heading_is_a_rule_that_fills_its_width():
    assert as_text(heading("PLAN", C, 20)) == "── PLAN ────────────"


def test_a_heading_with_a_counter_puts_it_at_the_far_end():
    assert as_text(heading("PLAN", C, 20, right="3/7")) == "── PLAN ──────── 3/7"


def test_a_heading_drops_a_counter_it_cannot_seat():
    """Below RULE_FLOOR rule cells the heading would read as a word, a gap
    and a label with nothing joining them — the shape this change removes.
    The counter is dropped and the rule stays whole."""
    assert not heading_fits("MONITORS", 18, "✻ working… · ◐ thinking")
    assert as_text(heading("MONITORS", C, 18, right="✻ working… · ◐ thinking")) == \
        "── MONITORS ──────"


def test_a_heading_measures_in_cells_not_characters():
    """The section name is ASCII but the counter is not, and Rich measures
    what the terminal draws, not what len() counts."""
    assert cell_len(as_text(heading("REPOS", C, 30, right="✻ 2"))) == 30


def test_sections_are_separated_by_the_rule_and_nothing_else():
    m = SidebarModel(state_label="idle", metrics=("$1.84",))
    lines = as_text(render_sidebar(m, C, 40)).split("\n")
    assert "" not in lines, "a blank row survived between two sections"
    assert sum(1 for ln in lines if ln.startswith("── ")) == 2
```

Update the import line to pull in `heading_fits`, and delete `test_sections_are_separated_by_one_blank_row`, which asserts the behaviour being removed.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sidebar_render.py -q -k "heading or separated"`
Expected: FAIL — `ImportError` on `heading_fits`.

- [ ] **Step 3: Implement**

In `src/aegis/tui/sidebar.py`, replace `heading()` with:

```python
# Below this many rule cells a heading with a counter reads as a word, a
# gap and a label with nothing joining them — which is the shape this
# redesign exists to remove. The counter is dropped instead, and its
# section puts the value back on a row of its own.
RULE_FLOOR = 3

_LEAD = "── "


def _rule_cells(text: str, width: int, right: str) -> int:
    """Rule cells left once the lead, the name, the counter and the single
    space on each side of the rule are paid for. Negative means it does not
    fit at all."""
    return width - cell_len(_LEAD) - cell_len(text) - 1 - cell_len(right) - 1


def heading_fits(text: str, width: int, right: str) -> bool:
    """Whether ``right`` will be seated in this heading. A section that has
    somewhere else to put the value asks first."""
    return bool(right) and _rule_cells(text, width, right) >= RULE_FLOOR


def heading(text: str, palette, width: int, right: str = "") -> Text:
    """A section heading drawn as a rule, optionally seating a value at the
    far end.

    The rule is what separates one section from the next, which is why
    ``render_sidebar`` no longer spends a blank row on it. The name is
    ``palette.ink`` rather than ``palette.muted``: it is the structure of
    the column and must not be the quietest thing in it.

    Measured in cells, not characters — the name is ASCII but the value is
    routinely not (``✻ working``, ``✓5``), and Rich draws cells.
    """
    out = Text(_LEAD, style=palette.rule)
    out.append(text, style=f"bold {palette.ink}")
    if heading_fits(text, width, right):
        out.append(" " + "─" * _rule_cells(text, width, right) + " ",
                   style=palette.rule)
        out.append(right, style=palette.muted)
        return out
    fill = width - cell_len(_LEAD) - cell_len(text) - 1
    if fill > 0:
        out.append(" " + "─" * fill, style=palette.rule)
    return out
```

Then in `render_sidebar`, change the separator:

```python
        if i:
            out.append("\n")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sidebar_render.py -q`
Expected: PASS. Other tests in this file may fail on the blank-line change; fix those assertions, they are asserting the old separator.

- [ ] **Step 5: Mutation-check the cell measurement**

The `cell_len` in `_rule_cells` is the kind of correctness a test can silently stop covering. Break it on purpose:

```bash
sed -i 's/return width - cell_len(_LEAD) - cell_len(text) - 1 - cell_len(right) - 1/return width - len(_LEAD) - len(text) - 1 - len(right) - 1/' src/aegis/tui/sidebar.py
uv run pytest tests/test_sidebar_render.py -q -k "cells_not_characters"
```

Expected: FAIL. If it passes, the test is not measuring what it claims and must be fixed before moving on. Then restore:

```bash
git checkout -- src/aegis/tui/sidebar.py
```

and re-apply Step 3 (or `git stash pop` if you prefer; the point is the file returns to the Step 3 state).

- [ ] **Step 6: Commit**

```bash
git commit -F - -- src/aegis/tui/sidebar.py tests/test_sidebar_render.py <<'MSG'
feat(sidebar): headings are rules, and the blank separators go

Seven headings and six blanks were thirteen of the column's 35 rows. The
rule separates, so the blank is the belt and the braces; the name moves
from muted to ink, because the structure of a column must not be dimmer
than its contents.

Six rows bought.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 3: `SESSION` seats its state, indents its recap, and gauges its loop

**Files:**
- Modify: `src/aegis/tui/sidebar.py` — `SidebarModel` (line 38), `_session()` (line 98)
- Test: `tests/test_sidebar_render.py`

**Interfaces:**
- Consumes: `heading_fits` and `heading` from Task 2; `gauge` from Task 1.
- Produces: `SidebarModel.loop_status: dict | None = None` — `{"iteration": int, "max_iterations": int}`, the same dict `StatusBar.set_loop` already receives.

- [ ] **Step 1: Write the failing test**

```python
def test_the_state_label_rides_the_session_heading():
    m = SidebarModel(title="fix the eviction race", state_label="✻ working")
    lines = as_text(render_sidebar(m, C, 56)).split("\n")
    assert lines[0].startswith("── SESSION")
    assert lines[0].endswith("✻ working")
    assert "✻ working" not in lines[1]


def test_a_state_label_too_long_for_the_heading_keeps_its_own_row():
    m = SidebarModel(title="t", state_label="✻ working… · ◐ thinking")
    lines = [ln for ln in as_text(render_sidebar(m, C, 26)).split("\n") if ln]
    assert "✻ working… · ◐ thinking" not in lines[0]
    assert "✻ working… · ◐ thinking" in lines


def test_the_recap_continuation_is_indented_under_its_label():
    m = SidebarModel(state_label="idle",
                     now_line="reading pane.py to find where the recap lands")
    rows = [ln for ln in as_text(render_sidebar(m, C, 26)).split("\n")
            if ln and not ln.startswith("── ")]
    tail = [r for r in rows if r.startswith("      ")]
    assert tail, "the recap wrapped flush left"
    assert all(len(r) <= 26 for r in rows)


def test_the_loop_draws_a_bar():
    m = SidebarModel(state_label="idle",
                     loop_status={"iteration": 3, "max_iterations": 20})
    out = as_text(render_sidebar(m, C, 56))
    assert "LOOP" in out and "█" in out and "3/20" in out


def test_a_loop_without_a_status_falls_back_to_its_tier():
    """A remote pane gets the rendered string and no dict."""
    m = SidebarModel(state_label="idle", loop=("⟳ loop 3/20",))
    assert "⟳ loop 3/20" in as_text(render_sidebar(m, C, 56))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sidebar_render.py -q -k "state_label or recap_continuation or loop"`
Expected: FAIL — `SidebarModel` has no `loop_status`, and the state label is still on its own row.

- [ ] **Step 3: Implement**

Add the field to `SidebarModel`, under the `# SESSION` comment:

```python
    loop: tuple[str, ...] = ()
    # The loop's own numbers, for the gauge. The tier tuple above stays for
    # a remote pane, which is handed the rendered string and no dict.
    loop_status: dict | None = None
```

Replace `_session` with:

```python
def _session(m: SidebarModel, palette, width: int) -> Text | None:
    # Connection leads: a disconnected session is a fact about the session,
    # and it is the one segment that demands action. Under its own heading
    # at some scroll offset it would be worse than the bar it replaces.
    seated = heading_fits("SESSION", width, m.state_label)
    segs = [
        Segment("connection", m.connection, 0),
        Segment("title", (m.title,) if m.title else (), 0),
        Segment("identity", m.identity, 0),
        # Seated in the heading when it fits; otherwise it keeps its row.
        Segment("state", () if seated or not m.state_label else (m.state_label,), 0),
    ]
    rows = _rows(segs, palette, width)
    if m.loop_status:
        i, n = m.loop_status["iteration"], m.loop_status["max_iterations"]
        rows.append(gauge("LOOP", 100 * i / n if n else 0, f"{i}/{n}",
                          palette.accent, width, palette))
    else:
        rows += _rows([Segment("loop", m.loop, 0)], palette, width)
    rows += _recap_rows(m.now_line, palette, width)
    if not rows and not m.state_label:
        return None
    return _block(heading("SESSION", palette, width,
                          right=m.state_label if seated else ""), rows)
```

And add the recap folder above `_session`:

```python
_RECAP_LABEL = "now   "


def _recap_rows(line: str, palette, width: int) -> list[Text]:
    """The mid-turn recap, wrapped with a hanging indent.

    Wrapped rather than tiered: ``fit_rows`` drops a segment whose narrowest
    tier overflows, and a recap sentence is routinely wider than the column.
    Rich has no hanging-indent option on ``Text``, so the fold is explicit —
    without it the continuation lands flush left and reads as a new row of
    the section.
    """
    if not line:
        return []
    body = max(8, width - cell_len(_RECAP_LABEL))
    words, out, cur = line.split(), [], ""
    for w in words:
        nxt = f"{cur} {w}".strip()
        if cell_len(nxt) > body and cur:
            out.append(cur)
            cur = w
        else:
            cur = nxt
    if cur:
        out.append(cur)
    rows = []
    for i, chunk in enumerate(out):
        t = Text(_RECAP_LABEL if i == 0 else " " * cell_len(_RECAP_LABEL),
                 style=palette.muted)
        t.append(chunk, style=palette.working)
        rows.append(t)
    return rows
```

Add `from aegis.fleet.render import gauge` to the imports.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sidebar_render.py -q`
Expected: PASS. `test_connection_warning_leads_the_session_section` asserts `lines[0] == "SESSION"` and now needs `lines[0].startswith("── SESSION")`; fix it.

- [ ] **Step 5: Commit**

```bash
git commit -F - -- src/aegis/tui/sidebar.py tests/test_sidebar_render.py <<'MSG'
feat(sidebar): SESSION seats its state, folds its recap, gauges its loop

The state label rides the heading's right slot, which is how the section
loses a row; below three rule cells it keeps a row of its own instead of
degrading into a word, a gap and a label. The recap gets the hanging
indent it never had — the continuation used to land flush left and read
as a new row.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 4: `SessionMetrics.gauge()` returns numbers instead of a string

`render_tiers` computes `ctx_pct` and bakes it straight into markup, so the percentage exists for exactly as long as one expression. Extract it, and both callers read the same arithmetic.

**Files:**
- Modify: `src/aegis/tui/metrics.py` — around line 283
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ContextGauge` — frozen dataclass, fields `pct: float`, `live: int`, `window: int`
  - `SessionMetrics.gauge(self) -> ContextGauge | None`

The class is `SessionMetrics` (`src/aegis/tui/metrics.py:58`), a plain `@dataclass`, and it owns `context_window`, `last_true_input`, `p_in` and `_provisional`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_metrics.py`:

```python
from aegis.tui.metrics import ContextGauge


def test_gauge_is_none_without_a_context_window():
    """A harness that never reported a window. None, not a ZeroDivisionError
    and not a 0% bar — a 0% bar claims a reading of zero rather than no
    reading at all."""
    m = SessionMetrics()
    assert m.context_window == 0
    assert m.gauge() is None


def test_gauge_agrees_with_the_percentage_in_the_tier_string():
    m = SessionMetrics(context_window=200_000, last_true_input=142_000)
    g = m.gauge()
    assert isinstance(g, ContextGauge)
    assert g.live == 142_000 and g.window == 200_000
    assert g.pct == 71
    assert "(71%)" in m.render_tiers(0.0)[0]
```

Use whatever constructor the file's existing tests use; `SessionMetrics` is a dataclass, so keyword fields work.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_metrics.py -q -k gauge`
Expected: FAIL — `ImportError: cannot import name 'ContextGauge'`

- [ ] **Step 3: Implement**

Add near the top of `src/aegis/tui/metrics.py`, after the imports:

```python
@dataclass(frozen=True)
class ContextGauge:
    """The context meter as numbers, for a caller that draws a bar.

    Three fields, because three is what exists. There is no turn counter on
    this class — the neighbouring quantities are ``tool_calls``,
    ``turn_seconds`` and ``session_seconds`` — so a caller wanting the rest
    of the meter takes a rendered tier rather than expecting fields here.
    """

    pct: float
    live: int
    window: int
```

Add the method to the metrics class:

```python
    def gauge(self) -> ContextGauge | None:
        """The context meter, or None when no window was ever reported.

        The arithmetic lives here rather than inside ``render_tiers`` so the
        percentage is computed once for both callers. ``render_tiers`` baked
        it into markup, which left the sidebar trying to draw a bar for a
        number it had only ever seen as the string ``ctx 142k (71%)``.
        """
        if self.context_window <= 0:
            return None
        live = self.p_in if self._provisional else self.last_true_input
        return ContextGauge(
            pct=round(100 * live / self.context_window),
            live=live,
            window=self.context_window,
        )
```

Then rewrite the block at line 283 to call it:

```python
        ctx = ctx_short = ""
        g = self.gauge()
        if g is not None:
            ctx_pct = g.pct
            live = g.live
            # (the existing tag / body / body_short lines follow unchanged)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_metrics.py tests/test_statusbar_fit.py -q`
Expected: PASS. The status-bar tests are the regression guard: if the extracted arithmetic disagrees with the old inline version by even a rounding step, a tier string changes and those tests say so.

- [ ] **Step 5: Commit**

```bash
git commit -F - -- src/aegis/tui/metrics.py tests/test_metrics.py <<'MSG'
feat(metrics): the context meter has numbers, not only a rendered string

`render_tiers` computed ctx_pct and baked it into markup in the same
expression, so the sidebar was handed `ctx 142k (71%)` and could not draw
a bar for it. `gauge()` returns the three fields that exist; both callers
now read one piece of arithmetic.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 5: The data path — values reach the sidebar

The wiring task. No visible change lands here; the next three tasks depend on it entirely.

**Files:**
- Modify: `src/aegis/tui/sidebar.py` — `SidebarModel`
- Modify: `src/aegis/tui/pane.py` — `set_system` (1502), `set_quota` (1510), `_on_loop_change` (2522), `_sidebar_model` (3094), `__init__` (1103)
- Modify: `src/aegis/tui/app.py` — `_tick` (1647), `_quota_tick` (1631)
- Test: `tests/test_sidebar_system.py`

**Interfaces:**
- Consumes: `ContextGauge` and `SessionMetrics.gauge()` from Task 4.
- Produces, on `SidebarModel`:
  - `ctx: ContextGauge | None = None`
  - `quota_gauges: tuple[QuotaGauge, ...] = ()` — `aegis.fleet.models.QuotaGauge`, fields `label`, `percent`, `severity`, `resets_in_s`
  - `stats: SystemStats | None = None` — `aegis.tui.sysmeter.SystemStats`, fields `cpu`, `ram`, `disk`, `ram_used_gb`, `ram_total_gb`
  - `loop_status: dict | None` (added in Task 3)
- Produces, on `Pane`: `set_system(self, text, stats=None)`, `set_quota(self, tiers, gauges=())`

Both new parameters default, so a caller that has not been updated keeps working — which matters because `set_system` and `set_quota` are reached through `hasattr` checks in `app.py`, and a remote pane class may not implement them at all.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_sidebar_system.py`:

```python
@pytest.mark.asyncio
async def test_the_sidebar_receives_system_numbers_not_only_strings():
    """The gauge sections cannot draw a bar for a percentage handed to them
    as the string `cpu 34%`. This asserts the values arrive; the sections
    that draw them are asserted in their own tests."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        model = pane._sidebar_model()
        assert model.stats is not None, "the app tick never pushed SystemStats"
        assert 0.0 <= model.stats.cpu <= 100.0


@pytest.mark.asyncio
async def test_a_pane_that_was_never_pushed_numbers_still_renders():
    """The remote case: tiers populated, every numeric field empty. Nothing
    may render blank and nothing may raise."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.set_system(("cpu 34% ram 61% disk 82%",))
        pane.set_quota(("quota 47% · resets in 3h",))
        pane.toggle_task_dock()
        await pilot.pause()
        painted = _painted(pane)
        assert "cpu 34%" in painted
        assert "quota 47%" in painted
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sidebar_system.py -q -k "numbers or never_pushed"`
Expected: FAIL — `AttributeError: 'SidebarModel' object has no attribute 'stats'`

- [ ] **Step 3: Implement**

In `src/aegis/tui/sidebar.py`, add to `SidebarModel` beside the tier tuples they accompany:

```python
    # CONTEXT
    metrics: tuple[str, ...] = ()
    quota: tuple[str, ...] = ()
    # The same two as numbers. The tuples above stay: StatusBar still eats
    # them, and a remote pane is handed strings and nothing else, so a
    # section draws a gauge when it has the number and falls back to its
    # tier row when it does not.
    ctx: ContextGauge | None = None
    quota_gauges: tuple = ()
    ...
    # SYSTEM
    system: tuple[str, ...] = ()
    stats: object | None = None  # aegis.tui.sysmeter.SystemStats
```

`quota_gauges` and `stats` are annotated loosely for the same reason `queues: object | None` already is in this file: importing `aegis.fleet.models` and `aegis.tui.sysmeter` into a pure renderer to satisfy a type annotation buys nothing.

In `src/aegis/tui/pane.py`:

```python
    def set_system(self, text, stats=None) -> None:
        """Push the system-stats segment (sampled app-side) to the StatusBar.

        ``stats`` is the same sample the tiers were rendered from, kept for
        the sidebar's gauges. Optional so a caller that has not been updated
        — and a pane class that never gets one — keeps working.
        """
        self._system_tiers = tuple(text or ())
        if stats is not None:
            self._system_stats = stats
        bar = self._bar()
        if bar is not None:
            bar.set_system(text)
        self._refresh_sidebar()

    def set_quota(self, tiers, gauges=()) -> None:
        """Push the quota segment (sampled app-side) to the StatusBar."""
        self._quota_tiers = tuple(tiers or ())
        self._quota_gauges = tuple(gauges or ())
        bar = self._bar()
        if bar is not None:
            bar.set_quota(tiers)
        self._refresh_sidebar()
```

Initialise both in `__init__` beside the existing tier attributes (line 1103):

```python
        self._system_stats = None
        self._quota_gauges: tuple = ()
        self._loop_status: dict | None = None
```

In `_on_loop_change`, keep the dict beside the tiers:

```python
        status = state.status() if state is not None else None
        self._loop_status = status
        bar = self._bar()
        if bar is not None:
            bar.set_loop(status)
            # Reuse the bar's own tier construction rather than duplicating
            # the format string in two places.
            self._loop_tiers = bar._loop
```

In `_sidebar_model`, add the four:

```python
            ctx=core.metrics.gauge(),
            quota_gauges=self._quota_gauges,
            loop_status=self._loop_status,
            stats=self._system_stats,
```

In `src/aegis/tui/app.py` `_tick`, pass the sample it already assigned:

```python
            if active is not None and hasattr(active, "set_system"):
                active.set_system(self._system_last, stats)
```

In `_quota_tick`, build the gauges beside the tiers. The readings list is already constructed for `format_quota_bar`; hoist it so both consume it:

```python
        readings = [(p, self.quota_services[p.name].current()) for p in PROVIDERS]
        tiers = format_quota_bar(readings, self._palette)
        gauges = quota_gauges(readings, now=datetime.now().astimezone())
```

and pass `gauges` at the `active.set_quota(tiers)` call site. Import with the other quota names: `from aegis.usage.quota import quota_gauges`.

Note the push-on-change guard immediately above that call compares `self._quota_last != tiers`. Leave it comparing tiers only: the gauges are derived from the same readings, so tiers unchanged means gauges unchanged, and adding a second comparison would repaint on a float that moved in a digit the tier rounds away.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sidebar_system.py tests/test_sidebar_toggle.py tests/test_sidebar_repos.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git commit -F - -- src/aegis/tui/sidebar.py src/aegis/tui/pane.py src/aegis/tui/app.py tests/test_sidebar_system.py <<'MSG'
feat(sidebar): the model carries numbers beside its rendered tiers

`metrics`, `quota` and `system` were tuples of already-rendered markup, so
the column could not draw a bar for a percentage it had only seen as
`quota 47%`. The app already holds every value — `_system_stats` per tick,
the quota readings it builds for the fleet band — so this passes them on
rather than sampling anything twice.

The tiers stay: StatusBar still eats them, and a remote pane is handed
strings and nothing else.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 6: `CONTEXT` draws gauges

**Files:**
- Modify: `src/aegis/tui/sidebar.py` — `_context()` (line 122)
- Test: `tests/test_sidebar_render.py`

**Interfaces:**
- Consumes: `gauge`, `reset_in`, `ctx_style` (Task 1); `ContextGauge` (Task 4); `SidebarModel.ctx` and `.quota_gauges` (Task 5).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

```python
from aegis.fleet.models import QuotaGauge
from aegis.fleet.render import ctx_style
from aegis.tui.metrics import ContextGauge


def test_context_draws_a_bar_for_the_window():
    m = SidebarModel(ctx=ContextGauge(pct=71, live=142_000, window=200_000))
    out = as_text(render_sidebar(m, C, 56))
    assert "CTX" in out and "█" in out and "71%" in out
    assert "142k/200k" in out


def test_the_context_bar_takes_its_colour_from_the_pressure():
    """Asserted on `ctx_style` rather than on Rich spans: the behaviour under
    test is that pressure picks the colour, and reaching into a Text's span
    list couples the test to Rich internals instead."""
    assert ctx_style(95, C) == C.error
    assert ctx_style(65, C) == C.accent
    assert ctx_style(20, C) == C.ready


def test_quota_draws_one_bar_per_window_with_its_reset():
    m = SidebarModel(quota_gauges=(
        QuotaGauge(label="cc 5h", percent=47.0, severity="normal", resets_in_s=11040),
    ))
    out = as_text(render_sidebar(m, C, 56))
    assert "cc 5h" in out and "47%" in out and "↻ 3h04m" in out


def test_no_quota_reading_falls_back_to_the_tier_not_to_a_zero_bar():
    """No credentials configured. A 0% bar would claim a reading of zero
    rather than no reading at all."""
    m = SidebarModel(quota=("quota unavailable",), quota_gauges=())
    out = as_text(render_sidebar(m, C, 56))
    assert "quota unavailable" in out
    assert "0%" not in out


def test_no_context_window_falls_back_to_the_metrics_tier():
    m = SidebarModel(ctx=None, metrics=("↑142k ↓8.2k · $1.84 · 1:20",))
    out = as_text(render_sidebar(m, C, 56))
    assert "$1.84" in out
    assert "CTX" not in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sidebar_render.py -q -k "context or quota"`
Expected: FAIL — no `CTX` label is rendered.

- [ ] **Step 3: Implement**

```python
def _context(m: SidebarModel, palette, width: int) -> Text | None:
    rows: list[Text] = []
    if m.ctx is not None:
        rows.append(gauge(
            "CTX", m.ctx.pct, f"{m.ctx.pct:.0f}%",
            ctx_style(m.ctx.pct, palette), width, palette,
            tail=f"{_fmt_tokens(m.ctx.live)}/{_fmt_tokens(m.ctx.window)}",
        ))
    for q in m.quota_gauges:
        style = severity_style(q.severity, palette)
        rows.append(gauge(q.label, q.percent, f"{q.percent:.0f}%", style,
                          width, palette, value_style=style,
                          tail=reset_in(q.resets_in_s)))
    # The gauge takes the fraction; the leftover tier takes the rest. T3 is
    # the narrowest form `render_tiers` returns and is what is left once the
    # context percentage has its own row above. A pane with no gauge falls
    # all the way back and shows the widest tier it can fit instead.
    if m.metrics:
        rows += _rows([Segment("metrics", m.metrics[-1:] if m.ctx else m.metrics, 0)],
                      palette, width)
    if not m.quota_gauges:
        rows += _rows([Segment("quota", m.quota, 0)], palette, width)
    if not rows:
        return None
    return _block(heading("CONTEXT", palette, width), rows)
```

Add to the imports:

```python
from aegis.fleet.render import ctx_style, gauge, reset_in, severity_style
from aegis.tui.metrics import _fmt_tokens
```

`_fmt_tokens` is private by name but already imported by four other modules (`render.py`, `render_html.py`, `pane.py` twice); follow the established practice rather than renaming it across five files for this change.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sidebar_render.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git commit -F - -- src/aegis/tui/sidebar.py tests/test_sidebar_render.py <<'MSG'
feat(sidebar): CONTEXT draws the window and the quota as bars

Two of the five fractions in this column that were rendered as text. The
gauge takes the proportion and the leftover metrics tier takes the rest,
so the section gains one row and loses a dot-separated status-bar line.

No reading falls back to the tier rather than to a 0% bar: a zero bar
claims a reading of zero rather than no reading at all.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 7: `SYSTEM` draws gauges and merges its two static rows

**Files:**
- Modify: `src/aegis/tui/sidebar.py` — `_system()` (line 198)
- Test: `tests/test_sidebar_render.py`, `tests/test_sidebar_system.py`

**Interfaces:**
- Consumes: `gauge`, `rows_of`, `ctx_style` (Task 1); `SidebarModel.stats` (Task 5).
- Produces: nothing new.

The existing test `test_the_open_sidebar_answers_where_and_which_build` must keep passing. `cwd` and `build` merge onto one row rather than being cut — see the spec's reasoning; they are the two questions a stale checkout makes you ask.

- [ ] **Step 1: Write the failing test**

In `tests/test_sidebar_render.py`:

```python
from aegis.tui.sysmeter import SystemStats


def test_system_draws_three_meters_as_bars_on_two_rows():
    m = SidebarModel(stats=SystemStats(cpu=34.0, ram=61.0, disk=82.0))
    rows = [ln for ln in as_text(render_sidebar(m, C, 56)).split("\n")
            if ln and not ln.startswith("── ")]
    assert len(rows) == 2
    assert "CPU" in rows[0] and "RAM" in rows[0]
    assert "DSK" in rows[1]
    assert "█" in rows[0]


def test_a_narrow_column_puts_one_meter_per_row():
    m = SidebarModel(stats=SystemStats(cpu=34.0, ram=61.0, disk=82.0))
    rows = [ln for ln in as_text(render_sidebar(m, C, 26)).split("\n")
            if ln and not ln.startswith("── ")]
    assert len(rows) == 3


def test_cwd_and_build_share_one_row():
    m = SidebarModel(cwd=("CWD ~/Workspace/repos/aegis",), build=("aegis 0.38.0",))
    rows = [ln for ln in as_text(render_sidebar(m, C, 56)).split("\n")
            if ln and not ln.startswith("── ")]
    assert len(rows) == 1
    assert "aegis" in rows[0] and "repos/aegis" in rows[0]


def test_no_stats_falls_back_to_the_system_tier():
    m = SidebarModel(stats=None, system=("cpu 34% ram 61% disk 82%",))
    assert "cpu 34%" in as_text(render_sidebar(m, C, 56))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sidebar_render.py -q -k "system or cwd_and_build or meter"`
Expected: FAIL — `CPU` is not rendered as a label.

- [ ] **Step 3: Implement**

```python
# Two gauges to a row above this width, one below: at 26 cells a pair of
# labelled bars leaves three cells each for the bar itself.
_PAIR_WIDTH = 40


def _system(m: SidebarModel, palette, width: int) -> Text | None:
    rows: list[Text] = []
    if m.stats is not None:
        per = 2 if width >= _PAIR_WIDTH else 1
        cells = (width - 2 * (per - 1)) // per
        ram_tail = (f"{m.stats.ram_used_gb:.1f}/{m.stats.ram_total_gb:.0f}G"
                    if m.stats.ram_total_gb else "")
        meters = [
            gauge("CPU", m.stats.cpu, f"{m.stats.cpu:.0f}%",
                  ctx_style(m.stats.cpu, palette), cells, palette),
            gauge("RAM", m.stats.ram, f"{m.stats.ram:.0f}%",
                  ctx_style(m.stats.ram, palette), cells, palette, tail=ram_tail),
            gauge("DSK", m.stats.disk, f"{m.stats.disk:.0f}%",
                  ctx_style(m.stats.disk, palette), cells, palette),
        ]
        rows += list(rows_of(meters, per).split("\n", allow_blank=False))
    else:
        rows += _rows([Segment("system", m.system, 0)], palette, width)
    rows += _rows([Segment("clock", m.clock, 0)], palette, width)
    # Merged, not dropped. They never change, but they are the two questions
    # a stale checkout makes you ask — which directory this aegis is rooted
    # at and which build is running — and the redesign has rows to spare.
    # `cwd` narrows first: the build string is the shorter and the less
    # compressible half.
    if m.cwd or m.build:
        merged = tuple(
            f"{c} [{palette.muted}]·[/] {b}"
            for b in (m.build or ("",)) for c in (m.cwd or ("",))
        )
        rows += _rows([Segment("where", merged, 0)], palette, width)
    if not rows:
        return None
    return _block(heading("SYSTEM", palette, width), rows)
```

Add `rows_of` to the `aegis.fleet.render` import.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sidebar_render.py tests/test_sidebar_system.py -q`
Expected: PASS, including `test_the_open_sidebar_answers_where_and_which_build` — that test reads a row containing `CWD` and asserts `BUILD` is somewhere in the paint, and the merged row satisfies both.

- [ ] **Step 5: Commit**

```bash
git commit -F - -- src/aegis/tui/sidebar.py tests/test_sidebar_render.py <<'MSG'
feat(sidebar): SYSTEM draws its three meters as bars

`cpu 34% ram 61% disk 82%` was a status-bar line in a column thirty rows
tall. Two rows of gauges instead, one per row below 40 cells.

CWD and BUILD merge onto one row rather than being cut: they are the two
questions a stale checkout makes you ask, and this redesign has rows to
spare.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 8: `QUEUES` shows saturation as a bar

**Files:**
- Modify: `src/aegis/tui/sidebar.py` — `_queues()` (line 164)
- Test: `tests/test_sidebar_render.py`

**Interfaces:**
- Consumes: `bar` (Task 1).
- Produces: nothing new. `format_q` in `strip.py` is **not** touched — it is shared with the collapsed `QueueStrip`, which is out of scope. The bar is prefixed at the composition site, the way `_repos` already composes its own heading around `render_repos`'s rows.

- [ ] **Step 1: Write the failing test**

```python
def test_a_queue_row_leads_with_its_saturation():
    m = SidebarModel(queues=Snapshot(queues=(
        QueueView(name="build", agent="claude", running=1, max_parallel=2,
                  queued=3, ok=5, err=0),
    )))
    row = [ln for ln in as_text(render_sidebar(m, C, 56)).split("\n")
           if "build" in ln][0]
    assert "█" in row and "●1/2" in row
    assert cell_len(row) <= 56


def test_a_queue_with_no_parallelism_configured_does_not_divide_by_zero():
    m = SidebarModel(queues=Snapshot(queues=(
        QueueView(name="idle", agent="claude", running=0, max_parallel=0,
                  queued=0, ok=0, err=0),
    )))
    assert "idle" in as_text(render_sidebar(m, C, 56))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sidebar_render.py -q -k queue`
Expected: FAIL — no `█` in the row.

- [ ] **Step 3: Implement**

```python
_QBAR = 9


def _queues(m: SidebarModel, palette, width: int) -> Text | None:
    snap = m.queues
    if snap is None or not snap.queues:
        return None
    rows = []
    for q in snap.queues:
        pct = 100 * q.running / q.max_parallel if q.max_parallel else 0
        row = Text()
        row.append_text(bar(pct, _QBAR, palette.work, palette))
        row.append(" ")
        # `format_q` is shared with the collapsed QueueStrip, so the bar is
        # prefixed here rather than added inside it. Its width budget is the
        # column minus what the bar took.
        row.append_text(format_q(q, palette, width - _QBAR - 1))
        rows.append(row)
    return _block(heading("QUEUES", palette, width), rows)
```

Add `bar` to the `aegis.fleet.render` import.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sidebar_render.py tests/test_tui_strip.py -q`
Expected: PASS. `test_tui_strip.py` is the guard that `format_q` was not touched.

- [ ] **Step 5: Commit**

```bash
git commit -F - -- src/aegis/tui/sidebar.py tests/test_sidebar_render.py <<'MSG'
feat(sidebar): a queue row leads with its saturation

Prefixed at the composition site rather than inside `format_q`, which the
collapsed QueueStrip shares and which is not in scope.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 9: One bar glyph in the program, not two

`monitor_strip._bar` draws `▓`/`░` at a fixed eight cells; `fleet.render.bar` draws `█`/`░` at a caller-chosen width with the empty half on `pal.rule`. Two bars in one program is the fault this whole change is removing.

This is the one change that lands **outside** the open F3 mode: `format_mon` is shared with the collapsed `MonitorStrip`. It is a glyph swap inside a row whose layout is untouched, and having the strip disagree with the sidebar about what a bar looks like is worse than the diff.

**Files:**
- Modify: `src/aegis/tui/monitor_strip.py` — `_bar` (line 24), `_tail_tiers` (line 34)
- Test: `tests/test_monitor_strip.py`

**Interfaces:**
- Consumes: `bar` (Task 1).
- Produces: nothing new. `format_mon(v, palette, width=None) -> Text` keeps its signature.

- [ ] **Step 1: Write the failing test**

```python
def test_a_monitor_bar_is_drawn_the_way_every_other_bar_is():
    """One glyph in the program. A strip that disagrees with the sidebar
    about what a bar looks like is the fault this change removes."""
    v = MonitorView(id="m", description="pytest", state="watching",
                    pct=62.0, eta_s=100, elapsed_s=160)
    row = format_mon(v, C, 56).plain
    assert "█" in row and "▓" not in row
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_monitor_strip.py -q -k every_other_bar`
Expected: FAIL — `assert '█' in row`, the row contains `▓`.

- [ ] **Step 3: Implement**

Delete `_bar` from `src/aegis/tui/monitor_strip.py` and use the shared one inside `_tail_tiers`:

```python
from aegis.fleet.render import bar as _shared_bar

_BAR_CELLS = 8
```

Replace the `bar = (f"  {_bar(v.pct)} ", palette.work)` tuple. `_tail_tiers`'s local `t()` helper builds a `Text` from `(text, style)` pairs, so the bar — which is already a styled `Text` — cannot go through it. Build that tier directly:

```python
    def with_bar(*parts: tuple[str, str]) -> Text:
        out = Text("  ")
        out.append_text(_shared_bar(v.pct, _BAR_CELLS, palette.work, palette))
        out.append(" ")
        for text, style in parts:
            out.append(text, style=style)
        return out

    pct = (f"{v.pct:.0f}%", palette.ink)
    if v.eta_s is None:
        return [with_bar(pct), t(("  ", palette.muted), pct)]
    eta = (f" · ETA {_fmt_dur(v.eta_s)}", palette.muted)
    return [
        with_bar(pct, eta),
        t(("  ", palette.muted), pct, eta),
        t(("  ", palette.muted), pct),
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_monitor_strip.py tests/test_sidebar_render.py -q`
Expected: PASS. Any pre-existing test asserting `▓` is asserting the old glyph; update it to `█`.

- [ ] **Step 5: Commit**

```bash
git commit -F - -- src/aegis/tui/monitor_strip.py tests/test_monitor_strip.py <<'MSG'
fix(monitor): one bar glyph in the program, not two

`monitor_strip._bar` drew ▓ at a fixed eight cells while every other bar
in aegis draws █ through `fleet.render.bar`. This lands in the collapsed
MonitorStrip too, since `format_mon` is shared — a glyph swap in a row
whose layout is untouched, and a strip that disagrees with the sidebar
about what a bar looks like is worse than the diff.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 10: `PLAN` gets a window and a progress bar

The section that can currently evict four others. Twenty tasks is twenty rows; after this it is six, for any plan length.

**Files:**
- Create: `src/aegis/plan/window.py`
- Modify: `src/aegis/tui/sidebar.py` — `_plan()` (line 128)
- Test: `tests/test_plan_window.py` (new), `tests/test_sidebar_render.py`

**Interfaces:**
- Consumes: `gauge` (Task 1); `PlanState` / `PlanTask` from `aegis.plan.models`.
- Produces:
  - `WINDOW_BEFORE: int = 1`, `WINDOW_AFTER: int = 2`
  - `window(state: PlanState, before: int = WINDOW_BEFORE, after: int = WINDOW_AFTER) -> tuple[PlanState, int]` — the windowed state and the count of tasks outside it

`render_plan_dock` keeps its current signature and contract. The window is a separate pure function: `render_plan_dock` has a contract asserted in `tests/test_plan_render.py` covering its header and its `(no plan)` body, and selecting which tasks to show is a different question from how a task row looks.

- [ ] **Step 1: Write the failing test**

Create `tests/test_plan_window.py`:

```python
"""Which plan tasks the sidebar shows.

A plan is unbounded and the column is not: twenty tasks is twenty rows,
which evicts QUEUES, MONITORS, REPOS and SYSTEM — the four sections
ordered below PLAN precisely because they are stable, not because they
are unimportant.
"""
from aegis.plan.models import PlanState, PlanTask
from aegis.plan.window import window


def _plan(n, current=None):
    return PlanState(tasks=tuple(
        PlanTask(key=str(i), subject=f"task {i}",
                 status="in_progress" if i == current
                 else "completed" if current is not None and i < current
                 else "pending")
        for i in range(n)))


def test_a_short_plan_is_shown_whole():
    w, hidden = window(_plan(3, current=1))
    assert len(w.tasks) == 3 and hidden == 0


def test_a_long_plan_keeps_the_current_task_with_a_neighbour_each_side():
    w, hidden = window(_plan(20, current=10))
    assert [t.key for t in w.tasks] == ["9", "10", "11", "12"]
    assert hidden == 16
    assert w.tasks[1].status == "in_progress"


def test_a_plan_with_nothing_in_progress_shows_the_head_of_the_queue():
    """All pending — nothing to centre on. The window must not raise and
    must not come back empty."""
    w, hidden = window(_plan(20))
    assert [t.key for t in w.tasks] == ["0", "1", "2"]
    assert hidden == 17


def test_a_finished_plan_shows_its_tail():
    """Every task completed. Centring on `current` is impossible; the
    interesting end is the one that just finished."""
    s = PlanState(tasks=tuple(
        PlanTask(key=str(i), subject=f"t{i}", status="completed")
        for i in range(20)))
    w, hidden = window(s)
    assert [t.key for t in w.tasks] == ["17", "18", "19"]
    assert hidden == 17


def test_an_empty_plan_windows_to_nothing():
    w, hidden = window(PlanState())
    assert w.tasks == () and hidden == 0


def test_the_window_preserves_the_whole_plans_counts():
    """`done` and `total` on the windowed state would lie. The caller reads
    them off the original, and this pins that the window does not pretend
    to be the plan."""
    full = _plan(20, current=10)
    w, _ = window(full)
    assert full.total == 20 and w.total == 4
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_plan_window.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.plan.window'`

- [ ] **Step 3: Implement**

Create `src/aegis/plan/window.py`:

```python
"""Which plan tasks a bounded column shows.

Pure, and separate from ``render_plan_dock`` on purpose: that renderer has
its own contract (its header, its ``(no plan)`` body) asserted in
``tests/test_plan_render.py``, and choosing which tasks to show is a
different question from how a task row looks. Two functions, one job each.
"""

from __future__ import annotations

from aegis.plan.models import PlanState

# One completed task above the current one and two pending below. The task
# above is what you just finished, which is the cheapest orientation there
# is; two below is how much of what comes next fits without the section
# starting to evict its neighbours.
WINDOW_BEFORE = 1
WINDOW_AFTER = 2


def window(
    state: PlanState,
    before: int = WINDOW_BEFORE,
    after: int = WINDOW_AFTER,
) -> tuple[PlanState, int]:
    """``(windowed, hidden)`` — the tasks to draw, and how many were left out.

    Centred on the in-progress task. With none in progress there is nothing
    to centre on, and the two cases differ: a plan not yet started is asking
    what comes first, and a finished one is asking what just happened, so the
    window takes the head in the first case and the tail in the second.

    The returned state's ``done`` and ``total`` describe the *window*, not
    the plan. The caller reads the real counts off the original.
    """
    tasks = state.tasks
    span = before + after + 1
    if len(tasks) <= span:
        return state, 0

    current = next((i for i, t in enumerate(tasks)
                    if t.status == "in_progress"), None)
    if current is not None:
        lo = max(0, current - before)
        hi = min(len(tasks), current + after + 1)
    else:
        # One row shorter with no current task: the extra row exists to mark
        # where you are, and there is nowhere to mark.
        n = span - 1
        # Not started asks what comes first; finished asks what just
        # happened. Head in the first case, tail in the second.
        lo = 0 if any(t.status != "completed" for t in tasks) else len(tasks) - n
        lo = max(0, lo)
        hi = min(len(tasks), lo + n)

    return PlanState(tasks=tasks[lo:hi]), len(tasks) - (hi - lo)
```

Then in `src/aegis/tui/sidebar.py`, rewrite `_plan`:

```python
def _plan(m: SidebarModel, palette, width: int) -> Text | None:
    if not m.plan and not m.subplans:
        return None
    # PlanState already computes both — do not re-derive them, and read them
    # off the WHOLE plan: the window's own counts describe the window.
    done = m.plan.done if m.plan else 0
    total = m.plan.total if m.plan else 0
    shown, hidden = window(m.plan or PlanState())
    # A fan-out's question is which subagent is still grinding, so each
    # subplan contributes its current task and nothing else.
    subs = {k: PlanState(tasks=(s.current,) if s.current else ())
            for k, s in (m.subplans or {}).items()}
    rows: list[Text] = []
    if total:
        rows.append(gauge("", 100 * done / total, f"{100 * done // total}%",
                          palette.ok, width, palette))
    # render_plan_dock verbatim: it already space-separates the circles
    # (East Asian Ambiguous — Rich measures one cell, terminals draw two,
    # neighbours overlap) and budgets labels at width - 9. Re-implementing
    # rows here would re-pay both bugs. Its own first line is dropped: the
    # heading already carries the counter at the right edge.
    body = render_plan_dock(shown, palette, working=m.plan_working,
                            frame=m.plan_frame, width=width, subplans=subs)
    rows += list(body.split("\n", allow_blank=False))[1:]
    if hidden:
        rows.append(Text(f"   +{hidden} more", style=palette.muted))
    head = heading("PLAN", palette, width, right=f"{done}/{total}" if total else "")
    return _block(head, rows)
```

Add `from aegis.plan.window import window` to the imports.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_plan_window.py tests/test_sidebar_render.py tests/test_plan_render.py -q`
Expected: PASS

- [ ] **Step 5: Add the sidebar-level test and run it**

```python
def test_a_long_plan_does_not_evict_the_sections_below_it():
    tasks = tuple(PlanTask(key=str(i), subject=f"task {i}",
                           status="in_progress" if i == 10 else "pending")
                  for i in range(20))
    m = SidebarModel(plan=PlanState(tasks=tasks),
                     system=("cpu 34% ram 61% disk 82%",))
    out = as_text(render_sidebar(m, C, 56))
    assert "SYSTEM" in out
    assert "+16 more" in out
    assert "task 10" in out
    assert "task 0" not in out
```

Run: `uv run pytest tests/test_sidebar_render.py -q -k evict`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git commit -F - -- src/aegis/plan/window.py src/aegis/tui/sidebar.py tests/test_plan_window.py tests/test_sidebar_render.py <<'MSG'
feat(plan): the sidebar's PLAN section is bounded

Twenty tasks was twenty rows, which pushed QUEUES, MONITORS, REPOS and
SYSTEM off the bottom of a 37-row column — the four sections ordered below
PLAN precisely because they are stable, not because they are unimportant.

A progress bar, the current task with a neighbour each side, and a count
of what is left out. Six rows for any plan. Subplans contribute their
current task only, which is the question a fan-out actually asks.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 11: Pin the row budget, and exercise it for real

A budget nobody measures regresses the first time a section grows. This makes the number a test.

**Files:**
- Test: `tests/test_sidebar_render.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: every task above.
- Produces: nothing.

- [ ] **Step 1: Write the test**

```python
# A 40-row terminal gives the sidebar 37 content rows: one to the TabBar,
# one to each SIDEBAR_PAD_Y. Before this redesign a session this size
# rendered 35 of them. The ceiling is what keeps the slack real.
ROW_CEILING = 31


def _full_model():
    """A realistic busy session: the shape the budget was measured against."""
    return SidebarModel(
        title="fix the eviction race",
        identity=("opus · high · local",),
        state_label="✻ working… · ◐ thinking",
        loop_status={"iteration": 3, "max_iterations": 20},
        now_line="reading pane.py to find where the recap lands",
        ctx=ContextGauge(pct=71, live=142_000, window=200_000),
        metrics=("↑142k ↓8.2k · $1.84 · 1:20",),
        quota_gauges=(QuotaGauge(label="cc 5h", percent=47.0,
                                 severity="normal", resets_in_s=11040),),
        plan=PlanState(tasks=tuple(
            PlanTask(key=str(i), subject=f"a task with a realistic subject {i}",
                     status="in_progress" if i == 10 else "pending")
            for i in range(20))),
        queues=Snapshot(queues=(
            QueueView(name="build", agent="claude", running=1, max_parallel=2,
                      queued=3, ok=5, err=0),
            QueueView(name="review", agent="claude", running=0, max_parallel=1,
                      queued=0, ok=2, err=1),
        )),
        monitors=[
            MonitorView(id="m1", description="pytest", state="watching",
                        pct=62.0, eta_s=100, elapsed_s=160),
            MonitorView(id="m2", description="docker build the web image",
                        state="watching", pct=None, eta_s=None, elapsed_s=430),
        ],
        stats=SystemStats(cpu=34.0, ram=61.0, disk=82.0),
        clock=("Mon 22 Sep · 18:41",),
        cwd=("CWD ~/Workspace/repos/aegis",),
        build=("aegis 0.38.0",),
    )


@pytest.mark.parametrize("width", [56, 40, 26])
def test_a_busy_session_fits_a_forty_row_terminal(width):
    out = as_text(render_sidebar(_full_model(), C, width))
    rows = out.split("\n")
    assert len(rows) <= ROW_CEILING, f"{len(rows)} rows at width {width}"


@pytest.mark.parametrize("width", [56, 40, 26])
def test_no_row_is_wider_than_the_column(width):
    """Textual clips silently, which is why `fit` exists. A gauge that
    overflows does not look like an overflow — it looks like the row below
    it moved."""
    for row in as_text(render_sidebar(_full_model(), C, width)).split("\n"):
        assert cell_len(row) <= width, repr(row)
```

Add `import pytest` if the file lacks it.

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_sidebar_render.py -q -k "forty_row or wider_than"`
Expected: PASS. If the row count exceeds 31, do not raise the ceiling — find which section grew and fix it. The ceiling is the deliverable.

- [ ] **Step 3: Mutation-check the budget test**

A ceiling test that cannot fail is worth less than none, because it licenses shipping.

```bash
uv run python - <<'PY'
import pathlib
p = pathlib.Path("src/aegis/tui/sidebar.py")
s = p.read_text()
p.write_text(s.replace('            out.append("\\n")\n', '            out.append("\\n\\n")\n', 1))
PY
uv run pytest tests/test_sidebar_render.py -q -k forty_row
git checkout -- src/aegis/tui/sidebar.py
```

Expected: FAIL while mutated (the blank separators come back, six rows over), PASS again after the checkout.

- [ ] **Step 4: Run the whole gate**

Run: `make check`
Expected: every stage green. Run it as the target, not as a bare `pytest` — `make test` adds `--max-unmarked-duration=3`, and a bare run goes green on a suite the gate fails.

Note: the full suite flakes one or two inotify tests on zion. A failure in `tests/test_watch*.py` or similar that passes on a re-run alone is that flake, not this change. Anything in `tests/test_sidebar*`, `tests/test_plan*`, `tests/test_fleet*`, `tests/test_monitor*`, `tests/test_tui_strip.py` or `tests/test_metrics.py` is this change.

- [ ] **Step 5: Exercise it the way a user reaches it**

`make check` passing is not done. Start a daemon **after** the change, attach at 40 rows, press `F3`, and confirm against the spec's mockup:

```bash
uv run aegis --help >/dev/null   # import check
# then, in a 40-row terminal:
uv run aegis
```

Check, in the running TUI: the headings are rules and there are no blank rows between sections; `CTX`, `QUOTA`, `CPU`, `RAM`, `DSK` and the queues all draw `█` bars; the state label sits on the `SESSION` rule; a long recap line wraps with its continuation indented; and with a plan of more than four tasks, `+k more` appears and `SYSTEM` is still on screen.

Green tests against a daemon that booted before the change prove nothing about the change.

- [ ] **Step 6: Write the CHANGELOG entry and commit**

Add under the unreleased heading in `CHANGELOG.md`:

```markdown
### Changed

- The `F3` sidebar draws its fractions as bars. Context, quota, loop, plan
  progress, queue saturation and the three host meters are gauges now, sharing
  the renderer the `F10` fleet band already used. Section headings are rules
  and no longer cost a blank row each, and `PLAN` shows a window around the
  current task rather than every task — a twenty-task plan used to push
  `QUEUES`, `MONITORS`, `REPOS` and `SYSTEM` off the bottom of the column.
  A busy session went from 35 rows to 29, which fits a 40-row terminal with
  slack instead of two rows of headroom.
- Monitor bars draw `█` rather than `▓`, in the collapsed strip as well as the
  sidebar, so every bar in aegis is drawn the same way.
```

```bash
git commit -F - -- CHANGELOG.md tests/test_sidebar_render.py <<'MSG'
test(sidebar): the row budget is a test, not a hope

35 rows to 29 on a busy session, with a ceiling at 31 and a per-row width
assertion at each of the three widths. Textual clips silently, so an
overflowing gauge does not look like an overflow — it looks like the row
below it moved.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

## Notes for the executor

**Order matters.** Task 1 unblocks 3, 6, 7, 8, 9, 10. Task 4 unblocks 5, which unblocks 6 and 7. Tasks 8, 9 and 10 are independent of each other once 1 is done.

**The spec's mockup is the acceptance criterion**, not a sketch. If a section renders differently from `docs/superpowers/specs/2026-09-22-aegis-f3-sidebar-redesign-design.md`'s block, either the code is wrong or the spec needs amending — say which, do not quietly diverge.

**`aegis.plan.window` is new and pure.** No Textual import, no clock, no I/O. If a test for it needs an app running, the function is in the wrong place.
