# Aegis Status-Line Metrics — Design

- **Date:** 2026-05-18
- **Status:** approved (pending written-spec review)
- **Builds on:** `2026-05-18-aegis-tui-design.md` (shipped)

## Goal

Append a live, at-a-glance session meter to the TUI status bar: cumulative
input/output tokens, cumulative tool calls (and errors), and elapsed time for
the current turn versus the whole session.

## Locked decisions

1. **Tokens & tool counts are cumulative for the session** (running totals
   that grow each turn). Time is **this turn / total session**.
2. **Fully live:** a 1-second interval timer advances the displayed turn and
   session time while the app runs. Tool counts increment live as tool events
   stream. Token counts finalize at turn end (their only source is the
   `result` event).
3. Humanized token counts; `(N err)` shown only when errors > 0; times as
   `Xs`, switching to `Mm SSs` past 60s.

## Display

Appended to the StatusBar right side, after the state label:

```
default · opus · auto    ✻ working…    ↑1.2k ↓340 · ⚒ 7 (1 err) · 12s / 4m03s
```

When tool errors are zero: `… · ⚒ 7 · 12s / 4m03s`.

### Formatting rules

- **Tokens:** `< 1000` → exact (`340`); `< 1_000_000` → `{n/1000:.1f}k`
  trimming a trailing `.0` (`1.2k`, `12k`); `>= 1_000_000` →
  `{n/1e6:.1f}M`. `None`/absent → `0`.
- **Tool segment:** `⚒ {calls}` always; ` ({errors} err)` only when
  `errors > 0`.
- **Time:** `< 60` → `{s}s`; `>= 60` → `{m}m{ss:02d}s` (e.g. `4m03s`).
  Turn time before the first turn → `0s`.

## Components

### 1. `events.py` — `Result` gains token fields

`Result` gets `input_tokens: int | None` and `output_tokens: int | None`
(in addition to existing `duration_ms`, `is_error`). `parse()` reads them from
the `result` event's `usage` object: `usage.input_tokens` /
`usage.output_tokens`. Missing `usage` or missing keys → `None`. Cache tokens,
cost, `num_turns`, `iterations` are intentionally not surfaced (YAGNI).

This is additive; existing `Result` consumers and fixtures are unaffected
(fields default to `None`).

### 2. New `src/aegis/tui/metrics.py` — `SessionMetrics`

Pure, `now`-injectable, independently testable. No Textual or I/O imports.

State:

- `session_start: float` — monotonic timestamp set at construction.
- `in_tokens: int`, `out_tokens: int` — cumulative.
- `tool_calls: int`, `tool_errors: int` — cumulative.
- `turn_start: float | None` — set while a turn is in flight, else `None`.
- `last_turn_seconds: float` — duration of the most recently finished/cancelled
  turn (shown when idle).

Methods:

- `start_turn(now: float)` — `turn_start = now`.
- `record_tool()` — `tool_calls += 1`.
- `record_tool_error()` — `tool_errors += 1`.
- `end_turn(result: Result, now: float)` — add `result.input_tokens or 0` /
  `output_tokens or 0` to the cumulative totals; set
  `last_turn_seconds = now - turn_start` (0 if `turn_start` is None);
  `turn_start = None`.
- `cancel_turn(now: float)` — like `end_turn` but no token addition: freeze
  `last_turn_seconds = now - turn_start`; `turn_start = None`.
- `turn_seconds(now: float) -> float` — `now - turn_start` if a turn is in
  flight, else `last_turn_seconds`.
- `session_seconds(now: float) -> float` — `now - session_start`.
- `render(now: float) -> str` — the formatted suffix string per the rules
  above.

`record_tool` / `record_tool_error` are independent: a tool call that errors
increments both (it is still a call). Errors are counted from
`ToolResult(is_error=True)` events; calls from `ToolUse` events.

### 3. `widgets.py` — `StatusBar.set_metrics(text: str)`

`StatusBar` renders three segments now:
`<identity>    <state-label>    <metrics>`. `set_metrics(text)` stores the
metrics string and refreshes; `set_state` continues to refresh the label.
Empty metrics string → the third segment is omitted (clean initial state
before any tick). Display-only; no logic, no time or token math in the widget.

### 4. `app.py` wiring

- Construct `SessionMetrics(now=monotonic())` in `on_mount`.
- `on_input_submitted` → `metrics.start_turn(monotonic())` before starting the
  worker.
- In `_run_turn`'s event loop: `ToolUse` → `metrics.record_tool()`;
  `ToolResult` with `is_error` → `metrics.record_tool_error()`; `Result` →
  `metrics.end_turn(ev, monotonic())`. After each, call
  `StatusBar.set_metrics(metrics.render(monotonic()))`.
- `action_interrupt` → `metrics.cancel_turn(monotonic())` then refresh.
- **Live tick:** `self.set_interval(1.0, self._tick)` registered in
  `on_mount` (always-on; one cheap widget update/sec). `_tick` calls
  `StatusBar.set_metrics(metrics.render(monotonic()))`. While a turn runs the
  turn clock advances; when idle it shows the last turn's frozen duration; the
  session clock always advances.
- Time source: a single `_now()` helper wrapping `time.monotonic()` so tests
  can monkeypatch it; `SessionMetrics` itself takes `now` as a parameter (no
  internal clock) and stays pure.

## Error handling

- Missing/partial `usage` in a `result` event → token fields `None` →
  contribute `0` to totals (no crash, meter just doesn't grow that turn).
- `end_turn`/`cancel_turn` with `turn_start is None` (defensive: interrupt with
  no active turn) → `last_turn_seconds` unchanged, no negative time.
- The interval timer is registered once; no teardown needed (Textual cancels
  app intervals on exit). `action_quit` is unchanged.

## Testing

- **`SessionMetrics`** (pure unit, injected `now`): cumulative token
  accumulation across two turns; `record_tool`/`record_tool_error` counts;
  a call that errors increments both; `turn_seconds` in-flight vs idle;
  `session_seconds` monotonic; `cancel_turn` freezes turn time and adds no
  tokens; `render` format for: zero errors (segment hidden), nonzero errors,
  token humanization boundaries (999→`999`, 1000→`1.0k`→trimmed `1k`,
  1234→`1.2k`, 1_000_000→`1.0M`), time `<60`/`>=60` (`45s`, `4m03s`),
  pre-first-turn (`0s`).
- **`events.py`:** a `result` line with `usage` → `Result.input_tokens` /
  `output_tokens` populated; a `result` line without `usage` → both `None`;
  the existing real fixtures still parse (their `usage` is redacted/absent →
  `None`, which is fine).
- **Pilot (`test_tui.py`):** a `FakeSession` yielding `ToolUse`,
  `ToolResult(is_error=True)`, then `Result(input_tokens=1200,
  output_tokens=340, duration_ms=...)`; after the turn assert the StatusBar
  text contains `↑1.2k`, `↓340`, `⚒ 1 (1 err)`; assert `_tick` updates the
  displayed string (advance the monkeypatched clock, call `_tick`, see
  session seconds change).
- All tests written and validated inline by the implementer; the live
  `claude` driver test is unchanged.

## Cleanup folded into this increment

From the TUI final review (one worthwhile leftover):

- Add a pilot test for the spec'd `error → working → ready` resend transition
  (currently the only spec'd state transition without coverage).
- Add a one-line note to the TUI spec's Tab-state section that the status-bar
  error label is the generic `⚠ error` (the specific message appears in the
  transcript), recording the intentional simplification.

## Non-goals

Cost ($), cache-token display, `num_turns`, per-turn token breakdown, color
coding of the meter, configurable format string, persistence of metrics across
`aegis` restarts.
