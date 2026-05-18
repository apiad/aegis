# Aegis Status-Line Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Append a live session meter (↑in / ↓out tokens · tool calls (errors) · turn time / session time) to the TUI status bar.

**Architecture:** `events.py`'s `Result` gains optional token fields parsed from the stream-json `result` event's `usage`. A new pure `SessionMetrics` (now-injectable, no Textual/I-O) accumulates cumulative tokens/tool-counts and computes turn/session elapsed. `AegisApp` owns one instance, feeds it events during `_run_turn`, and a 1-second interval refreshes a third StatusBar segment.

**Tech Stack:** Python 3.13, Textual 8.2.6, pytest (Textual pilot). Spec: `docs/superpowers/specs/2026-05-18-aegis-status-metrics-design.md`.

---

## File Structure

| Path | Change |
|---|---|
| `src/aegis/events.py` | `Result` dataclass + `parse()` gain `input_tokens`/`output_tokens`. |
| `src/aegis/tui/metrics.py` | **New.** Pure `SessionMetrics`. |
| `src/aegis/tui/widgets.py` | `StatusBar` gains a third `metrics` segment via `set_metrics`. |
| `src/aegis/tui/app.py` | Owns `SessionMetrics`, `_now()`, interval tick, event hooks. |
| `tests/test_events.py` | + token-parsing tests. |
| `tests/test_metrics.py` | **New.** Pure unit tests. |
| `tests/test_tui.py` | + metrics pilot test; + `error→working` resend test (folded cleanup). |
| `docs/superpowers/specs/2026-05-18-aegis-tui-design.md` | + one-line `⚠ error` label note (folded cleanup). |

---

### Task 1: `Result` carries token counts

**Files:** Modify `src/aegis/events.py`; Modify `tests/test_events.py`

- [ ] **Step 1: Add failing tests** — append to `tests/test_events.py`:

```python
def test_parse_result_with_usage_tokens():
    ev = parse(json.dumps({
        "type": "result", "subtype": "success",
        "duration_ms": 700, "is_error": False,
        "usage": {"input_tokens": 1200, "output_tokens": 340},
    }))
    assert isinstance(ev, Result)
    assert ev.input_tokens == 1200
    assert ev.output_tokens == 340


def test_parse_result_without_usage_tokens_are_none():
    ev = parse(json.dumps({"type": "result", "subtype": "success",
                            "duration_ms": 1, "is_error": False}))
    assert ev.input_tokens is None
    assert ev.output_tokens is None
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_events.py -q`
Expected: FAIL — `Result` has no `input_tokens` attribute.

- [ ] **Step 3: Implement** — in `src/aegis/events.py`, change the `Result` dataclass from:

```python
@dataclass
class Result:
    duration_ms: int | None
    is_error: bool
```

to:

```python
@dataclass
class Result:
    duration_ms: int | None
    is_error: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
```

And in `parse()`, change the result branch from:

```python
    if etype == "result":
        return Result(
            duration_ms=obj.get("duration_ms"),
            is_error=bool(obj.get("is_error", False)),
        )
```

to:

```python
    if etype == "result":
        usage = obj.get("usage") or {}
        return Result(
            duration_ms=obj.get("duration_ms"),
            is_error=bool(obj.get("is_error", False)),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_events.py -q`
Expected: all pass (incl. the two real-fixture tests — their `usage` is redacted/absent so tokens are `None`, still parse).

- [ ] **Step 5: Full fast suite green**

Run: `uv run pytest -q -k "not live"`
Expected: all pass (the added `Result` fields default to `None`; `test_tui.py`/`render_event` unaffected).

- [ ] **Step 6: Commit**

```bash
git add src/aegis/events.py tests/test_events.py
git -c user.name="Alejandro Piad" -c user.email="apiad@apiad.net" commit -m "feat(events): Result carries input/output token counts from usage"
```

---

### Task 2: `SessionMetrics` (pure)

**Files:** Create `src/aegis/tui/metrics.py`; Create `tests/test_metrics.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_metrics.py`:

```python
from aegis.events import Result
from aegis.tui.metrics import SessionMetrics


def _r(inp=None, out=None):
    return Result(duration_ms=0, is_error=False,
                  input_tokens=inp, output_tokens=out)


def test_tokens_accumulate_across_turns():
    m = SessionMetrics(0.0)
    m.start_turn(0.0)
    m.end_turn(_r(100, 20), 1.0)
    m.start_turn(2.0)
    m.end_turn(_r(50, 5), 3.0)
    assert m.in_tokens == 150
    assert m.out_tokens == 25


def test_missing_tokens_contribute_zero():
    m = SessionMetrics(0.0)
    m.start_turn(0.0)
    m.end_turn(_r(None, None), 1.0)
    assert m.in_tokens == 0 and m.out_tokens == 0


def test_tool_counts_and_error_increments_both():
    m = SessionMetrics(0.0)
    m.record_tool()
    m.record_tool()
    m.record_tool_error()  # an erroring call: caller also called record_tool
    assert m.tool_calls == 2
    assert m.tool_errors == 1


def test_turn_seconds_in_flight_then_idle():
    m = SessionMetrics(0.0)
    m.start_turn(10.0)
    assert m.turn_seconds(13.0) == 3.0          # in flight
    m.end_turn(_r(), 15.0)
    assert m.turn_seconds(99.0) == 5.0          # frozen at last turn


def test_session_seconds_monotonic():
    m = SessionMetrics(100.0)
    assert m.session_seconds(160.0) == 60.0


def test_cancel_turn_freezes_time_no_tokens():
    m = SessionMetrics(0.0)
    m.start_turn(10.0)
    m.cancel_turn(14.0)
    assert m.turn_seconds(999.0) == 4.0
    assert m.in_tokens == 0 and m.out_tokens == 0


def test_render_hides_errors_when_zero():
    m = SessionMetrics(0.0)
    m.record_tool()
    out = m.render(0.0)
    assert "⚒ 1" in out
    assert "err" not in out


def test_render_shows_errors_when_nonzero():
    m = SessionMetrics(0.0)
    m.record_tool()
    m.record_tool_error()
    assert "⚒ 1 (1 err)" in m.render(0.0)


def test_token_humanization_boundaries():
    m = SessionMetrics(0.0)
    m.start_turn(0.0)
    m.end_turn(_r(999, 1000), 0.0)
    s = m.render(0.0)
    assert "↑999" in s
    assert "↓1k" in s
    m.start_turn(0.0)
    m.end_turn(_r(235, 999_001), 0.0)   # in: 999+235=1234 -> 1.2k
    s = m.render(0.0)
    assert "↑1.2k" in s
    assert "↓1.0M" in s                  # out: 1000+999001=1_000_001 -> 1.0M


def test_time_formatting():
    m = SessionMetrics(0.0)
    m.start_turn(0.0)
    # turn started at 0, session_start 0; now=45 -> both 45s
    assert m.render(45.0).endswith("45s / 45s")
    m2 = SessionMetrics(0.0)
    m2.start_turn(0.0)
    # now=243 -> 243s -> "4m03s" for both turn and session
    assert m2.render(243.0).endswith("4m03s / 4m03s")


def test_render_before_first_turn_is_zero_seconds():
    m = SessionMetrics(0.0)
    assert "0s / 0s" in m.render(0.0)
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_metrics.py -q`
Expected: ImportError — `aegis.tui.metrics` does not exist.

- [ ] **Step 3: Implement** — `src/aegis/tui/metrics.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field

from aegis.events import Result


def _fmt_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        s = f"{n / 1000:.1f}"
        if s.endswith(".0"):
            s = s[:-2]
        return s + "k"
    return f"{n / 1_000_000:.1f}M"


def _fmt_time(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m{s % 60:02d}s"


@dataclass
class SessionMetrics:
    session_start: float
    in_tokens: int = 0
    out_tokens: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    turn_start: float | None = None
    last_turn_seconds: float = 0.0

    def start_turn(self, now: float) -> None:
        self.turn_start = now

    def record_tool(self) -> None:
        self.tool_calls += 1

    def record_tool_error(self) -> None:
        self.tool_errors += 1

    def end_turn(self, result: Result, now: float) -> None:
        self.in_tokens += result.input_tokens or 0
        self.out_tokens += result.output_tokens or 0
        if self.turn_start is not None:
            self.last_turn_seconds = now - self.turn_start
        self.turn_start = None

    def cancel_turn(self, now: float) -> None:
        if self.turn_start is not None:
            self.last_turn_seconds = now - self.turn_start
        self.turn_start = None

    def turn_seconds(self, now: float) -> float:
        if self.turn_start is not None:
            return now - self.turn_start
        return self.last_turn_seconds

    def session_seconds(self, now: float) -> float:
        return now - self.session_start

    def render(self, now: float) -> str:
        tool = f"⚒ {self.tool_calls}"
        if self.tool_errors:
            tool += f" ({self.tool_errors} err)"
        return (
            f"↑{_fmt_tokens(self.in_tokens)} "
            f"↓{_fmt_tokens(self.out_tokens)} · "
            f"{tool} · "
            f"{_fmt_time(self.turn_seconds(now))} / "
            f"{_fmt_time(self.session_seconds(now))}"
        )
```

(`field` import is unused-safe to drop; keep imports minimal — remove
`field` from the import if your linter flags it, it is not used.)

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_metrics.py -q`
Expected: all pass. (`test_time_formatting` and `test_token_humanization_boundaries` assert the exact `_fmt_*` outputs above.)

- [ ] **Step 5: Full fast suite green**

Run: `uv run pytest -q -k "not live"`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/tui/metrics.py tests/test_metrics.py
git -c user.name="Alejandro Piad" -c user.email="apiad@apiad.net" commit -m "feat(tui): SessionMetrics — pure cumulative token/tool/time tracker"
```

---

### Task 3: Wire metrics into StatusBar + AegisApp (live)

**Files:** Modify `src/aegis/tui/widgets.py`; Modify `src/aegis/tui/app.py`; Modify `tests/test_tui.py`

- [ ] **Step 1: Add the failing pilot test** — append to `tests/test_tui.py`
  (the file already imports `pytest`, `Agent`, `AssistantText`, `Result`,
  `AegisApp`, `AgentState`, `TabStrip`, `StatusBar`, `Text`, `RichLog`,
  `Input`; add `ToolUse, ToolResult` to the `from aegis.events import ...`
  line):

```python
class ToolTurnSession:
    def __init__(self):
        self.sent = []
        self.started = self.closed = False

    async def start(self):
        self.started = True

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        from aegis.events import ToolUse, ToolResult, Result
        yield ToolUse(name="Bash", summary="echo hi")
        yield ToolResult(text="boom", is_error=True)
        yield Result(duration_ms=10, is_error=False,
                     input_tokens=1200, output_tokens=340)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_status_metrics_render_and_tick():
    from aegis.tui.metrics import SessionMetrics
    app = AegisApp(ToolTurnSession(), _agent(), "default")
    async with app.run_test() as pilot:
        clock = [100.0]
        app._now = lambda: clock[0]
        app._metrics = SessionMetrics(clock[0])
        inp = app.query_one(Input)
        inp.value = "go"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        sb_text = str(app.query_one(StatusBar).renderable)
        assert "↑1.2k" in sb_text
        assert "↓340" in sb_text
        assert "⚒ 1 (1 err)" in sb_text
        # live tick: advance the clock and tick -> session time grows
        clock[0] = 225.0
        app._tick()
        sb_text2 = str(app.query_one(StatusBar).renderable)
        assert "2m05s" in sb_text2          # 225 - 100 = 125s = 2m05s
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_tui.py::test_status_metrics_render_and_tick -q`
Expected: FAIL — `app._now`/`app._metrics`/`app._tick` or `StatusBar` metrics not present.

- [ ] **Step 3: Update `StatusBar`** — in `src/aegis/tui/widgets.py` replace the
  `StatusBar` class body's `__init__`, add `set_metrics`, and update
  `_refresh`. The class currently is:

```python
class StatusBar(Static):
    """`<agent> · <model> · <permission>` left, state label right."""

    def __init__(self, agent_name: str, model: str, permission: str) -> None:
        super().__init__(markup=True)
        self._identity = f"{agent_name} · {model} · {permission}"
        self._state = AgentState.ready

    def on_mount(self) -> None:
        self._refresh()

    def set_state(self, state: AgentState) -> None:
        self._state = state
        self._refresh()

    def _refresh(self) -> None:
        self.update(f"{self._identity}    {self._state.label}")
```

Replace it entirely with:

```python
class StatusBar(Static):
    """`<agent> · <model> · <permission>`, state label, then metrics."""

    def __init__(self, agent_name: str, model: str, permission: str) -> None:
        super().__init__(markup=True)
        self._identity = f"{agent_name} · {model} · {permission}"
        self._state = AgentState.ready
        self._metrics = ""

    def on_mount(self) -> None:
        self._refresh()

    def set_state(self, state: AgentState) -> None:
        self._state = state
        self._refresh()

    def set_metrics(self, text: str) -> None:
        self._metrics = text
        self._refresh()

    def _refresh(self) -> None:
        line = f"{self._identity}    {self._state.label}"
        if self._metrics:
            line += f"    {self._metrics}"
        self.update(line)
```

- [ ] **Step 4: Wire `AegisApp`** — in `src/aegis/tui/app.py`:

(a) Add imports. The current event import line is
`from aegis.events import Result`. Replace with:

```python
import time

from aegis.events import Result, ToolResult, ToolUse
from aegis.tui.metrics import SessionMetrics
```

(Place `import time` with the stdlib imports at the top; keep existing
`from rich...`/`from textual...`/`from aegis...` imports.)

(b) Add a clock helper and metrics refresh as methods on `AegisApp` (anywhere
among the helper methods, e.g. just after `_set_state`):

```python
    def _now(self) -> float:
        return time.monotonic()

    def _refresh_metrics(self) -> None:
        self.query_one(StatusBar).set_metrics(
            self._metrics.render(self._now())
        )

    def _tick(self) -> None:
        self._refresh_metrics()
```

(c) In `on_mount`, after `self._set_state(AgentState.ready)` and before
`self.query_one(Input).focus()`, add:

```python
        self._metrics = SessionMetrics(self._now())
        self.set_interval(1.0, self._tick)
```

(d) In `on_input_submitted`, the body currently is:

```python
        inp = self.query_one(Input)
        inp.value = ""
        inp.disabled = True
        self._write(Text.assemble(("› ", "bold"), text))
        self._set_state(AgentState.working)
        self.run_worker(self._run_turn(text), group="turn",
                        exclusive=True)
```

Insert `self._metrics.start_turn(self._now())` immediately before the
`self.run_worker(...)` call:

```python
        inp = self.query_one(Input)
        inp.value = ""
        inp.disabled = True
        self._write(Text.assemble(("› ", "bold"), text))
        self._set_state(AgentState.working)
        self._metrics.start_turn(self._now())
        self.run_worker(self._run_turn(text), group="turn",
                        exclusive=True)
```

(e) In `_run_turn`, the loop currently is:

```python
            async for ev in self._session.events():
                renderable = render_event(ev)
                if renderable is not None:
                    self._write(renderable)
                if isinstance(ev, Result):
                    saw_result = True
                    self._finish(error=ev.is_error)
```

Replace that loop body with:

```python
            async for ev in self._session.events():
                renderable = render_event(ev)
                if renderable is not None:
                    self._write(renderable)
                if isinstance(ev, ToolUse):
                    self._metrics.record_tool()
                elif isinstance(ev, ToolResult) and ev.is_error:
                    self._metrics.record_tool_error()
                if isinstance(ev, Result):
                    self._metrics.end_turn(ev, self._now())
                    saw_result = True
                    self._finish(error=ev.is_error)
                self._refresh_metrics()
```

(f) In `action_interrupt`, the body currently is:

```python
        self.workers.cancel_group(self, "turn")
        self._write(Text("^C — interrupted", style="dim"))
        self._set_state(AgentState.ready)
        inp = self.query_one(Input)
        inp.disabled = False
        inp.focus()
```

Insert `self._metrics.cancel_turn(self._now())` and a refresh:

```python
        self.workers.cancel_group(self, "turn")
        self._metrics.cancel_turn(self._now())
        self._write(Text("^C — interrupted", style="dim"))
        self._set_state(AgentState.ready)
        self._refresh_metrics()
        inp = self.query_one(Input)
        inp.disabled = False
        inp.focus()
```

Leave `_finish`, `action_quit`, `compose`, `_transcript_has` unchanged.

- [ ] **Step 5: Run the pilot test, expect pass**

Run: `uv run pytest tests/test_tui.py::test_status_metrics_render_and_tick -q`
Expected: PASS. If flaky on worker timing, add one more `await pilot.pause()`
after the existing two (timing only — do not change app behavior).

- [ ] **Step 6: Full fast suite green**

Run: `uv run pytest -q -k "not live"`
Expected: all pass (existing TUI tests still green — `set_metrics` is additive;
`StatusBar` with no metrics renders exactly as before).

- [ ] **Step 7: Commit**

```bash
git add src/aegis/tui/widgets.py src/aegis/tui/app.py tests/test_tui.py
git -c user.name="Alejandro Piad" -c user.email="apiad@apiad.net" commit -m "feat(tui): live session metrics in the status bar"
```

---

### Task 4: Folded cleanup — resend-transition test + spec note

**Files:** Modify `tests/test_tui.py`; Modify `docs/superpowers/specs/2026-05-18-aegis-tui-design.md`

- [ ] **Step 1: Add the `error → working → ready` resend pilot test** — append
  to `tests/test_tui.py`:

```python
class ErrorThenOkSession:
    """First turn errors (no Result), second turn succeeds."""

    def __init__(self):
        self.sent = []
        self.started = self.closed = False

    async def start(self):
        self.started = True

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        from aegis.events import AssistantText, Result
        if len(self.sent) == 1:
            raise RuntimeError("first turn blows up")
        yield AssistantText("recovered")
        yield Result(duration_ms=1, is_error=False)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_error_then_resend_recovers_to_ready():
    app = AegisApp(ErrorThenOkSession(), _agent(), "default")
    async with app.run_test() as pilot:
        inp = app.query_one(Input)
        inp.value = "first"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert app.state is AgentState.error      # first turn errored
        inp = app.query_one(Input)
        inp.value = "second"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert app._transcript_has("recovered")
        assert app.state is AgentState.ready      # resend cleared the error
```

- [ ] **Step 2: Run, expect pass**

Run: `uv run pytest tests/test_tui.py::test_error_then_resend_recovers_to_ready -q`
Expected: PASS (the existing `on_input_submitted` only blocks when state is
`working`, so an `error` state still accepts a resend → `working` → `ready`).
If it FAILS because submit is blocked in `error` state, that is a real spec
bug — STOP and report it rather than weakening the test.

- [ ] **Step 3: Add the `⚠ error` label note to the TUI spec** — in
  `docs/superpowers/specs/2026-05-18-aegis-tui-design.md`, find the
  "Tab-state signalling" section's table row for `error`
  (`| `error` | 🔴 red | `⚠ <message>` | …`). Immediately after that table,
  add this line:

```
> Implementation note: the status-bar error label is the generic `⚠ error`;
> the specific failure message (`⚠ harness error` / `⚠ harness exited`)
> appears in the transcript, not the status bar. Intentional simplification.
```

- [ ] **Step 4: Full fast suite green**

Run: `uv run pytest -q -k "not live"`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_tui.py docs/superpowers/specs/2026-05-18-aegis-tui-design.md
git -c user.name="Alejandro Piad" -c user.email="apiad@apiad.net" commit -m "test(tui): cover error→resend→ready; docs: note generic error label"
```

---

## Self-Review

**Spec coverage:**
- `Result` token fields parsed from `usage` → Task 1.
- Cumulative tokens/tools, turn-vs-session time, interrupt freezes turn, missing
  usage → 0 → Task 2 (`SessionMetrics` + its tests).
- Humanization (`999`/`1k`/`1.2k`/`1.0M`), `(N err)` hidden at zero, time
  `Xs`/`Mm SSs`, pre-first-turn `0s` → Task 2 render + tests.
- StatusBar third segment, display-only, empty omitted → Task 3 Step 3.
- App owns metrics, `_now()` seam, 1s `set_interval` tick, hooks for
  ToolUse/ToolResult/Result, start_turn on submit, cancel_turn on interrupt →
  Task 3 Step 4.
- Live pilot test asserts suffix + tick advances time → Task 3 Step 1.
- `events.py` token tests incl. fixtures-still-parse → Task 1.
- Folded cleanup: resend-transition test + `⚠ error` spec note → Task 4.

**Placeholder scan:** none — all code complete. The only conditional
instruction (`field` import) is explicit and harmless.

**Type consistency:** `Result(duration_ms, is_error, input_tokens=None,
output_tokens=None)`; `SessionMetrics(session_start, …)` methods
`start_turn`/`record_tool`/`record_tool_error`/`end_turn`/`cancel_turn`/
`turn_seconds`/`session_seconds`/`render` — used identically in Task 2 tests
and Task 3 app wiring. `StatusBar.set_metrics(text)` matches the app's
`_refresh_metrics`. `app._now`/`app._metrics`/`app._tick` names match the
pilot test in Task 3. `ToolUse`/`ToolResult` imported in both `app.py` and the
Task-3 test.

---

## Execution notes

- Strict order Task 1 → 4; each commit keeps `uv run pytest -q -k "not live"`
  green. Four commits.
- Tests written/validated inline by the implementer; the live `claude`
  driver test is untouched.
- Commit directly to `main` (authorized for aegis); pushing is pre-authorized.
