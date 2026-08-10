# Context Gauge Accuracy and Compaction Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the status-bar `ctx N (P%)` gauge report the real single-sub-turn
context size instead of an accumulated total, and surface Claude's
auto-compaction as an explicit `✂N` counter.

**Architecture:** Two independent fixes sharing one funnel.
`SessionMetrics.commit()` stops using `Result.usage.true_input` (which
accumulates across sub-turns) and keeps the per-sub-turn peak `p_in` it already
tracks. Compaction comes from Claude's `system`/`compact_boundary` stream event
— parsed into a new typed `CompactBoundary` — never from a drop heuristic.
`observe_context(ti)` is the single entry point both the Claude streaming path
and the ACP `ContextUpdate` path feed.

**Tech Stack:** Python 3.13+, `uv`, pytest, Textual 8.x, Rich markup.

**Spec:** `docs/superpowers/specs/2026-08-09-context-gauge-and-compaction-design.md`

## Global Constraints

- Package management is `uv`, never pip: `uv run python -m pytest`.
- TDD: failing test first, minimal implementation, commit per logical unit.
- Run the fast suite as `uv run pytest -q -m "not live"`. **Never** use
  `-k "not live"` — it matches `live` as a substring and eats unrelated names.
- A failing test is a real failure, not a flake. Do not re-roll a red run.
- `core/session.py` has **two** structurally identical event loops (the live
  turn around `:504-522` and the second around `:708-730`). Every routing
  change in this plan must be applied to **both**. A change landed in only one
  is the defining bug of this file.
- Status-bar glyphs must be single-width: `fit.plain_width` measures with
  `len()` after stripping markup. `✂` (U+2702) is EAW=Neutral, `cell_len` 1 —
  verified safe. Do not substitute an emoji-presentation variant.
- Rich markup embedded in status-bar tier strings is the established pattern —
  `StatusBar` is constructed with `markup=True` and `fit.plain_width` strips
  tags before measuring, so markup does not consume width budget.
- Cumulative accounting (`c_in`, `c_out`, cost) must not change. Accumulation
  is correct there; only the gauge numerator is wrong.

---

### Task 1: Parse `compact_boundary` into a typed event

**Files:**
- Modify: `src/aegis/events.py` (dataclasses near `:156`, `_classify_event`
  near `:423`, `Event` union at `:234`)
- Test: `tests/test_events.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `CompactBoundary(trigger: str, pre_tokens: int, post_tokens: int,
  dropped_tokens: int = 0, duration_ms: int = 0)`, importable from
  `aegis.events`, returned by `parse()` for `system`/`compact_boundary` lines.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_events.py`:

```python
def test_parse_compact_boundary_event():
    """Claude emits an explicit boundary at every auto-compaction. The
    payload is authoritative -- pre/post token counts come from the
    harness, so nothing here is inferred from a drop heuristic."""
    ev = parse(json.dumps({
        "type": "system", "subtype": "compact_boundary",
        "session_id": "8ac5c9fe", "uuid": "7b0f20f8",
        "compact_metadata": {
            "trigger": "auto",
            "pre_tokens": 999917,
            "post_tokens": 15022,
            "cumulative_dropped_tokens": 984895,
            "duration_ms": 163448,
        },
    }))
    assert isinstance(ev, CompactBoundary)
    assert ev.trigger == "auto"
    assert ev.pre_tokens == 999917
    assert ev.post_tokens == 15022
    assert ev.dropped_tokens == 984895
    assert ev.duration_ms == 163448


def test_parse_compact_boundary_tolerates_missing_metadata():
    """A boundary with no metadata block must still parse -- the counter
    is worth having even when the numbers are absent."""
    ev = parse(json.dumps({"type": "system", "subtype": "compact_boundary"}))
    assert isinstance(ev, CompactBoundary)
    assert ev.trigger == ""
    assert ev.pre_tokens == 0
    assert ev.post_tokens == 0
```

Add `CompactBoundary` to the existing `from aegis.events import (...)` block at
the top of the file.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_events.py -k compact_boundary -v`
Expected: FAIL with `ImportError: cannot import name 'CompactBoundary'`

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/events.py`, add the dataclass next to `ContextUpdate` (`:156`):

```python
@dataclass
class CompactBoundary:
    """Claude compacted the conversation. Authoritative: the harness has
    already decided, so nothing downstream infers compaction from token
    movement. `trigger` is "auto" or "manual"."""
    trigger: str = ""
    pre_tokens: int = 0
    post_tokens: int = 0
    dropped_tokens: int = 0
    duration_ms: int = 0
```

Add to the `Event` union at `:234` (append `| CompactBoundary`).

In `_classify_event`, directly after the `thinking_tokens` branch (`:423-427`):

```python
    if etype == "system" and obj.get("subtype") == "compact_boundary":
        meta = obj.get("compact_metadata")
        if not isinstance(meta, dict):
            meta = {}
        return CompactBoundary(
            trigger=meta.get("trigger") or "",
            pre_tokens=int(meta.get("pre_tokens") or 0),
            post_tokens=int(meta.get("post_tokens") or 0),
            dropped_tokens=int(meta.get("cumulative_dropped_tokens") or 0),
            duration_ms=int(meta.get("duration_ms") or 0),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_events.py -q`
Expected: PASS, including the pre-existing `test_unknown_never_raises`.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/events.py tests/test_events.py
git commit -m "feat(events): parse system/compact_boundary into a typed event"
```

---

### Task 2: Stop the gauge reading the accumulated total

**Files:**
- Modify: `src/aegis/tui/metrics.py:152-167` (`commit`), `:133-138` (`observe`)
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `SessionMetrics.observe_context(ti: int) -> None` — the shared
  funnel every harness's context-size snapshot goes through. `commit()` now
  sets `last_true_input` from the per-sub-turn peak.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_metrics.py`:

```python
def test_commit_keeps_per_subturn_peak_not_the_accumulated_total():
    """Result.usage.true_input accumulates across every sub-turn of an
    agentic turn, so it is not a context size. The gauge must keep the
    largest single sub-turn instead.

    Numbers are a scaled-down real turn: three sub-turns at 97k/113k/123k
    commit a Result of 333k. Measured over 6871 real turns, using the
    Result value put 61.9% of them above 100% of the window."""
    m = SessionMetrics(context_window=200_000)
    for ti in (97_000, 113_000, 123_000):
        m.observe(_u(inp=ti))
    m.commit(_u(inp=333_000, out=500), now=1.0)

    assert m.last_true_input == 123_000     # the peak sub-turn
    assert m.c_in == 333_000                # cumulative accounting unchanged


def test_commit_falls_back_to_result_when_no_streaming_usage():
    """A turn with no streamed usage (short turn, or a harness that does
    not stream it) still has to show something."""
    m = SessionMetrics(context_window=200_000)
    m.commit(_u(inp=42_000, out=100), now=1.0)
    assert m.last_true_input == 42_000


def test_observe_context_is_the_shared_funnel():
    """Both the Claude streaming path and the ACP ContextUpdate path feed
    one method, so compaction and the gauge behave identically per harness."""
    m = SessionMetrics(context_window=200_000)
    m.observe_context(50_000)
    m.observe_context(30_000)      # a smaller snapshot never lowers the peak
    assert m.p_in == 50_000
    m.observe_context(80_000)
    assert m.p_in == 80_000


def test_gauge_stays_under_100_percent_on_a_long_agentic_turn():
    """Regression for the 1000%+ bug. A 30-sub-turn turn averaging 70k
    commits a ~2.1M Result; the gauge must still read the peak sub-turn."""
    m = SessionMetrics(context_window=200_000)
    total = 0
    for i in range(30):
        ti = 60_000 + i * 700
        total += ti
        m.observe(_u(inp=ti))
    m.commit(_u(inp=total, out=1000), now=1.0)
    pct = round(100 * m.last_true_input / m.context_window)
    assert pct <= 100, f"gauge read {pct}%"
    assert m.last_true_input == 60_000 + 29 * 700
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_metrics.py -k "peak or funnel or agentic or fallback" -v`
Expected: FAIL — `test_commit_keeps_per_subturn_peak_not_the_accumulated_total`
asserts `123000 == 333000`, and `observe_context` does not exist.

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/tui/metrics.py`, replace `observe` (`:133-138`) with:

```python
    def observe_context(self, ti: int) -> None:
        """Record one sub-turn context-size snapshot, from any harness.

        The stream interleaves independent contexts (the main agent's and
        each subagent's), so this is a max over all of them. That is safe
        because a subagent's context is smaller than its parent's -- an
        empirical property of the corpus, not a structural guarantee. The
        signal that it has broken is a gauge that falls while the session
        is still growing.
        """
        self.p_in = max(self.p_in, ti)

    def observe(self, u: TokenUsage) -> None:
        """A streamed (non-authoritative) usage snapshot — provisional."""
        self.observe_context(u.true_input)
        self.p_out = max(self.p_out, u.output)
        self.p_cached = max(self.p_cached, u.cache_read)
        self._provisional = True
```

In `commit` (`:152`), capture the peak before the reset on `:161`:

```python
    def commit(self, usage: TokenUsage | None, now: float) -> None:
        """Turn end. `usage` (result.usage) is authoritative for cumulative
        accounting; provisional is discarded. `None` (error/no-result)
        commits no tokens."""
        # p_in is the largest single sub-turn context seen this turn — the
        # real context size. Result.usage.true_input sums every sub-turn, so
        # a 30-sub-turn turn reports ~2.1M and the gauge reads 1000%+.
        peak_ti = self.p_in
        if usage is not None:
            self.c_in += usage.true_input
            self.c_out += usage.output
            self.c_cached += usage.cache_read
            self.c_cache_write += usage.cache_creation
            self.last_true_input = peak_ti if peak_ti > 0 else usage.true_input
        self.p_in = self.p_out = self.p_cached = 0
        ...
```

Leave the rest of `commit` (the `_provisional` reset, `_end_time`, the tok/s
feed) exactly as it is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_metrics.py -q`
Expected: PASS, all of them. If a pre-existing test asserted the old
`last_true_input == result.true_input` behaviour, it encoded the bug — update
it to the peak and say so in the commit message.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/metrics.py tests/test_metrics.py
git commit -m "fix(metrics): gauge the peak sub-turn, not the accumulated total"
```

---

### Task 3: Count compactions and re-baseline the gauge

**Files:**
- Modify: `src/aegis/tui/metrics.py` (new field near `:84`, new method after
  `observe_context`)
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `observe_context` from Task 2.
- Produces: `SessionMetrics.compaction_count: int` and
  `SessionMetrics.note_compaction(post_tokens: int) -> None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_metrics.py`:

```python
def test_note_compaction_rebaselines_the_gauge():
    """After compaction the model sees a summary, not the old context.
    p_in still holds the pre-compaction peak (~the full window), so
    without a re-baseline the gauge would read ~100% for the rest of
    the turn."""
    m = SessionMetrics(context_window=1_000_000)
    m.observe(_u(inp=999_917))
    m.note_compaction(post_tokens=15_022)

    assert m.compaction_count == 1
    assert m.p_in == 15_022
    assert m.last_true_input == 15_022


def test_note_compaction_lets_the_gauge_climb_again():
    """The re-baseline is a floor reset, not a lock -- growth after the
    boundary must register normally."""
    m = SessionMetrics(context_window=1_000_000)
    m.observe(_u(inp=999_917))
    m.note_compaction(post_tokens=15_022)
    m.observe(_u(inp=40_000))
    assert m.p_in == 40_000


def test_compaction_count_accumulates_across_turns():
    """The counter is session-scoped: it is a context-integrity signal,
    not a per-turn one, so commit() must not reset it."""
    m = SessionMetrics(context_window=1_000_000)
    m.note_compaction(post_tokens=15_000)
    m.commit(_u(inp=100, out=10), now=1.0)
    m.note_compaction(post_tokens=20_000)
    assert m.compaction_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_metrics.py -k compaction -v`
Expected: FAIL with `AttributeError: 'SessionMetrics' object has no attribute 'note_compaction'`

- [ ] **Step 3: Write minimal implementation**

Add the field to the dataclass beside `last_true_input` (`:84`):

```python
    # Times the harness compacted this session. Session-scoped on purpose:
    # commit() must not reset it.
    compaction_count: int = 0
```

Add the method after `observe_context`:

```python
    def note_compaction(self, post_tokens: int) -> None:
        """An authoritative compaction boundary from the harness.

        There is no threshold and no ratio here — the harness has already
        decided. Detecting compaction from token movement instead was
        measured at ~1.3% precision over the real corpus; see the rejected
        approach in the design spec.
        """
        self.compaction_count += 1
        if post_tokens > 0:
            self.p_in = post_tokens
            self.last_true_input = post_tokens
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_metrics.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/metrics.py tests/test_metrics.py
git commit -m "feat(metrics): count compactions and re-baseline the gauge"
```

---

### Task 4: Route `CompactBoundary` in both session loops

**Files:**
- Modify: `src/aegis/core/session.py` (both event loops — around `:504-522`
  and around `:708-730`)
- Test: `tests/test_core_session.py`

**Interfaces:**
- Consumes: `CompactBoundary` (Task 1), `note_compaction` (Task 3).
- Produces: a session whose `metrics.compaction_count` advances when the
  harness emits a boundary.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_core_session.py`, reusing the module's existing
`FakeSession` helper (do not invent a second one):

```python
@pytest.mark.asyncio
async def test_compact_boundary_advances_the_session_counter():
    """The boundary is the only compaction signal aegis trusts. Both of
    session.py's event loops must route it; a change landed in one loop
    only is the defining bug of this file."""
    evs = [
        AssistantText(text="working",
                      usage=TokenUsage(input=999_917, cache_creation=0,
                                       cache_read=0, output=10)),
        CompactBoundary(trigger="auto", pre_tokens=999_917,
                        post_tokens=15_022),
        AssistantText(text="resumed",
                      usage=TokenUsage(input=20_000, cache_creation=0,
                                       cache_read=0, output=5)),
        Result(duration_ms=10, is_error=False, usage=None),
    ]
    s = AgentSession(FakeSession(evs), agent=None, agent_slug="default",
                     handle="h1")
    await s.send("go")
    await s._task

    assert s.metrics.compaction_count == 1
    # The re-baseline is a floor reset, so post-boundary growth registers:
    # 20k beats the 15,022 baseline, and the pre-compaction 999,917 is gone.
    assert s.metrics.last_true_input == 20_000


@pytest.mark.asyncio
async def test_compact_boundary_without_tokens_still_counts():
    """A boundary carrying no metadata must advance the counter without
    zeroing the gauge."""
    evs = [
        AssistantText(text="a",
                      usage=TokenUsage(input=50_000, cache_creation=0,
                                       cache_read=0, output=1)),
        CompactBoundary(),
        Result(duration_ms=1, is_error=False, usage=None),
    ]
    s = AgentSession(FakeSession(evs), None, "default", "h1")
    await s.send("go")
    await s._task
    assert s.metrics.compaction_count == 1
    assert s.metrics.last_true_input == 50_000
```

Extend the module's import line to
`from aegis.events import AssistantText, CompactBoundary, Result, TokenUsage`.

Both of these drive the live-turn loop. The second loop is not reachable from
this harness, so Step 4's `grep -c` is what gates it — do not skip that step.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_core_session.py -k compact -v`
Expected: FAIL — `compaction_count == 0`

- [ ] **Step 3: Write minimal implementation**

In **both** loops, extend the `elif` chain that already handles `ToolUse` /
`ToolResult` / `ThinkingTokens` (first loop `:506-511`):

```python
                elif isinstance(ev, CompactBoundary):
                    self.metrics.note_compaction(ev.post_tokens)
```

Add `CompactBoundary` to the `from aegis.events import (...)` block at the top
of `session.py`.

Note the structure: the `isinstance(ev, Result)` check below is a *separate*
`if`, and its `else` branch calls `self.metrics.observe(u)` for anything
carrying a `usage` attribute. `CompactBoundary` has no `usage` attribute, so it
falls through that branch harmlessly — no guard needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_core_session.py -q`
Expected: PASS

Then confirm both loops were edited:

Run: `grep -c "CompactBoundary" src/aegis/core/session.py`
Expected: `3` (one import, two routing branches)

- [ ] **Step 5: Commit**

```bash
git add src/aegis/core/session.py tests/test_core_session.py
git commit -m "feat(session): route CompactBoundary to the metrics counter"
```

---

### Task 5: Route ACP `ContextUpdate` to the gauge

**Files:**
- Modify: `src/aegis/core/session.py` (the same two loops)
- Test: `tests/test_core_session.py`

**Interfaces:**
- Consumes: `observe_context` (Task 2), existing `ContextUpdate` /
  `CostUsage` (`events.py:145-168`).
- Produces: ACP sessions (OpenCode, Lovelaice) whose gauge populates and whose
  `context_window` comes from the harness rather than the YAML registry.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_context_update_feeds_the_gauge_and_window():
    """ACP harnesses report context through UsageUpdate -> ContextUpdate.
    context_size is the model's real window, so it overrides the YAML
    registry for these sessions."""
    evs = [
        ContextUpdate(cost=CostUsage(context_used=42_000,
                                     context_size=262_144)),
        Result(duration_ms=1, is_error=False, usage=None),
    ]
    s = AgentSession(FakeSession(evs), None, "default", "h1")
    await s.send("go")
    await s._task

    assert s.metrics.context_window == 262_144
    # commit() moved the peak into last_true_input at Result.
    assert s.metrics.last_true_input == 42_000


@pytest.mark.asyncio
async def test_context_update_without_cost_leaves_the_window_alone():
    """A ContextUpdate with no cost block must not zero the window --
    an unknown window hides the ctx segment entirely."""
    s = AgentSession(
        FakeSession([ContextUpdate(cost=None, mode="build"),
                     Result(duration_ms=1, is_error=False, usage=None)]),
        None, "default", "h1")
    s.metrics.context_window = 200_000
    await s.send("go")
    await s._task
    assert s.metrics.context_window == 200_000
```

Extend the module's imports with `ContextUpdate` and `CostUsage` from
`aegis.events` (field names verified against `events.py:145-168`).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_core_session.py -k context_update -v`
Expected: FAIL — `p_in == 0`

- [ ] **Step 3: Write minimal implementation**

In **both** loops, add to the same `elif` chain:

```python
                elif isinstance(ev, ContextUpdate):
                    if ev.cost:
                        if ev.cost.context_used:
                            self.metrics.observe_context(ev.cost.context_used)
                        if ev.cost.context_size:
                            self.metrics.context_window = ev.cost.context_size
```

Add `ContextUpdate` to the imports if it is not already there.

**Do not add a compaction fallback for ACP.** There is no protocol signal, and
the drop heuristic measured 12% precision at best. ACP sessions get an accurate
gauge and no `✂` segment.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_core_session.py -q`
Expected: PASS

Run: `grep -c "ContextUpdate" src/aegis/core/session.py`
Expected: at least `3` (import + two branches)

- [ ] **Step 5: Commit**

```bash
git add src/aegis/core/session.py tests/test_core_session.py
git commit -m "feat(session): feed ACP ContextUpdate into the context gauge"
```

---

### Task 6: Colour the ctx segment by fullness

**Files:**
- Modify: `src/aegis/tui/metrics.py:201-240` (`render_tiers`)
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `last_true_input` / `p_in` semantics from Task 2.
- Produces: `render_tiers()` still returns exactly **four** strings; the `ctx`
  segment inside them carries Rich markup when ≥50% full.

- [ ] **Step 1: Write the failing test**

```python
def test_ctx_segment_is_uncoloured_below_half():
    m = SessionMetrics(context_window=200_000, last_true_input=40_000)
    t0 = m.render_tiers(now=0.0)[0]
    assert "ctx 40k (20%)" in t0
    assert "[" not in t0.split("ctx")[1].split("·")[0]


def test_ctx_segment_warns_at_half_full():
    m = SessionMetrics(context_window=200_000, last_true_input=120_000)
    t0 = m.render_tiers(now=0.0)[0]
    assert "[$warning]ctx 120k (60%)[/$warning]" in t0


def test_ctx_segment_errors_at_three_quarters():
    m = SessionMetrics(context_window=200_000, last_true_input=160_000)
    t0 = m.render_tiers(now=0.0)[0]
    assert "[$error]ctx 160k (80%)[/$error]" in t0


def test_ctx_colour_does_not_consume_width_budget():
    """fit.plain_width strips markup before measuring, so a coloured
    segment must measure the same as an uncoloured one of equal text."""
    from aegis.tui.fit import plain_width
    hot = SessionMetrics(context_window=200_000, last_true_input=160_000)
    assert plain_width(hot.render_tiers(now=0.0)[0]) == \
        len(hot.render_tiers(now=0.0)[0].replace("[$error]", "")
            .replace("[/$error]", ""))


def test_render_tiers_still_returns_four_tiers():
    """StatusBar.set_metrics feeds the tuple straight into _tiers(), which
    treats every element as a tier -- a fifth element would render as a
    fifth, narrower variant of the whole bar."""
    m = SessionMetrics(context_window=200_000, last_true_input=160_000)
    assert len(m.render_tiers(now=0.0)) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_metrics.py -k ctx_segment -v`
Expected: FAIL — no markup in the output.

- [ ] **Step 3: Write minimal implementation**

In `render_tiers`, replace the ctx block (`:221-226`):

```python
        ctx = ctx_short = ""
        if self.context_window > 0:
            live = self.p_in if self._provisional else self.last_true_input
            ctx_pct = round(100 * live / self.context_window)
            # Markup, not a fifth return value: StatusBar.set_metrics feeds
            # this tuple to _tiers(), which reads every element as a tier.
            # fit.plain_width strips tags, so the colour is free of width.
            tag = ("$error" if ctx_pct >= 75
                   else "$warning" if ctx_pct >= 50 else "")
            body = f"ctx {_fmt_tokens(live)} ({ctx_pct}%)"
            body_short = f"ctx {ctx_pct}%"
            if tag:
                body = f"[{tag}]{body}[/{tag}]"
                body_short = f"[{tag}]{body_short}[/{tag}]"
            ctx = f"{body} · "
            ctx_short = f"{body_short} · "
```

The signature and the four-tuple return are unchanged; `tui/pane.py` needs no
edit.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_metrics.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/metrics.py tests/test_metrics.py
git commit -m "feat(metrics): colour the ctx gauge yellow at 50%, red at 75%"
```

---

### Task 7: Show the `✂N` compaction counter

**Files:**
- Modify: `src/aegis/tui/metrics.py:201-240` (`render_tiers`)
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `compaction_count` (Task 3), the tier layout from Task 6.
- Produces: a `✂N` segment in tiers T0 and T1 only.

- [ ] **Step 1: Write the failing test**

```python
def test_no_scissors_segment_when_nothing_compacted():
    m = SessionMetrics(context_window=200_000, last_true_input=40_000)
    assert "✂" not in m.render_tiers(now=0.0)[0]


def test_scissors_segment_in_t0_and_t1_only():
    """It is a context-integrity signal, not a turn-by-turn one, so it
    drops out before the numbers you act on every turn."""
    m = SessionMetrics(context_window=200_000, last_true_input=40_000,
                       compaction_count=2)
    t0, t1, t2, t3 = m.render_tiers(now=0.0)
    assert "✂2" in t0
    assert "✂2" in t1
    assert "✂" not in t2
    assert "✂" not in t3


def test_scissors_is_yellow_at_one_and_red_at_two():
    one = SessionMetrics(context_window=200_000, compaction_count=1)
    assert "[$warning]✂1[/$warning]" in one.render_tiers(now=0.0)[0]
    two = SessionMetrics(context_window=200_000, compaction_count=2)
    assert "[$error]✂2[/$error]" in two.render_tiers(now=0.0)[0]


def test_scissors_glyph_is_single_width():
    """fit.plain_width counts characters after stripping markup, so a
    double-width glyph would overflow the bar by one column per use."""
    from rich.cells import cell_len
    assert cell_len("✂") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_metrics.py -k scissors -v`
Expected: FAIL — no `✂` in the output.

- [ ] **Step 3: Write minimal implementation**

In `render_tiers`, build the segment after the ctx block:

```python
        cut = ""
        if self.compaction_count > 0:
            cut_tag = "$error" if self.compaction_count >= 2 else "$warning"
            cut = f"[{cut_tag}]✂{self.compaction_count}[/{cut_tag}] · "
```

Then insert `{cut}` into the T0 and T1 return strings only — after `{cost}` in
both, leaving T2 and T3 untouched:

```python
        return (
            f"{head_full}{tps_seg}{ctx}{cost}{cut}{tool} · {turn} / {session}",
            f"{head_full}{ctx}{cost}{cut}{turn} / {session}",
            f"{head_bare}{ctx_short}{cost}{turn} / {session}",
            f"{head_bare}{cost}{turn}".rstrip(" ·"),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_metrics.py -q`
Expected: PASS

- [ ] **Step 5: Run the full fast suite**

Run: `uv run pytest -q -m "not live"`
Expected: PASS. A failure here is a real regression — investigate, do not
re-run.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/tui/metrics.py tests/test_metrics.py
git commit -m "feat(metrics): show a compaction counter in the status bar"
```

---

### Task 8: Verify against the real corpus, then close the docs

**Files:**
- Modify: `TASKS.md` (the *Context gauge accuracy + compaction detection*
  section under `## Active`)
- Modify: `docs/superpowers/specs/2026-08-09-context-gauge-and-compaction-design.md`
  (status header)
- Modify: `docs/roadmap.md` (unreleased/next section)

**Interfaces:**
- Consumes: everything above.
- Produces: no code. A verification result and honest doc state.

- [ ] **Step 1: Replay the fix over real session logs**

This is the check that matters — the unit tests use synthetic numbers, and
synthetic fixtures already agreed with this bug once (see the corpus
re-verification note in the spec). Write a throwaway script under
`.playground/` that walks `/home/apiad/Workspace/.aegis/state/sessions/*.jsonl`,
and for every turn compares the committed gauge against the window:

- Resolve the window per session from the `SystemInit` model via
  `aegis.models.get_context_window("claude-code", model)` — the provider key is
  `claude-code`, and any model containing `opus` resolves to 1,000,000.
- Skip streaming events whose `true_input` is 0 (152 exist in the corpus).
- Expected result: **at most 1 turn in ~6,871 above 100%**, versus 4,256 before
  the fix.
- Expected result: **exactly 17 `compact_boundary` events**, one per affected
  session, across 381 logs.

Record both numbers in the commit message. If the >100% count is materially
above 1, the fix is wrong — stop and re-open the spec rather than adjusting the
threshold.

- [ ] **Step 2: Exercise it in the real TUI**

Run `aegis` and open a session. Confirm by eye that the status bar shows
`ctx Nk (P%)` with a plausible percentage, that it is uncoloured early in a
session, and that the number does not jump at turn end. A green suite is not
evidence the bar renders — it is the artifact the user actually reads.

- [ ] **Step 3: Update the docs**

- Flip the spec's status header to `implemented (<commit>)`.
- Tick the task list in `TASKS.md`, and move the section from `## Active` to
  `## Recently shipped` with the corpus verification numbers.
- Add a roadmap entry under the next version.

Leave anything genuinely unverified unchecked with an honest note.

- [ ] **Step 4: Commit**

```bash
git add TASKS.md docs/roadmap.md \
  docs/superpowers/specs/2026-08-09-context-gauge-and-compaction-design.md
git commit -m "docs: close out the context-gauge and compaction work"
```

---

## Notes for the implementer

- **The two loops in `session.py` are the trap.** Tasks 4 and 5 both edit the
  same two places. The `grep -c` steps exist because a change landed in one
  loop passes every test that only drives the other.
- **Do not reintroduce a drop heuristic.** The design spec keeps the rejected
  approach and its numbers on purpose: 1,272 detections against 17 real
  compactions, 47% of them subagent events, 98% recovering within the turn.
  If ACP compaction detection comes up, the answer is "no protocol signal".
- **`✂` is safe, `⚡` is not.** `fit.plain_width` measures with `len()`, and
  `⚡` (already in the bar) is genuinely two cells — a pre-existing off-by-one
  in the width budget, out of scope here but worth knowing if the bar overflows.
