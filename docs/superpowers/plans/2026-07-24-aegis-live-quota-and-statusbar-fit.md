# Live Claude Quota + Status-Bar Fit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** shipped — Tasks 1-9 landed `231caec`..`0dee5ee`; live smoke of the TUI segment still pending.

**Goal:** Show live Claude subscription quota (5-hour and weekly windows) in the TUI status bar whenever a Claude agent is open, and make the status bar degrade gracefully to the terminal width instead of silently clipping.

**Architecture:** A new `src/aegis/usage/quota.py` owns the fetch (undocumented OAuth usage endpoint), a 60-second polling cache (`QuotaService`), and pure rendering. A new pure module `src/aegis/tui/fit.py` composes the status bar from priority-ranked segments, degrading each through declared tiers until the line fits. `StatusBar` keeps its `set_*` API but each setter now takes tiers; `AegisApp._tick` pushes the quota state exactly where it already samples `sysmeter`.

**Tech Stack:** Python 3.13, `uv`, pytest + pytest-asyncio, Textual 8.2.6, stdlib `urllib` (no new dependency).

**Spec:** `docs/superpowers/specs/2026-07-24-aegis-live-quota-and-statusbar-fit-design.md`

## Global Constraints

- Package manager is `uv`. Run tests with `uv run python -m pytest`, never bare `pytest`.
- Run pytest as its **own step** and check the exit code. Never pipe it into `tail`/`head` in an `&&` chain — that masks the exit status.
- Code, comments, identifiers and commit messages in **English**.
- Commit straight to `main`. No feature branch, no PR.
- This repo's checkout is shared with parallel agents. Commit with explicit paths (`git commit -- <paths>`), never `git add -A`.
- **No new dependency.** The fetch uses stdlib `urllib.request`.
- **No network in the test suite.** Every fetch test injects a fake opener; every service test injects a fake clock and a fake fetch.
- Ruff reports one **pre-existing** error, `F821 Undefined name 'Workspace'` at `src/aegis/tui/app.py:132`. It is not yours; do not fix it, and do not treat it as a regression.
- `tests/tui/`, `tests/test_sysmeter.py` and `tests/test_terminal_tab.py` leak Textual theme state, so suites running after them can fail with `UnresolvedVariableError: reference to undefined variable '$background'`. Pre-existing. If you see it, re-run the affected file alone to confirm.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/aegis/tui/fit.py` | **new** — `plain_width`, `Segment`, `fit`: pure width-aware composition |
| `src/aegis/tui/widgets.py` | `StatusBar` builds segments and calls `fit`; `set_quota`; `on_resize` |
| `src/aegis/tui/sysmeter.py` | add `format_system_tiers` beside the existing `format_system` |
| `src/aegis/tui/metrics.py` | add `SessionMetrics.render_tiers`; `render()` delegates to tier 0 |
| `src/aegis/tui/pane.py` | `set_quota` passthrough; `refresh_metrics` pushes tiers |
| `src/aegis/usage/quota.py` | **new** — token read, fetch, parse, `QuotaService`, rendering |
| `src/aegis/tui/app.py` | construct `QuotaService`, visibility predicate, tick push, turn-end refresh |
| `src/aegis/commands/builtins/usage.py` | `quota` view |
| `tests/test_statusbar_fit.py` | **new** — `fit` table tests |
| `tests/test_statusbar_segments.py` | **new** — `StatusBar` tier composition |
| `tests/test_quota.py` | **new** — token read, parse, fetch |
| `tests/test_quota_service.py` | **new** — cadence, floor, state transitions |
| `tests/test_quota_render.py` | **new** — every rendering branch |
| `tests/test_quota_visibility.py` | **new** — the predicate |
| `tests/test_usage_quota_command.py` | **new** — `/usage quota` |
| `tests/test_metrics.py` | extend with tier assertions |

---

### Task 1: The fit engine

**Files:**
- Create: `src/aegis/tui/fit.py`
- Test: `tests/test_statusbar_fit.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `plain_width(text: str) -> int`; `strip_markup(text: str) -> str`; `Segment(key: str, tiers: tuple[str, ...], priority: int)` (frozen dataclass); `fit(segments: Sequence[Segment], width: int, sep: str = "    ") -> str`.

- [x] **Step 1: Write the failing tests**

Create `tests/test_statusbar_fit.py`:

```python
from aegis.tui.fit import Segment, fit, plain_width, strip_markup


def test_plain_width_ignores_markup():
    assert plain_width("[dim]abc[/]") == 3
    assert plain_width("plain") == 5


def test_strip_markup_removes_tags():
    assert strip_markup("[dim]abc[/] [red]d[/]") == "abc d"


def _segs():
    return [
        Segment("identity", ("aegis 0.21.0 opus", "opus"), 20),
        Segment("state", ("working",), 60),
        Segment("system", ("CPU 1% RAM 2%", "1/2%"), 10),
    ]


def test_unmeasured_width_renders_widest():
    assert fit(_segs(), 0) == "aegis 0.21.0 opus    working    CPU 1% RAM 2%"


def test_degrades_lowest_priority_first():
    # Widest is 45 cols. At 40 only the lowest-priority segment narrows.
    assert fit(_segs(), 40) == "aegis 0.21.0 opus    working    1/2%"


def test_drops_when_narrowest_still_does_not_fit():
    # system is already narrowest, so it is dropped before identity narrows.
    assert fit(_segs(), 30) == "aegis 0.21.0 opus    working"


def test_degrades_next_priority_after_a_drop():
    assert fit(_segs(), 20) == "opus    working"


def test_never_drops_the_highest_priority_segment():
    assert fit(_segs(), 5) == "worki"


def test_empty_tier_strings_are_skipped():
    segs = [Segment("a", ("",), 10), Segment("b", ("bee",), 20)]
    assert fit(segs, 0) == "bee"


def test_render_order_follows_list_order_not_priority():
    segs = [Segment("low", ("zzz",), 1), Segment("high", ("aaa",), 99)]
    assert fit(segs, 0) == "zzz    aaa"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_statusbar_fit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.tui.fit'`

- [x] **Step 3: Create `src/aegis/tui/fit.py`**

```python
"""Width-aware composition for the status bar.

Textual clips an over-long status line silently, so the right-hand segments
vanish without a trace. ``fit`` degrades segments by priority until the line
fits: the lowest-priority segment that can still narrow does so, and a segment
already at its narrowest is dropped. Pure — no Textual import — so it is
testable as a plain function.

Segments are rendered in *list* order and degraded in *priority* order; the two
are deliberately independent, so visual layout does not dictate what survives a
narrow terminal.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

_MARKUP = re.compile(r"\[[^\]]*\]")


def strip_markup(text: str) -> str:
    """``text`` with Rich markup tags removed."""
    return _MARKUP.sub("", text)


def plain_width(text: str) -> int:
    """Visible width of ``text``, ignoring Rich markup tags.

    Glyphs used in the bar (↑ ↓ ⚒ ⚡ ⧗ ⟳ ✻) are single-width in the terminals
    aegis targets, so a character count is accurate enough; no wcwidth
    dependency is pulled in for the general case.
    """
    return len(strip_markup(text))


@dataclass(frozen=True)
class Segment:
    """One status-bar segment.

    ``tiers`` runs widest-first; the last entry is the narrowest form the
    segment can take before being dropped entirely. ``priority`` is compared
    across segments — higher survives longer.
    """
    key: str
    tiers: tuple[str, ...]
    priority: int


def fit(segments: Sequence[Segment], width: int, sep: str = "    ") -> str:
    """Compose ``segments`` into a line no wider than ``width``.

    ``width <= 0`` means "unmeasured" (the widget has not been laid out yet)
    and renders every segment at tier 0.
    """
    live = [(s, 0) for s in segments if s.tiers]

    def render(items: list[tuple[Segment, int]]) -> str:
        return sep.join(s.tiers[i] for s, i in items if s.tiers[i])

    if width <= 0:
        return render(live)

    while live and plain_width(render(live)) > width:
        # Exhaust the lowest-priority segment — every tier, then drop it —
        # before touching anything above it. Degrading a higher-priority
        # segment while a lower one still has room to give would invert the
        # ranking. Ties broken by position so the pass is stable.
        order = sorted(range(len(live)), key=lambda k: (live[k][0].priority, k))
        k = order[0]
        seg, tier = live[k]
        if tier + 1 < len(seg.tiers):
            live[k] = (seg, tier + 1)
        elif len(live) > 1:
            live.pop(k)
        else:
            break

    out = render(live)
    if plain_width(out) > width:
        # One segment left and still too wide: hard-truncate the plain text.
        out = strip_markup(out)[:width]
    return out
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_statusbar_fit.py -q`
Expected: PASS, 8 passed

- [x] **Step 5: Lint**

Run: `uv run ruff check src/aegis/tui/fit.py tests/test_statusbar_fit.py`
Expected: no findings.

- [x] **Step 6: Commit**

```bash
git commit -m "feat(tui): width-aware status-bar composition" -- src/aegis/tui/fit.py tests/test_statusbar_fit.py
```

---

### Task 2: Metrics render tiers

**Files:**
- Modify: `src/aegis/tui/metrics.py:197-227`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `SessionMetrics.render_tiers(now: float) -> tuple[str, str, str, str]`, widest-first. `SessionMetrics.render(now)` keeps its current signature and returns `render_tiers(now)[0]`, so every existing caller is unchanged.

Metrics is 110 of the bar's ~226 columns, so it needs four tiers rather than two.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_metrics.py`:

```python
def _loaded_metrics():
    m = SessionMetrics()
    m.c_in, m.c_out, m.c_cached, m.c_think = 70000, 12000, 56000, 9600
    m.tool_calls, m.tool_errors = 27, 1
    m.context_window, m.last_true_input = 200000, 88200
    return m


def test_render_tiers_widest_equals_render():
    m = _loaded_metrics()
    assert m.render_tiers(0.0)[0] == m.render(0.0)


def test_render_tiers_narrow_monotonically():
    tiers = _loaded_metrics().render_tiers(0.0)
    from aegis.tui.fit import plain_width
    widths = [plain_width(t) for t in tiers]
    assert widths == sorted(widths, reverse=True)
    assert len(tiers) == 4


def test_tier1_drops_tools():
    tiers = _loaded_metrics().render_tiers(0.0)
    assert "⚒" in tiers[0]
    assert "⚒" not in tiers[1]


def test_tier2_drops_shares_and_shortens_ctx():
    tiers = _loaded_metrics().render_tiers(0.0)
    assert "cached" in tiers[1]
    assert "cached" not in tiers[2]
    assert "think" not in tiers[2]
    assert "ctx 44%" in tiers[2]


def test_tier3_is_the_irreducible_core():
    tiers = _loaded_metrics().render_tiers(0.0)
    assert "ctx" not in tiers[3]
    assert "↑" in tiers[3] and "↓" in tiers[3]
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_metrics.py -q`
Expected: FAIL — `AttributeError: 'SessionMetrics' object has no attribute 'render_tiers'`

- [x] **Step 3: Replace `SessionMetrics.render` with a tiered builder**

In `src/aegis/tui/metrics.py`, replace the whole `render` method (currently lines 197-227) with:

```python
    def render(self, now: float) -> str:
        """Widest form of the status-line metrics segment."""
        return self.render_tiers(now)[0]

    def render_tiers(self, now: float) -> tuple[str, str, str, str]:
        """Four progressively narrower forms of the metrics segment.

        T0 is everything. T1 drops the tool counter and throughput — both are
        curiosities rather than decisions. T2 additionally drops the cached and
        reasoning shares and reduces ``ctx`` to its percentage, which is the
        part you act on. T3 is the irreducible core: tokens, cost, turn time.
        """
        in_t = self.c_in + self.p_in
        out = self.c_out + self.p_out
        cached = self.c_cached + self.p_cached
        pct = round(100 * cached / in_t) if in_t else 0
        think_seg = ""
        if self.c_think > 0 and out > 0:
            think_seg = f" ({round(100 * self.c_think / out)}% think)"
        mark = "~" if self._provisional else ""
        tool = f"⚒ {self.tool_calls}"
        if self.tool_errors:
            tool += f" ({self.tool_errors} err)"
        ctx = ctx_short = ""
        if self.context_window > 0:
            live = self.p_in if self._provisional else self.last_true_input
            ctx_pct = round(100 * live / self.context_window)
            ctx = f"ctx {_fmt_tokens(live)} ({ctx_pct}%) · "
            ctx_short = f"ctx {ctx_pct}% · "
        cost = self._render_cost()
        tps = self.recent_tps()
        tps_seg = f"⚡ {round(tps)} tok/s · " if tps is not None else ""
        turn = _fmt_time(self.turn_seconds(now))
        session = _fmt_time(self.session_seconds(now))
        head = f"{mark}↑{_fmt_tokens(in_t)}"
        head_full = f"{head} ({pct}% cached) ↓{_fmt_tokens(out)}{think_seg} · "
        head_bare = f"{head} ↓{_fmt_tokens(out)} · "
        return (
            f"{head_full}{tps_seg}{ctx}{cost}{tool} · {turn} / {session}",
            f"{head_full}{ctx}{cost}{turn} / {session}",
            f"{head_bare}{ctx_short}{cost}{turn} / {session}",
            f"{head_bare}{cost}{turn}".rstrip(" ·"),
        )
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_metrics.py -q`
Expected: PASS — existing tests still green because `render` is unchanged in behaviour.

- [x] **Step 5: Commit**

```bash
git commit -m "feat(tui): metrics renders four progressively narrower tiers" -- src/aegis/tui/metrics.py tests/test_metrics.py
```

---

### Task 3: StatusBar adopts fit

**Files:**
- Modify: `src/aegis/tui/widgets.py:219-299`
- Modify: `src/aegis/tui/sysmeter.py` (add `format_system_tiers`)
- Modify: `src/aegis/tui/pane.py:688-699`
- Modify: `src/aegis/tui/app.py:649-660`
- Test: `tests/test_statusbar_segments.py`

**Interfaces:**
- Consumes: `Segment`, `fit`, `plain_width` from Task 1; `SessionMetrics.render_tiers` from Task 2.
- Produces: `short_model(name: str) -> str` in `aegis.tui.widgets`; every `StatusBar` setter accepts `str | Sequence[str]`; `StatusBar.set_quota(tiers)` exists but is fed in Task 7. Priorities: connection 70, state 60, loop 50, quota 40, metrics 30, identity 20, system 10.

- [x] **Step 1: Write the failing tests**

Create `tests/test_statusbar_segments.py`:

```python
from aegis.themes import AegisColors
from aegis.tui.state import AgentState
from aegis.tui.widgets import StatusBar, short_model

COLORS = AegisColors(
    ready="green", working="yellow", error="red", accent="blue",
    muted="grey50", ok="green", err="red", user="blue", user_bg="black")


def test_short_model_strips_prefix_and_dots_the_version():
    assert short_model("claude-opus-4-8") == "opus-4.8"
    assert short_model("claude-sonnet-5") == "sonnet-5"
    assert short_model("gpt-5") == "gpt-5"


def _bar():
    bar = StatusBar("claude-opus-4-8", "high", COLORS)
    bar.set_state(AgentState.working)
    bar.set_metrics(("METRICS-FULL", "METRICS-MID", "METRICS-SHORT"))
    bar.set_system(("CPU 1% · RAM 2% · DSK 3%", "1·2·3%"))
    return bar


def test_unmeasured_bar_renders_everything():
    text = _bar().render_plain()
    assert "aegis" in text
    assert "claude-opus-4-8" in text
    assert "METRICS-FULL" in text
    assert "CPU 1%" in text


def test_setters_accept_a_bare_string():
    bar = _bar()
    bar.set_metrics("JUST-ONE")
    assert "JUST-ONE" in bar.render_plain()


def test_loop_segment_hidden_when_none():
    bar = _bar()
    bar.set_loop(None)
    assert "loop" not in bar.render_plain()
    bar.set_loop({"iteration": 3, "max_iterations": 20})
    assert "⟳ loop 3/20" in bar.render_plain()


def test_quota_segment_hidden_when_empty():
    bar = _bar()
    bar.set_quota(())
    assert "⧗" not in bar.render_plain()
    bar.set_quota(("⧗ 5h 64% · wk 7%", "⧗ 64/7%"))
    assert "⧗ 5h 64%" in bar.render_plain()


def test_system_is_dropped_before_identity_when_narrow():
    bar = _bar()
    bar._width_override = 60          # test seam, see implementation
    text = bar.render_plain()
    assert "CPU 1%" not in text
    assert "opus" in text


def test_state_always_survives():
    bar = _bar()
    bar._width_override = 12
    assert "working" in bar.render_plain()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_statusbar_segments.py -q`
Expected: FAIL — `ImportError: cannot import name 'short_model'`

- [x] **Step 3: Rewrite `StatusBar` in `src/aegis/tui/widgets.py`**

Replace lines 219-299 (the whole class) with:

```python
def short_model(name: str) -> str:
    """``claude-opus-4-8`` -> ``opus-4.8``; unrecognised shapes pass through."""
    import re
    return re.sub(r"-(\d+)-(\d+)$", r"-\1.\2", name.removeprefix("claude-"))


class StatusBar(Static):
    """`<agent> · <model> · <permission>`, state label, then metrics.

    Segments are composed through ``aegis.tui.fit``: each carries a tuple of
    progressively narrower forms plus a priority, and the bar degrades from the
    bottom of the priority order until it fits the terminal. Static segments
    (identity, system stats) rank lowest deliberately — on a narrow terminal you
    should lose what never changes and keep what does.
    """

    # Priority ladder — higher survives longer. Ranked by "does this change,
    # and does it demand action", not by how interesting it is.
    P_CONNECTION, P_STATE, P_LOOP = 70, 60, 50
    P_QUOTA, P_METRICS, P_IDENTITY, P_SYSTEM = 40, 30, 20, 10

    def __init__(self, model: str, effort: str, colors) -> None:
        super().__init__(markup=True)
        # Identity is just model · effort — the session name/handle already
        # lives in the tab bar, so repeating it here is noise.
        from aegis.version import BUILD
        eff = f"[{colors.accent}]{effort}[/]"
        self._identity: tuple[str, ...] = (
            f"[dim]aegis {BUILD}[/]  {model}  {eff}",
            f"{short_model(model)} {eff}",
            short_model(model),
        )
        self._state = AgentState.ready
        self._metrics: tuple[str, ...] = ()
        self._system: tuple[str, ...] = ()
        self._loop: tuple[str, ...] = ()
        self._quota: tuple[str, ...] = ()
        self._connection: tuple[str, ...] = ()
        self._plain_content: str = ""
        # Tests set this to exercise narrow widths without a live layout.
        self._width_override: int | None = None

    @staticmethod
    def _tiers(value) -> tuple[str, ...]:
        """Normalise a setter argument to a tier tuple."""
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,) if value else ()
        return tuple(t for t in value if t)

    def on_mount(self) -> None:
        self._refresh()

    def on_resize(self) -> None:
        self._refresh()

    def set_state(self, state: AgentState) -> None:
        self._state = state
        self._refresh()

    def set_metrics(self, text) -> None:
        self._metrics = self._tiers(text)
        self._refresh()

    def set_system(self, text) -> None:
        """System-stats segment (CPU/RAM/disk); empty hides it."""
        self._system = self._tiers(text)
        self._refresh()

    def set_quota(self, text) -> None:
        """Claude subscription-quota segment; empty hides it."""
        self._quota = self._tiers(text)
        self._refresh()

    def set_loop(self, status: dict | None) -> None:
        """Loop segment (``⟳ loop 3/20``); None hides it."""
        self._loop = () if status is None else (
            f"⟳ loop {status['iteration']}/{status['max_iterations']}",
            f"⟳{status['iteration']}/{status['max_iterations']}",
        )
        self._refresh()

    def set_connection_state(self, up: bool, reason: str = "") -> None:
        """Show/hide a disconnected indicator on the right of the bar."""
        self._connection = () if up else (
            "⚠ disconnected — reconnecting…", "⚠ disconnected")
        self._refresh()

    def render_plain(self) -> str:
        """Current bar content as a plain string (strips Rich markup)."""
        from aegis.tui.fit import strip_markup
        return strip_markup(self._plain_content)

    def _available_width(self) -> int:
        if self._width_override is not None:
            return self._width_override
        try:
            return int(self.size.width)
        except Exception:  # noqa: BLE001 — not laid out yet
            return 0

    def _refresh(self) -> None:
        import contextlib

        from aegis.tui.fit import Segment, fit
        # List order is visual order; priority is independent of it.
        segments = [
            Segment("identity", self._identity, self.P_IDENTITY),
            Segment("state", (self._state.label,), self.P_STATE),
            Segment("metrics", self._metrics, self.P_METRICS),
            Segment("loop", self._loop, self.P_LOOP),
            Segment("quota", self._quota, self.P_QUOTA),
            Segment("system", self._system, self.P_SYSTEM),
            Segment("connection", self._connection, self.P_CONNECTION),
        ]
        line = fit(segments, self._available_width())
        self._plain_content = line
        with contextlib.suppress(Exception):
            self.update(line)
```

- [x] **Step 4: Add the sysmeter short tier**

Append to `src/aegis/tui/sysmeter.py`:

```python
def format_system_tiers(stats: SystemStats, colors) -> tuple[str, str]:
    """Widest and narrowest forms of the system segment.

    The narrow form drops the labels: three numbers in a fixed order are
    self-explanatory once you have seen the wide form once.
    """
    def val(pct: float) -> str:
        v = f"{pct:.0f}"
        return f"[{colors.working}]{v}[/]" if pct >= HIGH_THRESHOLD else v

    short = (f"{val(stats.cpu)}·{val(stats.ram)}·{val(stats.disk)}%")
    return (format_system(stats, colors), short)
```

- [x] **Step 5: Push tiers from the pane and the app**

In `src/aegis/tui/pane.py`, change `refresh_metrics` (line 693) to push tiers, and add `set_quota` after `set_system` (line 699):

```python
    def refresh_metrics(self) -> None:
        # Core observer callbacks (`_on_core_event`/`_on_core_state`) can fire
        # before this pane finishes mounting its StatusBar; no-op until it's up.
        bars = self.query(StatusBar)
        if bars:
            bars.first().set_metrics(
                self._core.metrics.render_tiers(time.monotonic()))

    def set_quota(self, tiers) -> None:
        """Push the quota segment (sampled app-side) to the StatusBar."""
        bars = self.query(StatusBar)
        if bars:
            bars.first().set_quota(tiers)
```

In `src/aegis/tui/app.py`, change the sysmeter push inside `_tick` (line 656-660):

```python
            from aegis.tui.sysmeter import format_system_tiers, sample_system
            # One app-side sample per tick (not per pane); local host stats.
            with contextlib.suppress(Exception):
                stats = sample_system(self._cwd)
                active.set_system(format_system_tiers(stats, self._palette))
```

- [x] **Step 6: Run the tests**

Run: `uv run python -m pytest tests/test_statusbar_segments.py tests/test_metrics.py tests/test_sysmeter.py -q`
Expected: PASS

- [x] **Step 7: Run the TUI-adjacent suites for regressions**

Run: `uv run python -m pytest tests/tui -q`
Expected: PASS. If you hit `UnresolvedVariableError: reference to undefined variable '$background'`, re-run the failing file alone to confirm it is the pre-existing theme leak described in Global Constraints.

- [x] **Step 8: Commit**

```bash
git commit -m "feat(tui): status bar fits the terminal instead of clipping" -- src/aegis/tui/widgets.py src/aegis/tui/sysmeter.py src/aegis/tui/pane.py src/aegis/tui/app.py tests/test_statusbar_segments.py
```

---

### Task 4: Quota fetch and parse

**Files:**
- Create: `src/aegis/usage/quota.py`
- Test: `tests/test_quota.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `QuotaWindow(kind, percent, severity, resets_at, is_active)`; `QuotaSnapshot(windows, fetched_at)` with `.window(kind)`; `QuotaError(Exception)` with `.kind`; `credentials_path() -> Path`; `read_token(path=None) -> str | None`; `parse_quota(payload: dict, *, now: float) -> QuotaSnapshot`; `fetch_quota(token, *, timeout=10.0, opener=None, now=None) -> QuotaSnapshot`.

- [x] **Step 1: Write the failing tests**

Create `tests/test_quota.py`:

```python
import json
import urllib.error
from datetime import timezone

import pytest

from aegis.usage.quota import (
    QuotaError, fetch_quota, parse_quota, read_token,
)

# Trimmed from a real response — note the null-heavy optional fields.
LIVE = {
    "five_hour": {"utilization": 64.0,
                  "resets_at": "2026-07-24T17:29:59.110568+00:00"},
    "seven_day": {"utilization": 7.0,
                  "resets_at": "2026-07-31T05:59:59.110600+00:00"},
    "seven_day_opus": None,
    "limits": [
        {"kind": "session", "group": "session", "percent": 64,
         "severity": "normal", "resets_at": "2026-07-24T17:29:59.110568+00:00",
         "scope": None, "is_active": True},
        {"kind": "weekly_all", "group": "weekly", "percent": 7,
         "severity": "normal", "resets_at": "2026-07-31T05:59:59.110600+00:00",
         "scope": None, "is_active": False},
    ],
}


def test_read_token_returns_none_when_missing(tmp_path):
    assert read_token(tmp_path / "nope.json") is None


def test_read_token_returns_none_on_garbage(tmp_path):
    p = tmp_path / "creds.json"
    p.write_text("not json{")
    assert read_token(p) is None


def test_read_token_reads_the_oauth_field(tmp_path):
    p = tmp_path / "creds.json"
    p.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-123"}}))
    assert read_token(p) == "tok-123"


def test_parse_reads_the_limits_array():
    snap = parse_quota(LIVE, now=100.0)
    assert [w.kind for w in snap.windows] == ["session", "weekly_all"]
    assert snap.window("session").percent == 64.0
    assert snap.window("session").is_active is True
    assert snap.fetched_at == 100.0


def test_parse_keeps_reset_timestamps_as_utc():
    snap = parse_quota(LIVE, now=0.0)
    ts = snap.window("session").resets_at
    assert ts.tzinfo is not None
    assert ts.astimezone(timezone.utc).hour == 17


def test_parse_drops_unreadable_entries_without_failing():
    payload = {"limits": [
        {"kind": "session", "percent": 12},
        {"kind": None, "percent": 3},
        {"percent": 9},
        "garbage",
        {"kind": "weekly_all", "percent": None},
    ]}
    snap = parse_quota(payload, now=0.0)
    assert [w.kind for w in snap.windows] == ["session"]


def test_parse_falls_back_to_local_severity():
    payload = {"limits": [
        {"kind": "a", "percent": 50},
        {"kind": "b", "percent": 85},
        {"kind": "c", "percent": 97},
    ]}
    sev = {w.kind: w.severity for w in parse_quota(payload, now=0.0).windows}
    assert sev == {"a": "normal", "b": "warning", "c": "critical"}


def test_parse_prefers_the_api_severity():
    payload = {"limits": [{"kind": "a", "percent": 10,
                           "severity": "critical"}]}
    assert parse_quota(payload, now=0.0).windows[0].severity == "critical"


def test_parse_survives_a_missing_limits_array():
    assert parse_quota({}, now=0.0).windows == ()


class _Resp:
    def __init__(self, body):
        self._body = body
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_fetch_parses_a_good_response():
    def opener(req, timeout=None):
        assert req.get_header("Authorization") == "Bearer tok"
        assert req.get_header("Anthropic-beta") == "oauth-2025-04-20"
        return _Resp(json.dumps(LIVE).encode())
    snap = fetch_quota("tok", opener=opener, now=5.0)
    assert snap.window("session").percent == 64.0


def test_fetch_maps_401_to_unauthorized():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError("u", 401, "no", {}, None)
    with pytest.raises(QuotaError) as e:
        fetch_quota("tok", opener=opener)
    assert e.value.kind == "unauthorized"


def test_fetch_maps_500_to_unreachable():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError("u", 500, "boom", {}, None)
    with pytest.raises(QuotaError) as e:
        fetch_quota("tok", opener=opener)
    assert e.value.kind == "unreachable"


def test_fetch_maps_timeout_to_unreachable():
    def opener(req, timeout=None):
        raise TimeoutError
    with pytest.raises(QuotaError) as e:
        fetch_quota("tok", opener=opener)
    assert e.value.kind == "unreachable"


def test_fetch_maps_truncated_body_to_unreachable():
    def opener(req, timeout=None):
        return _Resp(b'{"limits": [')
    with pytest.raises(QuotaError) as e:
        fetch_quota("tok", opener=opener)
    assert e.value.kind == "unreachable"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_quota.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.usage.quota'`

- [x] **Step 3: Create `src/aegis/usage/quota.py`**

```python
"""Live Claude subscription quota — the 5-hour and weekly windows.

Claude Code stores an OAuth token locally; Anthropic exposes current window
utilisation at an undocumented endpoint behind it. This module reads the token,
asks the endpoint, and caches the answer.

The endpoint is undocumented and may change or vanish. Every failure path here
degrades to a message in the status bar; nothing raises into the render loop.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

URL = "https://api.anthropic.com/api/oauth/usage"
BETA = "oauth-2025-04-20"

# Used only when the API omits its own `severity` for a window.
WARNING_AT = 80.0
CRITICAL_AT = 95.0


class QuotaError(Exception):
    """A fetch failed. ``kind`` is ``unauthorized`` or ``unreachable``."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class QuotaWindow:
    kind: str                    # "session", "weekly_all", "weekly_opus", ...
    percent: float
    severity: str                # "normal" | "warning" | "critical"
    resets_at: datetime | None
    is_active: bool


@dataclass(frozen=True)
class QuotaSnapshot:
    windows: tuple[QuotaWindow, ...]
    fetched_at: float            # monotonic

    def window(self, kind: str) -> QuotaWindow | None:
        for w in self.windows:
            if w.kind == kind:
                return w
        return None


def credentials_path() -> Path:
    """Where Claude Code keeps its OAuth token. ``CLAUDE_CREDS`` overrides."""
    default = Path.home() / ".claude" / ".credentials.json"
    return Path(os.environ.get("CLAUDE_CREDS", str(default)))


def read_token(path: Path | None = None) -> str | None:
    """The OAuth access token, or None if there isn't a usable one.

    Never raises: a missing file, unreadable JSON and an absent field are all
    the same answer — we cannot ask about quota.
    """
    p = path or credentials_path()
    try:
        with Path(p).open() as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    token = (data.get("claudeAiOauth") or {}).get("accessToken")
    return token or None


def _severity(percent: float, given) -> str:
    if given in ("normal", "warning", "critical"):
        return given
    if percent >= CRITICAL_AT:
        return "critical"
    if percent >= WARNING_AT:
        return "warning"
    return "normal"


def _timestamp(raw) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_quota(payload: dict, *, now: float) -> QuotaSnapshot:
    """Build a snapshot from the response's ``limits`` array.

    The array is preferred over the ``five_hour`` / ``seven_day`` headline
    objects: it is the complete set of windows, and each entry carries the
    API's own ``severity``. Entries that cannot be read are dropped rather than
    failing the whole snapshot — the payload is null-heavy and its shape is not
    contractual.
    """
    windows: list[QuotaWindow] = []
    for raw in payload.get("limits") or ():
        if not isinstance(raw, dict):
            continue
        kind = raw.get("kind")
        percent = raw.get("percent")
        if not isinstance(kind, str) or not kind:
            continue
        if not isinstance(percent, (int, float)) or isinstance(percent, bool):
            continue
        windows.append(QuotaWindow(
            kind=kind,
            percent=float(percent),
            severity=_severity(float(percent), raw.get("severity")),
            resets_at=_timestamp(raw.get("resets_at")),
            is_active=bool(raw.get("is_active")),
        ))
    return QuotaSnapshot(windows=tuple(windows), fetched_at=now)


def fetch_quota(token: str, *, timeout: float = 10.0,
                opener=None, now: float | None = None) -> QuotaSnapshot:
    """Ask the endpoint. Blocking — callers run it off the event loop.

    ``opener`` is the injection seam for tests; it defaults to
    ``urllib.request.urlopen``.
    """
    request = urllib.request.Request(URL, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": BETA,
    })
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        kind = "unauthorized" if exc.code in (401, 403) else "unreachable"
        raise QuotaError(kind) from exc
    except Exception as exc:  # noqa: BLE001 — every other failure is the same
        raise QuotaError("unreachable") from exc
    if not isinstance(payload, dict):
        raise QuotaError("unreachable")
    return parse_quota(
        payload, now=time.monotonic() if now is None else now)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_quota.py -q`
Expected: PASS, 14 passed

- [x] **Step 5: Commit**

```bash
git commit -m "feat(usage): read and parse live Claude quota" -- src/aegis/usage/quota.py tests/test_quota.py
```

---

### Task 5: The polling service

**Files:**
- Modify: `src/aegis/usage/quota.py` (append)
- Test: `tests/test_quota_service.py`

**Interfaces:**
- Consumes: `fetch_quota`, `read_token`, `QuotaError`, `QuotaSnapshot` from Task 4.
- Produces: `QuotaState(snapshot: QuotaSnapshot | None, age_s: float, failure: str)`; `QuotaService(clock=time.monotonic, fetch=fetch_quota, token_reader=read_token)` with `start()`, `async stop()`, `async refresh(*, force=False, min_interval=POLL_S)`, `current() -> QuotaState`, and a `started` property. Constants `POLL_S = 60.0`, `STALE_DROP_S = 300.0`.

`QuotaState` is a flat record rather than a union: `snapshot and not failure` is fresh, `snapshot and failure` is stale, `not snapshot` is failed. Renderers branch on those two fields directly.

- [x] **Step 1: Write the failing tests**

Create `tests/test_quota_service.py`:

```python
import pytest

from aegis.usage.quota import (
    POLL_S, STALE_DROP_S, QuotaError, QuotaService, QuotaSnapshot, QuotaWindow,
)


class Clock:
    def __init__(self):
        self.t = 1000.0
    def __call__(self):
        return self.t
    def advance(self, dt):
        self.t += dt


def _snap(now, pct=64.0):
    return QuotaSnapshot(
        windows=(QuotaWindow("session", pct, "normal", None, True),),
        fetched_at=now)


def _service(clock, results):
    """`results` is a list of snapshot-or-exception, consumed per call."""
    calls = []

    def fetch(token, **kw):
        calls.append(token)
        item = results.pop(0) if results else _snap(clock())
        if isinstance(item, Exception):
            raise item
        return item

    svc = QuotaService(clock=clock, fetch=fetch,
                       token_reader=lambda path=None: "tok")
    svc._calls = calls
    return svc


@pytest.mark.asyncio
async def test_first_refresh_fetches_and_reports_fresh():
    c = Clock()
    svc = _service(c, [_snap(c())])
    await svc.refresh()
    state = svc.current()
    assert state.failure == ""
    assert state.snapshot.window("session").percent == 64.0
    assert state.age_s == 0.0


@pytest.mark.asyncio
async def test_refresh_respects_the_floor():
    c = Clock()
    svc = _service(c, [_snap(c()), _snap(c(), 70.0)])
    await svc.refresh()
    c.advance(POLL_S - 1)
    await svc.refresh()
    assert len(svc._calls) == 1


@pytest.mark.asyncio
async def test_refresh_fetches_again_past_the_floor():
    c = Clock()
    svc = _service(c, [_snap(1000.0), _snap(1000.0 + POLL_S, 70.0)])
    await svc.refresh()
    c.advance(POLL_S + 1)
    await svc.refresh()
    assert len(svc._calls) == 2
    assert svc.current().snapshot.window("session").percent == 70.0


@pytest.mark.asyncio
async def test_force_bypasses_the_floor():
    c = Clock()
    svc = _service(c, [_snap(c()), _snap(c(), 70.0)])
    await svc.refresh()
    await svc.refresh(force=True)
    assert len(svc._calls) == 2


@pytest.mark.asyncio
async def test_custom_min_interval_allows_an_earlier_refetch():
    c = Clock()
    svc = _service(c, [_snap(c()), _snap(c(), 70.0)])
    await svc.refresh()
    c.advance(11)
    await svc.refresh(min_interval=10.0)
    assert len(svc._calls) == 2


@pytest.mark.asyncio
async def test_missing_credentials_reports_no_credentials():
    c = Clock()
    svc = QuotaService(clock=c, fetch=lambda *a, **k: _snap(c()),
                       token_reader=lambda path=None: None)
    await svc.refresh()
    assert svc.current().failure == "no_credentials"
    assert svc.current().snapshot is None


@pytest.mark.asyncio
async def test_failure_after_success_goes_stale_and_keeps_the_value():
    c = Clock()
    svc = _service(c, [_snap(1000.0), QuotaError("unreachable")])
    await svc.refresh()
    c.advance(POLL_S + 1)
    await svc.refresh()
    state = svc.current()
    assert state.failure == "unreachable"
    assert state.snapshot is not None
    assert state.age_s == pytest.approx(POLL_S + 1)


@pytest.mark.asyncio
async def test_sustained_failure_eventually_drops_the_snapshot():
    c = Clock()
    svc = _service(c, [_snap(1000.0)] + [QuotaError("unreachable")] * 10)
    await svc.refresh()
    for _ in range(9):
        c.advance(POLL_S + 1)
        await svc.refresh()
    state = svc.current()
    assert state.snapshot is None
    assert state.failure == "unreachable"


@pytest.mark.asyncio
async def test_success_clears_a_previous_failure():
    c = Clock()
    svc = _service(c, [_snap(1000.0), QuotaError("unreachable"),
                       _snap(1000.0 + 2 * POLL_S, 70.0)])
    await svc.refresh()
    c.advance(POLL_S + 1)
    await svc.refresh()
    c.advance(POLL_S + 1)
    await svc.refresh()
    assert svc.current().failure == ""
    assert svc.current().snapshot.window("session").percent == 70.0


@pytest.mark.asyncio
async def test_unauthorized_is_reported_distinctly():
    c = Clock()
    svc = _service(c, [QuotaError("unauthorized")])
    await svc.refresh()
    assert svc.current().failure == "unauthorized"


@pytest.mark.asyncio
async def test_start_is_idempotent_and_stop_is_safe():
    c = Clock()
    svc = _service(c, [])
    svc.start()
    first = svc._task
    svc.start()
    assert svc._task is first
    await svc.stop()
    assert svc._task is None
    await svc.stop()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_quota_service.py -q`
Expected: FAIL — `ImportError: cannot import name 'QuotaService'`

- [x] **Step 3: Append the service to `src/aegis/usage/quota.py`**

```python
POLL_S = 60.0          # background cadence
STALE_DROP_S = 300.0   # how long a stale value stays on screen before it goes


@dataclass(frozen=True)
class QuotaState:
    """What the bar should show right now.

    ``snapshot`` and no ``failure`` is a fresh reading; ``snapshot`` with a
    ``failure`` is the last good reading while fetches are failing; no
    ``snapshot`` means there is nothing trustworthy to show and ``failure``
    says why.
    """
    snapshot: QuotaSnapshot | None = None
    age_s: float = 0.0
    failure: str = ""   # "" | "no_credentials" | "unauthorized" | "unreachable"


class QuotaService:
    """Polls the quota endpoint on a cadence and caches the answer.

    ``current()`` is synchronous so the 1-second UI tick never touches the
    network. Fetches run in a worker thread; the endpoint is blocking stdlib
    code and must not stall the event loop.
    """

    def __init__(self, *, clock=time.monotonic, fetch=None,
                 token_reader=None) -> None:
        self._clock = clock
        self._fetch = fetch or fetch_quota
        self._read_token = token_reader or read_token
        self._snapshot: QuotaSnapshot | None = None
        self._failure = ""
        self._failing_since = 0.0
        self._last_attempt = 0.0
        self._task = None

    @property
    def started(self) -> bool:
        return self._task is not None

    def start(self) -> None:
        """Begin polling. Idempotent — safe to call from every UI tick."""
        if self._task is not None:
            return
        import asyncio
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        import asyncio
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    async def _loop(self) -> None:
        import asyncio
        while True:
            await self.refresh()
            await asyncio.sleep(POLL_S)

    async def refresh(self, *, force: bool = False,
                      min_interval: float | None = None) -> None:
        """Fetch unless we fetched recently.

        ``min_interval`` overrides the default floor — the turn-end trigger
        passes a shorter one so a finished turn updates the number promptly
        without letting a burst of short turns hammer an undocumented endpoint.
        """
        now = self._clock()
        floor = POLL_S if min_interval is None else min_interval
        if not force and self._last_attempt and now - self._last_attempt < floor:
            return
        self._last_attempt = now

        token = self._read_token()
        if not token:
            self._snapshot = None
            self._failure = "no_credentials"
            self._failing_since = 0.0
            return

        try:
            import asyncio
            snapshot = await asyncio.to_thread(self._fetch, token)
        except QuotaError as exc:
            self._note_failure(exc.kind, now)
            return
        except Exception:  # noqa: BLE001 — a fetch must never break the caller
            self._note_failure("unreachable", now)
            return
        self._snapshot = snapshot
        self._failure = ""
        self._failing_since = 0.0

    def _note_failure(self, kind: str, now: float) -> None:
        self._failure = kind
        if self._snapshot is None:
            return
        if not self._failing_since:
            self._failing_since = now
        elif now - self._failing_since >= STALE_DROP_S:
            self._snapshot = None

    def current(self) -> QuotaState:
        age = 0.0
        if self._snapshot is not None:
            age = max(0.0, self._clock() - self._snapshot.fetched_at)
        return QuotaState(
            snapshot=self._snapshot, age_s=age, failure=self._failure)
```

Note the `asyncio.to_thread(self._fetch, token)` call: tests inject a
synchronous fake, which `to_thread` runs happily, so no test needs a real
thread pool distinction.

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_quota_service.py -q`
Expected: PASS, 11 passed

- [x] **Step 5: Commit**

```bash
git commit -m "feat(usage): QuotaService — cached polling with stale handling" -- src/aegis/usage/quota.py tests/test_quota_service.py
```

---

### Task 6: Rendering the segment

**Files:**
- Modify: `src/aegis/usage/quota.py` (append)
- Test: `tests/test_quota_render.py`

**Interfaces:**
- Consumes: `QuotaState`, `QuotaSnapshot`, `QuotaWindow` from Tasks 4-5.
- Produces: `format_quota_tiers(state: QuotaState, colors, *, now: datetime | None = None) -> tuple[str, ...]` — empty tuple when there is nothing to say.

- [x] **Step 1: Write the failing tests**

Create `tests/test_quota_render.py`:

```python
from datetime import datetime, timedelta, timezone

from aegis.themes import AegisColors
from aegis.tui.fit import plain_width, strip_markup
from aegis.usage.quota import (
    QuotaSnapshot, QuotaState, QuotaWindow, format_quota_tiers,
)

COLORS = AegisColors(
    ready="green", working="yellow", error="red", accent="blue",
    muted="grey50", ok="green", err="red", user="blue", user_bg="black")

NOW = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)


def _state(session_pct=64.0, weekly_pct=7.0, severity="normal",
           failure="", age=0.0, resets_in=timedelta(hours=3, minutes=30)):
    snap = QuotaSnapshot(windows=(
        QuotaWindow("session", session_pct, severity, NOW + resets_in, True),
        QuotaWindow("weekly_all", weekly_pct, "normal",
                    NOW + timedelta(days=7), False),
    ), fetched_at=0.0)
    return QuotaState(snapshot=snap, age_s=age, failure=failure)


def _plain(tiers):
    return [strip_markup(t) for t in tiers]


def test_calm_shows_both_windows_without_a_countdown():
    tiers = format_quota_tiers(_state(), COLORS, now=NOW)
    assert _plain(tiers)[0] == "⧗ 5h 64% · wk 7%"


def test_warning_grows_a_countdown_on_that_window_only():
    tiers = format_quota_tiers(
        _state(session_pct=87.0, severity="warning"), COLORS, now=NOW)
    assert _plain(tiers)[0] == "⧗ 5h 87% ⟶3h30m · wk 7%"


def test_warning_uses_the_working_colour():
    tiers = format_quota_tiers(
        _state(session_pct=87.0, severity="warning"), COLORS, now=NOW)
    assert "[yellow]" in tiers[0]


def test_critical_uses_the_error_colour():
    tiers = format_quota_tiers(
        _state(session_pct=97.0, severity="critical"), COLORS, now=NOW)
    assert "[red]" in tiers[0]


def test_short_tier_is_narrower():
    tiers = format_quota_tiers(_state(), COLORS, now=NOW)
    assert plain_width(tiers[1]) < plain_width(tiers[0])
    assert _plain(tiers)[1] == "⧗ 64/7%"


def test_stale_keeps_the_numbers_and_shows_their_age():
    tiers = format_quota_tiers(
        _state(failure="unreachable", age=125.0), COLORS, now=NOW)
    assert _plain(tiers)[0] == "⧗ 5h 64% · wk 7% (2m old)"
    assert "[grey50]" in tiers[0]


def test_no_credentials_says_so():
    state = QuotaState(snapshot=None, failure="no_credentials")
    assert _plain(format_quota_tiers(state, COLORS, now=NOW))[0] == (
        "⧗ quota — no credentials")


def test_unauthorized_says_auth_expired():
    state = QuotaState(snapshot=None, failure="unauthorized")
    assert _plain(format_quota_tiers(state, COLORS, now=NOW))[0] == (
        "⧗ quota — auth expired")


def test_unreachable_says_unreachable():
    state = QuotaState(snapshot=None, failure="unreachable")
    assert _plain(format_quota_tiers(state, COLORS, now=NOW))[0] == (
        "⧗ quota — unreachable")


def test_nothing_known_yet_renders_nothing():
    assert format_quota_tiers(QuotaState(), COLORS, now=NOW) == ()


def test_missing_reset_timestamp_omits_the_countdown():
    snap = QuotaSnapshot(windows=(
        QuotaWindow("session", 91.0, "warning", None, True),), fetched_at=0.0)
    tiers = format_quota_tiers(QuotaState(snapshot=snap), COLORS, now=NOW)
    assert _plain(tiers)[0] == "⧗ 5h 91%"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_quota_render.py -q`
Expected: FAIL — `ImportError: cannot import name 'format_quota_tiers'`

- [x] **Step 3: Append the renderer to `src/aegis/usage/quota.py`**

```python
# Window kinds worth a place on a one-line status bar, in display order.
_BAR_WINDOWS = (("session", "5h"), ("weekly_all", "wk"))

_FAILURE_TEXT = {
    "no_credentials": "no credentials",
    "unauthorized": "auth expired",
    "unreachable": "unreachable",
}


def _countdown(target: datetime, now: datetime) -> str:
    """``2h14m`` / ``12m`` / ``now`` — how long until the window resets."""
    seconds = int((target - now).total_seconds())
    if seconds <= 0:
        return "now"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 86400}d{(seconds % 86400) // 3600}h"


def _age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    return f"{int(seconds // 3600)}h"


def format_quota_tiers(state: QuotaState, colors,
                       *, now: datetime | None = None) -> tuple[str, ...]:
    """Render the quota segment, widest form first.

    An empty tuple means "say nothing" — there is no reading and no failure to
    report, which is only true before the first poll completes.
    """
    if state.snapshot is None:
        if not state.failure:
            return ()
        text = _FAILURE_TEXT.get(state.failure, "unreachable")
        return (f"[{colors.muted}]⧗ quota — {text}[/]",)

    moment = now or datetime.now(timezone.utc)
    stale = bool(state.failure)
    parts: list[str] = []
    shorts: list[str] = []
    for kind, label in _BAR_WINDOWS:
        window = state.snapshot.window(kind)
        if window is None:
            continue
        value = f"{window.percent:.0f}%"
        shorts.append(f"{window.percent:.0f}")
        if not stale and window.severity == "warning":
            value = f"[{colors.working}]{value}[/]"
        elif not stale and window.severity == "critical":
            value = f"[{colors.error}]{value}[/]"
        chunk = f"{label} {value}"
        # The reset time is only a question once the number is high.
        if window.severity != "normal" and window.resets_at is not None:
            chunk += f" ⟶{_countdown(window.resets_at, moment)}"
        parts.append(chunk)

    if not parts:
        return ()

    full = "⧗ " + " · ".join(parts)
    short = "⧗ " + "/".join(shorts) + "%"
    if stale:
        full = f"[{colors.muted}]{full} ({_age(state.age_s)} old)[/]"
        short = f"[{colors.muted}]{short}[/]"
    return (full, short)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_quota_render.py -q`
Expected: PASS, 11 passed

- [x] **Step 5: Commit**

```bash
git commit -m "feat(usage): render the quota segment" -- src/aegis/usage/quota.py tests/test_quota_render.py
```

---

### Task 7: Wire it into the app

**Files:**
- Modify: `src/aegis/tui/app.py:275-290` (construct), `:649-660` (`_tick`), `:950-975` (`action_quit`)
- Test: `tests/test_quota_visibility.py`

**Interfaces:**
- Consumes: `QuotaService`, `format_quota_tiers` from Tasks 5-6; `ConversationPane.set_quota` from Task 3.
- Produces: `AegisApp.quota_service`; `AegisApp._quota_enabled() -> bool`; `AegisApp._quota_tick(active) -> None`.

Two behaviours decided here that the spec leaves to implementation:

- **Remote mode is excluded.** Under `aegis --remote` the Claude process runs on the daemon host and burns *that* host's quota, so the local credentials would report the wrong account. `_quota_enabled()` returns False when `_remote_manager` is present, matching the existing "remote mode: skip local planes" rule at `app.py:355`.
- **Turn-end refresh is detected in `_tick` by diffing pane states** rather than by registering a state observer at each of the four `ConversationPane(` construction sites (`app.py:453,560,1072,1289`). One place instead of four, no pane API change, and at a 1-second tick the detection delay is irrelevant against a 60-second poll. The turn-end refresh passes `min_interval=10.0` so a burst of short turns cannot hammer the endpoint.

- [x] **Step 1: Write the failing tests**

Create `tests/test_quota_visibility.py`:

```python
import pytest

from aegis.tui.app import AegisApp


class FakeAgent:
    def __init__(self, harness):
        self.harness = harness


class FakePane:
    """Stands in for a ConversationPane — only the fields the predicate reads."""
    def __init__(self, harness):
        self._agent = FakeAgent(harness)
        self.handle = f"pane-{harness}"
        self.quota_tiers = None

    def set_quota(self, tiers):
        self.quota_tiers = tiers


def _app(panes, remote=False):
    app = AegisApp.__new__(AegisApp)      # no Textual boot in a unit test
    app._panes = panes
    if remote:
        app._remote_manager = object()
    return app


def test_no_panes_means_no_quota():
    assert _app([])._quota_enabled() is False


def test_a_claude_pane_enables_quota():
    assert _app([FakePane("claude-code")])._quota_enabled() is True


def test_non_claude_panes_do_not_enable_quota():
    panes = [FakePane("gemini"), FakePane("lovelaice")]
    assert _app(panes)._quota_enabled() is False


def test_one_claude_pane_among_others_enables_quota():
    panes = [FakePane("gemini"), FakePane("claude-code")]
    assert _app(panes)._quota_enabled() is True


def test_remote_mode_never_enables_quota():
    app = _app([FakePane("claude-code")], remote=True)
    assert app._quota_enabled() is False


def test_panes_without_an_agent_are_ignored():
    class Bare:
        pass
    assert _app([Bare()])._quota_enabled() is False
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_quota_visibility.py -q`
Expected: FAIL — `AttributeError: 'AegisApp' object has no attribute '_quota_enabled'`

- [x] **Step 3: Construct the service**

In `src/aegis/tui/app.py`, after `self.loop_service = LoopService(self)` (line 288), add:

```python
        # Quota plane — live Claude subscription utilisation for the status
        # bar. Constructed always, started lazily: a session with no Claude
        # agent must make no network calls at all.
        from aegis.usage.quota import QuotaService
        self.quota_service = QuotaService()
        # handle -> last seen AgentState, for turn-end detection in _tick.
        self._quota_states: dict[str, object] = {}
```

- [x] **Step 4: Add the predicate and the tick**

In `src/aegis/tui/app.py`, add these two methods immediately before `_tick`
(line 649):

```python
    def _quota_enabled(self) -> bool:
        """True when a Claude agent is open locally.

        The quota is an account property, so any open Claude pane justifies the
        segment — including a background worker burning the window while you
        sit in a terminal tab, which is exactly when you want to see it. Remote
        mode is excluded: the agent runs on the daemon host and spends that
        host's quota, not this one's.
        """
        if hasattr(self, "_remote_manager"):
            return False
        return any(
            getattr(getattr(p, "_agent", None), "harness", "") == "claude-code"
            for p in self._panes)

    def _quota_tick(self, active) -> None:
        """Push the quota segment and refresh on any turn that just ended."""
        from aegis.tui.state import AgentState
        from aegis.usage.quota import format_quota_tiers

        if not self._quota_enabled():
            if active is not None and hasattr(active, "set_quota"):
                active.set_quota(())
            return
        self.quota_service.start()

        # Turn-end detection: any Claude pane going working -> ready means the
        # number just moved, so ask again ahead of the 60s cadence.
        for pane in self._panes:
            agent = getattr(pane, "_agent", None)
            if getattr(agent, "harness", "") != "claude-code":
                continue
            handle = getattr(pane, "handle", None)
            state = getattr(getattr(pane, "_core", None), "state", None)
            if handle is None:
                continue
            previous = self._quota_states.get(handle)
            self._quota_states[handle] = state
            if previous is AgentState.working and state is AgentState.ready:
                self.run_worker(
                    self.quota_service.refresh(min_interval=10.0),
                    exclusive=False)

        if active is not None and hasattr(active, "set_quota"):
            active.set_quota(
                format_quota_tiers(self.quota_service.current(), self._palette))
```

Then call it from `_tick`, after the sysmeter block:

```python
        import contextlib
        with contextlib.suppress(Exception):
            self._quota_tick(active)
```

- [x] **Step 5: Stop the service on quit**

In `action_quit`, beside `self.queue_digest.stop()`, add:

```python
        await self.quota_service.stop()
```

- [x] **Step 6: Run the tests**

Run: `uv run python -m pytest tests/test_quota_visibility.py -q`
Expected: PASS, 6 passed

- [x] **Step 7: Run the wider suite**

Run: `uv run python -m pytest tests/ -q --ignore=tests/tui`
Expected: PASS. Check the exit code; do not pipe this into `tail`.

- [x] **Step 8: Commit**

```bash
git commit -m "feat(tui): live Claude quota in the status bar" -- src/aegis/tui/app.py tests/test_quota_visibility.py
```

---

### Task 8: `/usage quota`

**Files:**
- Modify: `src/aegis/commands/builtins/usage.py`
- Test: `tests/test_usage_quota_command.py`

**Interfaces:**
- Consumes: `QuotaService`, `QuotaSnapshot`, `format_quota_tiers` from Tasks 4-6.
- Produces: `quota` in `_VIEWS`; `quota_lines(state: QuotaState, *, now: datetime | None = None) -> list[str]` exported from `aegis.usage.quota`; `_quota_service(ctx)` in the command module (the test's monkeypatch seam).

The command shows every window in `limits[]`, not just the two the bar has room for.

- [x] **Step 1: Write the failing tests**

Create `tests/test_usage_quota_command.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from aegis.usage.quota import (
    QuotaSnapshot, QuotaState, QuotaWindow, quota_lines,
)

NOW = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)


def _state():
    snap = QuotaSnapshot(windows=(
        QuotaWindow("session", 64.0, "normal",
                    NOW + timedelta(hours=3, minutes=30), True),
        QuotaWindow("weekly_all", 7.0, "normal",
                    NOW + timedelta(days=7), False),
        QuotaWindow("weekly_opus", 88.0, "warning",
                    NOW + timedelta(days=7), False),
    ), fetched_at=0.0)
    return QuotaState(snapshot=snap, age_s=12.0)


def test_lines_cover_every_window_not_just_the_bar_pair():
    text = "\n".join(quota_lines(_state(), now=NOW))
    assert "session" in text
    assert "weekly_all" in text
    assert "weekly_opus" in text


def test_lines_carry_percent_severity_and_countdown():
    text = "\n".join(quota_lines(_state(), now=NOW))
    assert "64%" in text
    assert "warning" in text
    assert "3h30m" in text


def test_lines_report_the_age_of_the_reading():
    assert any("12s" in ln for ln in quota_lines(_state(), now=NOW))


def test_failure_state_explains_itself():
    state = QuotaState(snapshot=None, failure="no_credentials")
    text = "\n".join(quota_lines(state, now=NOW))
    assert "no credentials" in text


@pytest.mark.asyncio
async def test_command_registers_and_returns_lines(monkeypatch):
    from aegis.commands import CommandContext, dispatch
    from aegis.commands.builtins import usage as usage_cmd

    class FakeService:
        refreshed = False
        async def refresh(self, **kw):
            self.refreshed = True
        def current(self):
            return _state()

    fake = FakeService()
    monkeypatch.setattr(usage_cmd, "_quota_service", lambda ctx: fake)
    result = await dispatch("/usage quota", CommandContext(None, "h"))
    assert result.ok
    assert fake.refreshed is True
    assert "session" in result.detail
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_usage_quota_command.py -q`
Expected: FAIL — `ImportError: cannot import name 'quota_lines'`

- [x] **Step 3: Append `quota_lines` to `src/aegis/usage/quota.py`**

```python
def quota_lines(state: QuotaState, *,
                now: datetime | None = None) -> list[str]:
    """Full breakdown for ``/usage quota`` — every window, not just the pair
    the status bar has room for."""
    moment = now or datetime.now(timezone.utc)
    if state.snapshot is None:
        text = _FAILURE_TEXT.get(state.failure or "unreachable", "unreachable")
        return [f"quota unavailable — {text}"]

    lines: list[str] = []
    for window in state.snapshot.windows:
        resets = "—"
        if window.resets_at is not None:
            resets = (f"{window.resets_at.astimezone().strftime('%Y-%m-%d %H:%M')}"
                      f" ({_countdown(window.resets_at, moment)})")
        active = "active" if window.is_active else "idle"
        lines.append(
            f"{window.kind:<14} {window.percent:>5.0f}%  "
            f"{window.severity:<8} {active:<6} resets {resets}")

    footer = f"read {_age(state.age_s)} ago"
    if state.failure:
        footer += f" — fetch failing ({state.failure})"
    lines.append("")
    lines.append(footer)
    return lines
```

- [x] **Step 4: Add the view to the command**

In `src/aegis/commands/builtins/usage.py`, extend `_VIEWS` and add the branch.
Replace the `_VIEWS` line:

```python
_VIEWS = ("dashboard", "tools", "sessions", "month", "dow", "hour", "quota")
```

Add a module-level service accessor (the monkeypatch seam the test uses) after
the imports:

```python
_SERVICE = None


def _quota_service(ctx):
    """The app's QuotaService when there is one, else a private instance.

    ``/usage quota`` must work headlessly (web client, `aegis serve`) where no
    TUI app owns a service, so fall back to a module-level one.
    """
    global _SERVICE
    service = getattr(getattr(ctx, "bridge", None), "quota_service", None)
    if service is not None:
        return service
    if _SERVICE is None:
        from aegis.usage.quota import QuotaService
        _SERVICE = QuotaService()
    return _SERVICE
```

And inside `_usage`, before the `dashboard` branch:

```python
    if view == "quota":
        from aegis.usage.quota import quota_lines
        service = _quota_service(ctx)
        await service.refresh(force=True)
        state = service.current()
        lines = quota_lines(state)
        title = "usage · quota"
        if state.snapshot is not None:
            session = state.snapshot.window("session")
            if session is not None:
                title = f"usage · quota · 5h {session.percent:.0f}%"
        return CommandResult(True, title, "\n".join(lines))
```

Note this branch returns before `build_report` runs, so `/usage quota` works
even with no session logs on disk.

- [x] **Step 5: Run the tests**

Run: `uv run python -m pytest tests/test_usage_quota_command.py -q`
Expected: PASS, 5 passed

- [x] **Step 6: Check the command surface still resolves**

Run: `uv run python -m pytest tests/ -q -k "command or palette or usage"`
Expected: PASS — no collision, and the palette still completes `/usage`.

- [x] **Step 7: Commit**

```bash
git commit -m "feat(usage): /usage quota — full subscription breakdown" -- src/aegis/usage/quota.py src/aegis/commands/builtins/usage.py tests/test_usage_quota_command.py
```

---

### Task 9: Docs

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md`

- [x] **Step 1: Add the CHANGELOG entry**

Under `## [Unreleased]`, above the existing "Interrupts stop being destructive"
section:

```markdown
### Live Claude quota in the status bar

- **New: the status bar shows how much of your Claude subscription is spent.**
  `⧗ 5h 64% · wk 7%` sits beside the metrics whenever a Claude agent is open —
  the quota is an account property, so a background worker burning the window
  while you sit in another tab is exactly the case this catches. No Claude
  agent open means no segment and no network call at all.
- Past 80% the window that is running out grows a reset countdown
  (`⧗ 5h 87% ⟶2h14m`), because "when does it reset" is only a question once
  the number is high. Amber and red follow the API's own severity rather than
  a threshold invented here.
- A failing fetch says so rather than going quiet: `⧗ quota — auth expired`,
  `— no credentials`, `— unreachable`. A single failed poll keeps the last
  numbers, dimmed, with their age; only sustained failure drops them.
- **New: `/usage quota`** prints every window the API reports — percent,
  severity, reset time and countdown — forcing a fresh read. Works in the web
  client too.
- Remote mode (`aegis --remote`) shows no quota: the agent runs on the daemon
  host and spends that host's account, not yours.

### The status bar fits the terminal

- **The bar no longer clips.** It was composing ~226 columns unconditionally
  and letting Textual cut whatever fell off the right. Segments now carry
  progressively narrower forms and a priority, and the bar degrades from the
  bottom until it fits — dropping what never changes (build string, model name,
  system stats) before what does (state, loop, quota, tokens and cost).
- Metrics narrows in four stages, shedding the tool counter and throughput
  first, then the cached and reasoning shares, keeping tokens, cost and turn
  time to the end. `ctx 88.2K (44%)` becomes `ctx 44%` when space is tight —
  the percentage is the part you act on.
```

- [x] **Step 2: Document the command in `README.md`**

Find the slash-command list and add, beside the existing `/usage` entry:

```markdown
| `/usage quota` | live Claude subscription utilisation — every window, with reset countdowns |
```

- [x] **Step 3: Commit**

```bash
git commit -m "docs(quota): changelog + README entry for live quota" -- CHANGELOG.md README.md
```

---

## Verification

- [x] `uv run python -m pytest tests/test_quota.py tests/test_quota_service.py tests/test_quota_render.py tests/test_quota_visibility.py tests/test_usage_quota_command.py -q` — all green
- [x] `uv run python -m pytest tests/test_statusbar_fit.py tests/test_statusbar_segments.py tests/test_metrics.py tests/test_sysmeter.py -q` — all green
- [x] `uv run python -m pytest tests/ -q --ignore=tests/tui` — full suite, minus the theme-leaking directory. Check the exit code.
- [x] `uv run python -m pytest tests/tui -q` — TUI suites, run separately
- [x] `uv run ruff check src/ tests/` — only the pre-existing `F821` at `src/aegis/tui/app.py:132`
- [x] Live smoke: start `aegis` with a Claude agent, confirm `⧗ 5h N%` appears in the status bar and matches `bin/claude-usage` in a shell
- [x] Live smoke: narrow the terminal to ~100 then ~70 columns and confirm the bar sheds system stats, then the build string and model, without ever clipping mid-glyph
- [x] Live smoke: `/usage quota` prints every window with countdowns
- [x] Live smoke: open a non-Claude-only session (lovelaice) and confirm no `⧗` segment appears and no request is made
