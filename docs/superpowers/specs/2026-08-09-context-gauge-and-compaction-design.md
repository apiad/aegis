# Context gauge accuracy and compaction detection — design

**Status:** specced 2026-08-09, not yet planned  
**Scope:** `SessionMetrics`, `session.py`, `tui/metrics.py` (render), status-bar colour.
TUI only for the visual layer; the detection fix benefits all frontends.

---

## The problems

### 1. ctx% shows 1000%+ for long agentic turns

`SessionMetrics.commit()` writes:

```python
self.last_true_input = usage.true_input   # BUG
```

`usage` here is `Result.usage`, which in the Claude stream-json protocol
accumulates across **all sub-turns** within a single user interaction.  A
30-sub-turn agentic response with average 70 k context per sub-turn yields
`true_input ≈ 2.1 M`.  Divided by a 200 k context window → **1050 %**.

Investigation (2026-08-09, `.playground/analyze_compaction.py`, sessions
`vast-valiant.jsonl` and `true-tarjan.jsonl`): the streaming `AssistantText /
AssistantThinking / ToolUse` events each carry a *per-sub-turn* `usage`
snapshot.  `SessionMetrics.observe()` already tracks these via `p_in = max(p_in,
u.true_input)`.  Those values are sane (39 k–163 k for an Opus 4.7 session),
and `p_in` is what we want — it gets zeroed in `commit()` before
`last_true_input` is written, so the gauge briefly reads something reasonable
during a turn but snaps to a wrong value at turn end.

### 2. Compaction is invisible to the user

Claude's auto-compaction fires **intra-turn** — the context drops mid-agentic-
loop, not between user messages.  Both events observed in `vast-valiant`:

| session | sub-turn before | sub-turn after | drop |
|---|---|---|---|
| vast-valiant turn 3 | 124,897 | 51,452 | −59 % |
| vast-valiant turn 5 | 163,160 | 50,064 | −69 % |

After compaction the model only sees a compressed summary (~50 k) instead of
the full conversation.  The user has no indication this happened.

### 3. The Result-level drop is a false positive

Comparing `Result.usage.true_input` between consecutive user turns is
unreliable: a short turn (num_turns=1) will show a 94% "drop" vs a long turn
(num_turns=18) purely because it accumulated fewer sub-turn tokens — no
compaction involved.  (Verified: turn 6→7 in vast-valiant, both starting at
~136 k per-sub-turn context.)

---

## Fix 1 — accurate context gauge

**Where:** `SessionMetrics.commit()` in `tui/metrics.py`

Save `p_in` as `last_true_input` *before* zeroing it:

```python
def commit(self, usage: TokenUsage | None, now: float) -> None:
    # Capture per-sub-turn peak BEFORE reset.
    # p_in is the max true_input seen in streaming events this turn —
    # that is the real single-sub-turn context size.
    # Result.usage.true_input accumulates across all sub-turns and must
    # NOT be used as the context gauge denominator.
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

No other change to the render path — `render_tiers()` already uses
`last_true_input` for the committed gauge.

**ACP bonus:** ACP's `UsageUpdate` fires `ContextUpdate(cost=CostUsage(
context_used=N, context_size=M))`.  The `context_size` field is the model's
actual window limit — more reliable than the YAML registry.  Wire it in
`session.py`:

```python
elif isinstance(ev, ContextUpdate):
    if ev.cost:
        if ev.cost.context_used:
            self.metrics.observe_context(ev.cost.context_used)
        if ev.cost.context_size:
            self.metrics.context_window = ev.cost.context_size
```

(`observe_context` is the new shared method described below.)

---

## Fix 2 — compaction detection

**Where:** `SessionMetrics.observe()` → refactored into `observe_context(ti)`

All per-sub-turn context size observations funnel through one method, regardless
of harness:

```python
def observe_context(self, ti: int) -> None:
    """Record one sub-turn context-size snapshot.

    Called from observe() (Claude streaming usage) and from the
    ContextUpdate path (ACP UsageUpdate). Detects intra-turn compaction
    as a >50% drop from the current p_in high-water mark.
    """
    if self.p_in > _COMPACTION_MIN and ti < self.p_in * _COMPACTION_RATIO:
        self.compaction_count += 1
        self._compaction_happened_this_turn = True
        # Reset the high-water mark to the new (post-compaction) baseline
        # so the gauge reads correctly after the drop.
        self.p_in = ti
    else:
        self.p_in = max(self.p_in, ti)
    self.last_live_ti = ti   # most recent snapshot; for provisional gauge


def observe(self, u: TokenUsage) -> None:
    """Streaming usage snapshot from Claude (per-sub-turn)."""
    self.observe_context(u.true_input)
    self.p_out = max(self.p_out, u.output)
    self.p_cached = max(self.p_cached, u.cache_read)
    self._provisional = True
```

Thresholds (empirical, two real sessions):
```python
_COMPACTION_MIN   = 20_000   # ignore noise below this
_COMPACTION_RATIO = 0.50     # >50% drop = compaction
```

Both observed compactions were −59% and −69%; normal inter-sub-turn growth
never drops.  Normal *inter-turn* starts (e.g. 136 k → 90 k) are −34%, safely
below the threshold.

New fields on `SessionMetrics`:
```python
compaction_count: int = 0
_compaction_happened_this_turn: bool = False
last_live_ti: int = 0
```

Reset `_compaction_happened_this_turn` in `start_turn()`.

---

## Fix 3 — visual colour on the context gauge

**Where:** `tui/metrics.py:render_tiers()` and the status-bar widget

The ctx segment currently renders as plain text.  Wrap it in a Textual
`Rich` markup colour tag keyed on `ctx_pct`:

| pct | colour | meaning |
|---|---|---|
| < 50 % | (default) | plenty of room |
| 50–74 % | yellow / `#f59e0b` | getting full |
| ≥ 75 % | red / `#ef4444` | approaching compaction |

Implementation options:
- Return the colour tag alongside the tier strings so the caller can mark it up.
- Or: expose `ctx_color(pct) -> str` helper and apply it in `pane.py`'s
  status-bar render.

`render_tiers()` already returns four tier strings fed through `fit()` then
into a Textual `Label`.  Textual's `Label` respects Rich markup, so wrapping
the ctx segment in `[yellow]...[/yellow]` / `[red]...[/red]` is sufficient.
The render path in `pane.py` can apply the colour after calling
`metrics.render_tiers()` by finding the `ctx …%` substring and wrapping it —
or `render_tiers()` returns the colour as a fifth element so callers don't need
to parse the string.

Recommended: fifth return value `ctx_color: str` from `render_tiers()` so the
caller doesn't parse formatted strings.

---

## Fix 4 — compaction counter in the status bar

When `compaction_count > 0`, append a `✂N` segment to T0 and T1 tiers only
(it's a context-integrity signal, not a turn-by-turn one):

```
↑45k (32% cached) ↓3k · ctx 142k (71%) · $1.84 · ⚒ 12 · ✂2 · 2m14s / 18m
```

Yellow colour at ✂1; red at ✂2+, since multiple compactions in one session
means the agent is working with a heavily pruned picture.

---

## Generalisation across harnesses

The detection lives entirely in `SessionMetrics.observe_context(ti)` — no
driver changes needed.  Routing:

| harness | signal source | session.py path |
|---|---|---|
| Claude (stream-json) | `AssistantText / AssistantThinking / ToolUse .usage.true_input` | existing `getattr(ev, "usage", None)` → `observe(u)` → `observe_context()` |
| ACP (OpenCode, Lovelaice, …) | `UsageUpdate` → `ContextUpdate.cost.context_used` | new `elif isinstance(ev, ContextUpdate)` branch → `observe_context()` |
| Gemini (ACP) | depends on whether `UsageUpdate` is fired — untested | same branch; degrades to 0 if not fired |

The YAML registry `context_window` lookup remains as fallback; for ACP sessions
it gets overridden by the live `context_size` from `ContextUpdate`.

---

## Files to change

| file | what changes |
|---|---|
| `src/aegis/tui/metrics.py` | `observe_context()`, `observe()` refactor, `commit()` fix, new fields, `render_tiers()` colour output |
| `src/aegis/core/session.py` | `elif isinstance(ev, ContextUpdate)` routing for ACP |
| `src/aegis/tui/pane.py` | apply `ctx_color` to status-bar label |
| `src/aegis/events.py` | no changes needed |
| `src/aegis/drivers/*.py` | no changes needed |

---

## Out of scope for this spec

- **Agent priming**: telling the agent it can call a history-read tool after
  compaction.  Requires a new MCP tool (`aegis_read_self`) and a PRIMING
  addition; tracked separately.
- **Transcript separator**: visual `── compacted ──` line in the pane body.
  Can be done by emitting a synthetic `Unknown` or `AssistantText` event when
  `_compaction_happened_this_turn` becomes True; save for a polish pass.
- **Web client**: no colour or counter changes there yet.

---

## Discovered facts (investigation 2026-08-09)

- Sessions `vast-valiant.jsonl` (4.1 MB, 9 result events) and
  `true-tarjan.jsonl` (1.4 MB, 22 result events) from
  `/home/apiad/Workspace/.aegis/state/sessions/`.
- Script: `/home/apiad/Workspace/.playground/analyze_compaction.py`
- Both sessions ran `claude-opus-4-7`; context window 200 k.
- Per-sub-turn true_input ranged 39 k–163 k — all sane, all well under 200 k.
- Result.usage.true_input ranged 137 k–12.4 M — accumulation artefact.
- Compaction threshold empirical: both drops were 59% and 69%.  50% ratio is
  safe; normal inter-sub-turn growth never drops; inter-turn starts drop at most
  ~34%.
- Post-compaction context (summary): ~50–52 k in both cases.
- ACP's `ContextUpdate.cost.context_size` can replace the YAML registry for
  ACP sessions — more reliable.
