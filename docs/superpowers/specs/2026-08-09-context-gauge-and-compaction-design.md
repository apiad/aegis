# Context gauge accuracy and compaction detection — design

**Status:** revised 2026-08-10 after corpus-wide re-verification; ready to plan
**Scope:** `SessionMetrics`, `core/session.py`, `events.py`, `tui/metrics.py`
(render), status-bar colour. TUI only for the visual layer; the detection fix
benefits all frontends.

**Revision note.** The first draft (2026-08-09) was written from two session
logs. Re-running its hypotheses over the whole local corpus (381 logs, 6,871
turns) confirmed Fix 1 far more strongly than claimed and **refuted Fix 2**:
the >50% drop heuristic has ~1.3% precision, and the two compactions the
original draft cited as evidence never happened. Claude Code emits an explicit
`compact_boundary` event that makes the heuristic unnecessary. The rejected
approach is kept in full at the end so it is not reintroduced.

---

## The problems

### 1. ctx% shows >100% on most agentic turns

`SessionMetrics.commit()` writes:

```python
self.last_true_input = usage.true_input   # BUG
```

`usage` here is `Result.usage`, which in the Claude stream-json protocol
accumulates across **all sub-turns** of a single user interaction. The gauge
then divides that sum by the context window.

Measured over the corpus:

| | |
|---|---|
| turns rendering >100% today | **4,256 / 6,871 (61.9%)** |
| turns where `Result.usage.true_input` exceeds the per-sub-turn peak | 5,800 / 6,871 (84.4%) |
| worst single turn | **92,956%** — 1,138 sub-turns, `blithe-backus`, opus-4-7 |

The streaming `AssistantText / AssistantThinking / ToolUse` events each carry a
*per-sub-turn* usage snapshot. `observe()` already tracks their maximum as
`p_in`, and that value is the real single-sub-turn context size — but
`commit()` zeroes `p_in` and overwrites `last_true_input` with the accumulated
figure, so the gauge reads sanely mid-turn and snaps to a wrong value at turn
end.

### 2. Compaction is invisible to the user

Claude's auto-compaction fires **intra-turn**. After it, the model sees a
compressed summary instead of the full conversation, and the user has no
indication it happened. The gauge is also stuck: `p_in` retains the
pre-compaction high-water mark for the rest of the turn.

---

## Fix 1 — accurate context gauge

**Where:** `SessionMetrics.commit()` in `tui/metrics.py`

Save the per-sub-turn peak before zeroing it:

```python
def commit(self, usage: TokenUsage | None, now: float) -> None:
    # p_in is the max true_input seen in streaming events this turn — the
    # real single-sub-turn context size. Result.usage.true_input accumulates
    # across all sub-turns and must NOT be used as the gauge numerator.
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

Cumulative accounting (`c_in`, cost) is unaffected — accumulation is correct
there. Only the gauge numerator changes.

**Verified:** replaying this against the corpus leaves **1 turn in 6,871**
above 100%, down from 4,256.

### Why `max()` over interleaved contexts is safe

The stream carries more than one context. A turn's events interleave the main
agent's context with each subagent's, and `parent_tool_use_id` does not
reliably distinguish them (see the rejected approach below). `observe()` maxes
over all of them.

This is safe for the gauge because a subagent's context is smaller than its
parent's — the max is the main thread in practice, which is what the corpus
measurement above demonstrates. It is an empirical guarantee, not a structural
one; if it ever breaks, the fix is to track the main thread explicitly, and the
signal to watch is a gauge that drops while the session is still growing.

### Context window resolution

Worth recording because the original draft got it wrong: the registry provider
key is **`claude-code`**, not `claude`, and `get_context_window` falls through
to a substring pattern table (`data/models.yaml:24-29`) — any model containing
`opus` or `1m` resolves to **1,000,000**, everything else to 200,000. Every
session in the corpus is Opus, so the live window is 1M, and the >100% readings
are driven purely by sub-turn count.

---

## Fix 2 — compaction from the explicit boundary event

Claude Code emits a system event at every compaction:

```json
{"type": "system", "subtype": "compact_boundary",
 "session_id": "...", "uuid": "...",
 "compact_metadata": {
   "trigger": "auto",
   "pre_tokens": 999917,
   "post_tokens": 15022,
   "cumulative_dropped_tokens": 984895,
   "duration_ms": 163448}}
```

It currently falls through `_classify_event` to `Unknown(raw=line)`.

**Corpus evidence:** 17 events across 381 logs, exactly one per affected
session, `trigger: "auto"` in all 17. `pre_tokens` is ~1,000,000 in every case
— compaction is a **window-ceiling event**, not mid-session drift.
`post_tokens` ranges 10,615–50,572.

| date | pre_tokens | post_tokens | session |
|---|---|---|---|
| 2026-06-10 | 999,235 | 21,677 | deep-dijkstra |
| 2026-06-14 | 1,000,130 | 13,459 | blithe-backus |
| 2026-06-16 | 1,004,373 | 30,759 | civic-codd |
| 2026-07-29 | 1,000,296 | 10,615 | manifoldx-modeling |
| 2026-07-30 | 999,917 | 15,022 | ainbox-bugfix-relay |

*(17 total; five shown.)*

### New typed event

```python
@dataclass
class CompactBoundary:
    trigger: str            # "auto" | "manual"
    pre_tokens: int
    post_tokens: int
    dropped_tokens: int = 0
    duration_ms: int = 0
```

Parsed in `_classify_event` alongside the existing `system`/`init` and
`system`/`thinking_tokens` branches (`events.py:402`, `:423`), and added to the
`Event` union (`events.py:234`).

### Metrics

```python
def note_compaction(self, post_tokens: int) -> None:
    """An authoritative compaction boundary from the harness.

    Resets the high-water mark to the post-compaction size: p_in holds the
    pre-compaction peak (~the full window), and without this the gauge would
    read ~100% for the rest of the turn.
    """
    self.compaction_count += 1
    self.p_in = post_tokens
    self.last_true_input = post_tokens
```

New fields: `compaction_count: int = 0`.

There is no threshold, no floor and no ratio — the harness has already decided.

### Routing

`core/session.py` has **two** structurally identical event loops (the live turn
at `:504-522` and the second at `:708-730`); both route metrics and both need
the branch:

```python
elif isinstance(ev, CompactBoundary):
    self.metrics.note_compaction(ev.post_tokens)
```

---

## Fix 3 — colour on the context gauge

**Where:** `tui/metrics.py:render_tiers()` and the status-bar render in
`tui/pane.py`

| pct | colour | meaning |
|---|---|---|
| < 50 % | (default) | plenty of room |
| 50–74 % | `$warning` | getting full |
| ≥ 75 % | `$error` | approaching compaction |

`render_tiers()` returns a fifth value `ctx_color: str` so the caller does not
parse formatted strings. Note `AegisColors` has **no `warning` role** — its
roles are ready/working/error/accent/muted/ok/err/user/user_bg — so this uses
Textual's `$warning`/`$error` theme variables in markup, or `err` from the
palette.

---

## Fix 4 — compaction counter in the status bar

When `compaction_count > 0`, append a `✂N` segment to the T0 and T1 tiers only
(it is a context-integrity signal, not a turn-by-turn one):

```
↑45k (32% cached) ↓3k · ctx 142k (71%) · $1.84 · ⚒ 12 · ✂2 · 2m14s / 18m
```

Yellow at ✂1, red at ✂2+. The glyph must stay single-width: `fit.plain_width`
measures with `len()`, not `cell_len`, on the documented assumption that
status-bar glyphs are single-width.

---

## Generalisation across harnesses

| harness | gauge signal | compaction |
|---|---|---|
| Claude (stream-json) | streaming `usage.true_input` → `observe()` | `compact_boundary` → `note_compaction()` |
| ACP (OpenCode, Lovelaice) | `ContextUpdate.cost.context_used` → `observe_context()` | **none** — no protocol signal |
| Gemini (ACP) | same, if `UsageUpdate` fires (untested) | none |

For ACP, wire the gauge in the same two loops:

```python
elif isinstance(ev, ContextUpdate):
    if ev.cost:
        if ev.cost.context_used:
            self.metrics.observe_context(ev.cost.context_used)
        if ev.cost.context_size:
            self.metrics.context_window = ev.cost.context_size
```

`observe_context(ti)` is the shared funnel — `observe()` calls it for the
Claude path, the `ContextUpdate` branch calls it for ACP:

```python
def observe_context(self, ti: int) -> None:
    """Record one sub-turn context-size snapshot, from any harness."""
    self.p_in = max(self.p_in, ti)
```

ACP sessions get an accurate gauge and no `✂` segment. **Do not fall back to
the drop heuristic there** — at 12% precision it is worse than showing nothing.

`ContextUpdate.cost.context_size` is the model's real window and overrides the
YAML registry for ACP sessions.

---

## Rejected: the >50% intra-turn drop heuristic

The 2026-08-09 draft proposed detecting compaction as a >50% drop in streaming
`true_input` above a 20k floor. **Do not reintroduce this.** Replayed over the
corpus it fires **1,272 times** against 17 real compactions — ~1.3% precision.

Why it fails: the event stream interleaves independent contexts, and
`parent_tool_use_id` tags only some of them. A real turn from `ample-adleman`:

```
22 AssistantThinking  ti=123,312  cc= 2,059  cr=121,252   <- main, climbing
23 ToolUse            ti= 34,823  cc=34,817  cr=      0   <- a fresh context
24 ToolUse            ti=123,312  cc= 2,059  cr=121,252   <- back to main
25 ToolUse            ti= 34,823  cc=     0  cr= 34,817
```

Diagnostics on the 1,272 detections:

- **596 (47%)** carry a `parent_tool_use_id` — subagents outright.
- **1,244 (98%)** recover to ≥90% of the pre-drop peak later in the same turn.
  Compaction is irreversible; these are context switches.
- Filtering to main-thread events *and* applying offline irreversibility leaves
  22 — close to the 17 real ones, but that filter needs the future.
- An online approximation (confirm a drop only if the next *k* snapshots stay
  below `peak × confirm`), swept over k ∈ {1,2,3,5} × confirm ∈ {60,75,90}%,
  reaches 100% recall at **12% precision at best**. The oscillation is
  persistent, so no lookahead window separates the classes.

The draft's own evidence does not survive: `vast-valiant` (opus-4-8) and
`true-tarjan` (opus-5) both have **zero** `compact_boundary` events. Its two
"observed compactions" — 124,897→51,452 and 163,160→50,064 — are subagent
switches at 12% and 16% of a 1M window, not compaction. The draft read them as
compaction only because it assumed a 200k window.

---

## Files to change

| file | what changes |
|---|---|
| `src/aegis/events.py` | `CompactBoundary` dataclass, `_classify_event` branch, `Event` union |
| `src/aegis/tui/metrics.py` | `commit()` fix, `observe_context()`, `note_compaction()`, `compaction_count`, `render_tiers()` colour + `✂N` |
| `src/aegis/core/session.py` | `CompactBoundary` + `ContextUpdate` routing in **both** event loops |
| `src/aegis/tui/pane.py` | apply `ctx_color` to the status-bar label |
| `src/aegis/drivers/*.py` | none |

---

## Out of scope

- **Agent priming** — telling the agent it can read its own history after
  compaction. Needs an `aegis_read_self` MCP tool and a PRIMING addition.
- **Transcript separator** — a visual `── compacted ──` line in the pane body.
  Now trivial to emit off `CompactBoundary`; save for a polish pass.
- **Web client** — no colour or counter changes there yet.
- **Compaction for ACP harnesses** — no protocol signal exists.

---

## Discovered facts (corpus re-verification, 2026-08-10)

- Corpus: 381 logs in `/home/apiad/Workspace/.aegis/state/sessions/`, 6,871
  turns carrying streaming usage, spanning 2026-05-22 to 2026-08-10.
  Scripts: `.playground/ctx-gauge/verify{,2,3,4,5}.py` (throwaway).
- Models seen: `claude-opus-5` (150), `claude-opus-4-8` (117),
  `claude-opus-4-7` (85), OpenCode (3). All Claude entries resolve to a **1M**
  window through the `opus` substring pattern.
- 152 streaming events carry a zero `true_input`; they must be skipped, not fed
  to any detector.
- A step's usage repeats verbatim across consecutive events — dedupe before
  any sequential analysis.
- Real compaction always fires at `pre_tokens` ≈ the window ceiling. If a
  future harness compacts mid-session, that assumption is worth re-checking,
  but nothing should depend on it: the boundary event is authoritative.
