# `/loop` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/loop <text>` arms a looping instruction on a session; it re-fires at every turn boundary until the agent reaps it with `aegis_loop_stop`, the cap is hit, the operator stops it, the turn is interrupted, or the harness errors.

**Architecture:** A fourth and lowest rung on `AgentSession._chain_if_pending`'s existing ladder (`_inbox_buffer` → unsolicited drain → `_reminders` → **loop** → idle). A thin `LoopService` gives the MCP and slash-command planes one session-lookup surface, mirroring `ReminderService`. Nothing persists; a loop dies with its session.

**Tech Stack:** Python 3.13, `uv`, pytest + pytest-asyncio, Textual 8.2.6, FastMCP.

**Spec:** `docs/superpowers/specs/2026-07-23-aegis-loop-command-design.md`

## Global Constraints

- Package manager is `uv`. Run tests with `uv run python -m pytest`, never bare `pytest`.
- Run pytest as its **own step** and check the exit code. Never pipe it into `tail`/`head` in an `&&` chain — that masks the exit status.
- Code, comments, identifiers and commit messages in **English**.
- Commit straight to `main`. No feature branch, no PR.
- This repo's checkout is shared with parallel agents. Commit with explicit paths (`git commit -- <paths>`), never `git add -A`.
- Default cap is **20** iterations.
- Ruff reports one **pre-existing** error, `F821 Undefined name 'Workspace'` at `src/aegis/tui/app.py:132`. It is not yours; do not fix it, and do not treat it as a regression.
- `tests/tui/`, `tests/test_sysmeter.py` and `tests/test_terminal_tab.py` leak Textual theme state, so suites running after them can fail with `UnresolvedVariableError: reference to undefined variable '$background'`. Pre-existing. If you see it, re-run the affected file alone to confirm.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/aegis/queue/schema.py` | add `sender_loop(iteration, total)` next to `sender_reminder()` |
| `src/aegis/core/loop.py` | **new** — the `LoopState` dataclass and its prompt rendering |
| `src/aegis/core/session.py` | loop slot, the firing tier, arm/stop, termination edges |
| `src/aegis/queue/loop.py` | **new** — `LoopService`, the handle→session shim for MCP + commands |
| `src/aegis/queue/__init__.py` | export `LoopService`, `sender_loop` |
| `src/aegis/mcp/bridge.py` | `loop_service` on the `AppBridge` protocol |
| `src/aegis/core/manager.py` | construct/attach `LoopService` (headless) |
| `src/aegis/tui/app.py` | `get()` (bug fix), construct `LoopService` (TUI) |
| `src/aegis/mcp/server.py` | `aegis_loop_stop` tool + BRIEFING entry |
| `src/aegis/commands/builtins/loop.py` | **new** — the `/loop` command |
| `src/aegis/commands/builtins/__init__.py` | import the new module for its registration side-effect |
| `src/aegis/tui/widgets.py` | StatusBar loop segment |
| `src/aegis/tui/pane.py` | subscribe to loop changes, drive the segment |
| `tests/test_loop.py` | **new** — core, service, MCP and command coverage |

---

### Task 1: Fix `AgentSession` lookup on the TUI bridge

`ReminderService._session_for` does `getattr(self._sm, "get", None)`. In the
TUI the `_sm` is the `AegisApp` itself (`src/aegis/tui/app.py:284` —
`ReminderService(self.inbox_router, self)`), and **`AegisApp` has no `get`
method**; neither does Textual's `App`. So the lookup silently returns `None`
and turn-end `aegis_remind` answers `{"error": "no live session for handle …"}`
in the TUI. It works in `aegis serve`, where `SessionManager.get` exists —
which is the path the feature was smoke-tested on.

`/loop` needs the same lookup, so fix it first rather than inheriting it.
`AegisApp.pane_for(handle)` already finds the pane; the session is its `_core`.

**Files:**
- Modify: `src/aegis/tui/app.py` (add a method to `AegisApp`, near `pane_for` at line 1135)
- Test: `tests/test_loop.py` (create)

**Interfaces:**
- Produces: `AegisApp.get(handle: str) -> AgentSession | None` — the bridge-side handle→session lookup that `ReminderService` and (Task 4) `LoopService` both consume.

- [ ] **Step 1: Write the failing test**

Create `tests/test_loop.py`:

```python
"""/loop — a self-re-arming turn-end instruction.

Layers, mirroring tests/test_reminder.py:
- the loop tier on AgentSession (lowest rung of _chain_if_pending)
- LoopService (handle -> session routing)
- the aegis_loop_stop MCP tool
- the /loop slash command
"""
from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.core.session import AgentSession
from aegis.events import AssistantText, Result
from aegis.tui.app import AegisApp


# --------------------------------------------------------------------------
# Fakes (same shapes as tests/test_reminder.py)
# --------------------------------------------------------------------------
class FakeHarness:
    def __init__(self, events_per_turn=None):
        self._turns = list(events_per_turn or [])
        self.started = False
        self.closed = False
        self.sent: list[str] = []
        self.session_id = None

    async def start(self):
        self.started = True

    async def send(self, t):
        self.sent.append(t)

    async def close(self):
        self.closed = True

    async def events(self):
        evs = self._turns.pop(0) if self._turns else []
        for e in evs:
            await asyncio.sleep(0)
            yield e


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge):
        self.bound = bridge

    async def start(self):
        pass

    async def stop(self):
        pass


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


def _turn(text):
    return [AssistantText(text=text),
            Result(duration_ms=1, is_error=False, usage=None)]


def _factory(agent, mcp_url, handle):
    return FakeHarness()


# --------------------------------------------------------------------------
# Task 1 — bridge session lookup
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_app_get_returns_agent_session(tmp_path, monkeypatch):
    """AegisApp.get(handle) -> AgentSession. Without it the TUI's
    ReminderService (and LoopService) silently find no session and every
    turn-end delivery errors out."""
    monkeypatch.chdir(tmp_path)
    app = AegisApp({"default": _agent()}, "default", _factory, FakeMCP(),
                   cwd=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        handle = app._active.handle
        session = app.get(handle)
        assert isinstance(session, AgentSession)
        assert session.handle == handle
        assert app.get("no-such-handle") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_loop.py::test_app_get_returns_agent_session -v`
Expected: FAIL — `AttributeError: 'AegisApp' object has no attribute 'get'`

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/tui/app.py`, immediately after the `pane_for` method (line 1135):

```python
    def get(self, handle: str):
        """AgentSession for a handle, or None.

        The bridge-side lookup ReminderService / LoopService use
        (`getattr(sm, "get", None)`). SessionManager has it for the headless
        path; AegisApp is the TUI's own bridge and needs it too, or every
        turn-end delivery silently reports "no live session".
        """
        pane = self.pane_for(handle)
        return pane._core if pane is not None else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_loop.py::test_app_get_returns_agent_session -v`
Expected: PASS

- [ ] **Step 5: Confirm the reminder bug is fixed too**

Run: `uv run python -m pytest tests/test_reminder.py -q`
Expected: PASS (no regressions; the existing suite covers the headless path)

- [ ] **Step 6: Commit**

```bash
git add tests/test_loop.py
git commit -m "fix(tui): AegisApp.get — turn-end reminders never found their session

ReminderService._session_for does getattr(sm, \"get\", None); in the TUI the
sm is the AegisApp, which had no get() (nor does Textual's App), so every
turn-end aegis_remind answered \"no live session\". Only aegis serve worked,
which is the path the feature was smoke-tested on." -- src/aegis/tui/app.py tests/test_loop.py
```

---

### Task 2: `LoopState` and the firing tier

**Files:**
- Create: `src/aegis/core/loop.py`
- Modify: `src/aegis/queue/schema.py` (add `sender_loop`, next to `sender_reminder` at line 32)
- Modify: `src/aegis/queue/__init__.py` (export `sender_loop`)
- Modify: `src/aegis/core/session.py` (`__init__`, `_chain_if_pending`, new methods)
- Test: `tests/test_loop.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `LoopState(text: str, iteration: int = 0, max_iterations: int = 20)` with `render(handle: str) -> str`
  - `sender_loop(iteration: int, total: int) -> str`
  - `AgentSession.arm_loop(text: str, max_iterations: int = 20) -> None`
  - `AgentSession.stop_loop(reason: str = "stopped") -> bool`
  - `AgentSession.loop_status() -> dict | None`
  - `AgentSession.on_loop: Callable[[AgentSession, LoopState | None, str], None] | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loop.py`:

```python
# --------------------------------------------------------------------------
# Task 2 — the loop tier on AgentSession
# --------------------------------------------------------------------------
from aegis.core.loop import LoopState          # noqa: E402
from aegis.queue import InboxMessage, sender_loop   # noqa: E402
from aegis.tui.state import AgentState          # noqa: E402


def _inbox(body):
    return InboxMessage(sender="queue:impl", timestamp="2026-07-23T00:00:00Z",
                        body=body, task_id="01J", status="ok")


def _remind(body):
    from aegis.queue import sender_reminder
    return InboxMessage(sender=sender_reminder(),
                        timestamp="2026-07-23T00:00:00Z", body=body)


async def _settle(session):
    """Let every chained turn run to completion."""
    for _ in range(50):
        await asyncio.sleep(0)
        if session.state is not AgentState.working:
            return


def test_sender_loop_renders_iteration():
    assert sender_loop(3, 20) == "loop · iteration 3/20"


def test_loop_state_render_includes_text_and_stop_tool():
    ls = LoopState(text="fix the tests")
    body = ls.render("witty-wirth")
    assert "fix the tests" in body
    assert "aegis_loop_stop" in body
    assert "witty-wirth" in body


@pytest.mark.asyncio
async def test_loop_refires_at_turn_end_and_counts():
    harness = FakeHarness([_turn("a"), _turn("b"), _turn("c")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("keep going", max_iterations=3)
    await _settle(s)
    # Armed while idle -> promoted immediately, then re-fires to the cap.
    assert len(harness.sent) == 3
    assert all("keep going" in t for t in harness.sent)
    assert s.loop_status() is None          # cleared by the cap


@pytest.mark.asyncio
async def test_loop_header_carries_iteration():
    harness = FakeHarness([_turn("a")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("keep going", max_iterations=1)
    await _settle(s)
    assert "> from loop · iteration 1/1" in harness.sent[0]


@pytest.mark.asyncio
async def test_inbox_message_preempts_the_loop():
    """Tier order: a buffered inbox message dispatches before the loop."""
    harness = FakeHarness([_turn("a"), _turn("b"), _turn("c")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("LOOPTEXT", max_iterations=2)
    await asyncio.sleep(0)          # first loop turn is in flight
    await s.deliver(_inbox("INBOXTEXT"))
    await _settle(s)
    order = ["INBOX" if "INBOXTEXT" in t else "LOOP" for t in harness.sent]
    assert order[0] == "LOOP"       # armed-while-idle fired first
    assert order[1] == "INBOX"      # then the inbox, ahead of the next loop
    assert order[2] == "LOOP"


@pytest.mark.asyncio
async def test_reminder_preempts_the_loop():
    harness = FakeHarness([_turn("a"), _turn("b"), _turn("c")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("LOOPTEXT", max_iterations=2)
    await asyncio.sleep(0)
    s.add_reminder(_remind("REMINDTEXT"))
    await _settle(s)
    assert "REMINDTEXT" in harness.sent[1]


@pytest.mark.asyncio
async def test_stop_loop_prevents_the_next_iteration():
    harness = FakeHarness([_turn("a"), _turn("b")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("keep going", max_iterations=10)
    await asyncio.sleep(0)
    assert s.stop_loop("agent says done") is True
    await _settle(s)
    assert len(harness.sent) == 1
    assert s.loop_status() is None
    assert s.stop_loop("again") is False      # idempotent


@pytest.mark.asyncio
async def test_loop_status_reports_progress():
    harness = FakeHarness([_turn("a"), _turn("b"), _turn("c")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("keep going", max_iterations=5)
    await asyncio.sleep(0)
    st = s.loop_status()
    assert st["text"] == "keep going"
    assert st["max_iterations"] == 5
    assert st["iteration"] >= 1
    s.stop_loop()


@pytest.mark.asyncio
async def test_arming_twice_replaces():
    harness = FakeHarness([_turn("a"), _turn("b")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("first", max_iterations=10)
    await asyncio.sleep(0)
    s.arm_loop("second", max_iterations=10)
    st = s.loop_status()
    assert st["text"] == "second"
    assert st["iteration"] == 0
    s.stop_loop()


@pytest.mark.asyncio
async def test_cap_fires_the_observer_with_a_capped_reason():
    seen = []
    harness = FakeHarness([_turn("a")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.on_loop = lambda sess, state, reason: seen.append((state, reason))
    s.arm_loop("keep going", max_iterations=1)
    await _settle(s)
    reasons = [r for _, r in seen]
    assert any("capped" in r for r in reasons)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.core.loop'`

- [ ] **Step 3: Create `src/aegis/core/loop.py`**

```python
"""LoopState — the operator's looping instruction.

`/loop <text>` arms one of these on an AgentSession. It is re-delivered at
every turn boundary at which the session would otherwise settle idle, until
the agent reaps it (`aegis_loop_stop`), the cap is reached, the operator
stops it, the turn is interrupted, or the harness errors.

In-memory and session-scoped by design: a loop does not survive a restart.
Auto-firing a restored loop would mean a cold TUI starts spending tokens at
boot without anyone asking it to.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MAX_ITERATIONS = 20

_CODA = (
    "\n\nIf this instruction is now fully satisfied, call "
    "aegis_loop_stop(from_handle='{handle}', reason='<why>') and stop. "
    "Otherwise continue."
)


@dataclass
class LoopState:
    """One armed loop. ``iteration`` counts deliveries and is incremented as
    the turn is dispatched, so the Nth delivery reads ``iteration N/max``."""

    text: str
    iteration: int = 0
    max_iterations: int = DEFAULT_MAX_ITERATIONS

    def exhausted(self) -> bool:
        return self.iteration >= self.max_iterations

    def render(self, handle: str) -> str:
        """The body delivered to the agent: the instruction verbatim, plus
        the stop coda. Verbatim matters — the previous turn may have ended
        somewhere unhelpful, and the instruction has to be present in the
        turn that acts on it."""
        return self.text + _CODA.format(handle=handle)

    def status(self) -> dict:
        return {"text": self.text, "iteration": self.iteration,
                "max_iterations": self.max_iterations}
```

- [ ] **Step 4: Add `sender_loop` to `src/aegis/queue/schema.py`**

Immediately after `sender_reminder()` (which ends at line 36):

```python
def sender_loop(iteration: int, total: int) -> str:
    """Sender tag for one iteration of an armed `/loop`.

    Carries the counter so `render_inbox_header` produces
    `> from loop · iteration 3/20 · <ts>` with no special-casing.
    """
    return f"loop · iteration {iteration}/{total}"
```

- [ ] **Step 5: Export it from `src/aegis/queue/__init__.py`**

Add `sender_loop,` to the `from aegis.queue.schema import (...)` block (alphabetically, before `sender_queue`) and `"sender_loop",` to `__all__` (before `"sender_queue"`).

- [ ] **Step 6: Wire the loop into `src/aegis/core/session.py`**

Add to the imports at the top:

```python
from aegis.core.loop import DEFAULT_MAX_ITERATIONS, LoopState
```

Extend the schema import on line 19 to pull in what the tier needs:

```python
from aegis.queue.schema import (
    Delivery, InboxMessage, now_iso, render_inbox_header, sender_loop,
)
```

Add the observer type alias next to `CloseCb` (line 29):

```python
LoopCb = Callable[["AgentSession", "LoopState | None", str], None]
```

In `__init__`, immediately after the `self._reminders` block (line 74):

```python
        # The operator's `/loop` instruction, or None. Drained by
        # _chain_if_pending as the LOWEST tier of all — below reminders —
        # and re-armed rather than consumed. See aegis/core/loop.py.
        self._loop: LoopState | None = None
```

And next to the other observer slots (after `self.on_close`, line 113):

```python
        # Fired on arm / fire / stop so a frontend can render the loop chip
        # and announce termination. (session, state_or_None, reason).
        self.on_loop: LoopCb | None = None
```

Add the public methods next to `add_reminder` (after line 280):

```python
    def arm_loop(self, text: str,
                 max_iterations: int = DEFAULT_MAX_ITERATIONS) -> None:
        """Arm (or replace) this session's looping instruction.

        Armed while idle nothing else would poke the session, so promote the
        chain now — `/loop <text>` should start working, not wait for the
        next unrelated turn.
        """
        self._loop = LoopState(text=text, max_iterations=max_iterations)
        self._emit_loop("armed")
        if self.state is not AgentState.working:
            self._chain_if_pending()

    def stop_loop(self, reason: str = "stopped") -> bool:
        """Reap the loop. Returns False when nothing was armed, so a double
        stop is harmless."""
        if self._loop is None:
            return False
        self._loop = None
        self._emit_loop(reason)
        return True

    def loop_status(self) -> dict | None:
        return self._loop.status() if self._loop is not None else None

    def _emit_loop(self, reason: str) -> None:
        if self.on_loop is not None:
            self.on_loop(self, self._loop, reason)
```

In `_chain_if_pending`, replace the final two lines (`self._unsolicited = False` / `self._arm_idle_watcher()`, lines 465-466) with the loop tier followed by them:

```python
        # Lowest tier of all: the operator's looping instruction. Everything
        # else — inbox, unsolicited drain, reminders — has already had its
        # turn, so nothing is starved behind a loop.
        if self._loop is not None:
            if self._loop.exhausted():
                self.stop_loop(
                    f"capped at {self._loop.max_iterations} iterations "
                    f"— the agent did not stop it")
            else:
                self._loop.iteration += 1
                msg = InboxMessage(
                    sender=sender_loop(self._loop.iteration,
                                       self._loop.max_iterations),
                    timestamp=now_iso(),
                    body=self._loop.render(self.handle))
                self._emit_dispatch([msg])
                self._emit_loop("fired")
                self._emit_state(AgentState.working, finished=False)
                self.metrics.start_turn(self._now())
                self._task = asyncio.create_task(
                    self._run_turn(_render_batch([msg])))
                return
        self._unsolicited = False  # settling idle — no turn in flight
        self._arm_idle_watcher()
```

- [ ] **Step 7: Run the tests**

Run: `uv run python -m pytest tests/test_loop.py -v`
Expected: PASS (all Task 1 + Task 2 tests)

- [ ] **Step 8: Run the neighbouring suites for regressions**

Run: `uv run python -m pytest tests/test_reminder.py tests/test_queue_inbox.py tests/test_queue_session_deliver.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/aegis/core/loop.py
git commit -m "feat(loop): LoopState and the turn-boundary firing tier

The lowest rung of _chain_if_pending's ladder: inbox, unsolicited drain and
reminders all dispatch ahead of it, so nothing starves behind a loop. Fires
only when the session would otherwise settle idle, re-arming rather than
being consumed. sender_loop carries the counter so the existing header
renderer produces '> from loop · iteration 3/20 · <ts>' unchanged." -- src/aegis/core/loop.py src/aegis/core/session.py src/aegis/queue/schema.py src/aegis/queue/__init__.py tests/test_loop.py
```

---

### Task 3: Termination edges — monitor gate, interrupt, harness error

**Files:**
- Modify: `src/aegis/core/session.py` (`_chain_if_pending`, `interrupt`, both `except Exception` blocks)
- Test: `tests/test_loop.py`

**Interfaces:**
- Consumes: `AgentSession.arm_loop` / `stop_loop` / `loop_status` and `_loop` from Task 2.
- Produces: no new public names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loop.py`:

```python
# --------------------------------------------------------------------------
# Task 3 — termination edges
# --------------------------------------------------------------------------
class HoldHarness(FakeHarness):
    """Never reports pending events; used with _unsolicited_hold set by hand."""


@pytest.mark.asyncio
async def test_loop_yields_to_an_armed_monitor():
    """_unsolicited_hold > 0 means an aegis monitor is the authoritative
    waker. Firing underneath it turns `/loop run the tests` into a spin loop:
    the agent would burn whole turns asking 'done yet?' while the monitor sits
    there waiting to wake it."""
    harness = HoldHarness([_turn("a"), _turn("b")])
    s = AgentSession(harness, _agent(), "default", "h")
    s._unsolicited_hold = 1
    s.arm_loop("keep going", max_iterations=5)
    await _settle(s)
    assert harness.sent == []                       # suppressed
    assert s.loop_status()["iteration"] == 0        # counter did not advance
    # Releasing the hold lets it fire.
    s._unsolicited_hold = 0
    s._chain_if_pending()
    await _settle(s)
    assert len(harness.sent) == 1
    s.stop_loop()


@pytest.mark.asyncio
async def test_interrupt_clears_the_loop():
    """Without this Esc is useless — the loop re-fires the instant the
    interrupted turn ends."""
    harness = FakeHarness([_turn("a"), _turn("b"), _turn("c")])
    s = AgentSession(harness, _agent(), "default", "h")
    s.arm_loop("keep going", max_iterations=10)
    await asyncio.sleep(0)
    await s.interrupt()
    assert s.loop_status() is None


class BoomHarness(FakeHarness):
    async def events(self):
        raise RuntimeError("harness exploded")
        yield  # pragma: no cover — makes this an async generator


@pytest.mark.asyncio
async def test_harness_error_stops_the_loop():
    """Otherwise a broken session spins on its own error forever."""
    s = AgentSession(BoomHarness(), _agent(), "default", "h")
    s.arm_loop("keep going", max_iterations=10)
    await _settle(s)
    assert s.loop_status() is None
    assert s.last_error is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_loop.py -k "monitor or interrupt or harness_error" -v`
Expected: FAIL — the loop fires under the hold, survives interrupt, and survives the error.

- [ ] **Step 3: Gate the tier on `_unsolicited_hold`**

In `_chain_if_pending`, change the loop-tier condition written in Task 2 from:

```python
        if self._loop is not None:
```

to:

```python
        # Yields to an armed aegis monitor: the monitor is the authoritative
        # waker, and re-firing underneath it would spin. The counter does not
        # advance on a suppressed fire.
        if self._loop is not None and self._unsolicited_hold == 0:
```

- [ ] **Step 4: Clear the loop on interrupt**

In `interrupt()` (line 550), insert as the very first statement of the method, before `await self._cancel_idle_watcher()`:

```python
        # Interrupt means stop. Without this the loop re-fires the instant the
        # interrupted turn ends and Esc can never escape it.
        self.stop_loop("interrupted")
```

- [ ] **Step 5: Stop the loop on a harness error**

In `_run_turn`'s `except Exception as e:` block, immediately after `self.last_error = e` (line 391):

```python
            self.stop_loop("stopped after a harness error")
```

And in `_drain_unsolicited_turn`'s `except Exception as e:` block, immediately after `self.last_error = e` (line 498):

```python
            self.stop_loop("stopped after a harness error")
```

- [ ] **Step 6: Run the tests**

Run: `uv run python -m pytest tests/test_loop.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git commit -m "feat(loop): monitor gate, interrupt and harness-error termination

Three ways a loop must stop that aren't the agent reaping it. The monitor
gate is the one that matters most: without it, /loop composed with
aegis_monitor spins, burning turns asking 'done yet?' while the monitor
waits to wake the agent." -- src/aegis/core/session.py tests/test_loop.py
```

---

### Task 4: `LoopService` and bridge wiring

**Files:**
- Create: `src/aegis/queue/loop.py`
- Modify: `src/aegis/queue/__init__.py`, `src/aegis/mcp/bridge.py`, `src/aegis/core/manager.py`, `src/aegis/tui/app.py`
- Test: `tests/test_loop.py`

**Interfaces:**
- Consumes: `AgentSession.arm_loop / stop_loop / loop_status` (Task 2), `AegisApp.get` (Task 1).
- Produces: `LoopService(session_manager)` with `arm(from_handle, text, max_iterations) -> dict`, `stop(from_handle, reason) -> dict`, `status(from_handle) -> dict`. Bridge attribute name: `loop_service`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loop.py`:

```python
# --------------------------------------------------------------------------
# Task 4 — LoopService
# --------------------------------------------------------------------------
from aegis.queue import LoopService          # noqa: E402


class FakeSM:
    def __init__(self, sessions):
        self._by_handle = {s.handle: s for s in sessions}

    def get(self, handle):
        return self._by_handle.get(handle)


@pytest.mark.asyncio
async def test_service_arm_routes_to_session():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    res = svc.arm(from_handle="h", text="keep going", max_iterations=4)
    assert res["armed"] is True
    assert res["max_iterations"] == 4
    assert s.loop_status()["text"] == "keep going"
    s.stop_loop()


def test_service_unknown_handle_errors():
    svc = LoopService(FakeSM([]))
    assert "error" in svc.arm(from_handle="nope", text="x")
    assert "error" in svc.stop(from_handle="nope")
    assert "error" in svc.status(from_handle="nope")


@pytest.mark.asyncio
async def test_service_stop_and_status():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    svc.arm(from_handle="h", text="keep going", max_iterations=9)
    assert svc.status(from_handle="h")["loop"]["max_iterations"] == 9
    assert svc.stop(from_handle="h", reason="done")["stopped"] is True
    assert svc.status(from_handle="h")["loop"] is None
    assert svc.stop(from_handle="h")["stopped"] is False


def test_service_rejects_bad_max_iterations():
    svc = LoopService(FakeSM([]))
    assert "error" in svc.arm(from_handle="h", text="x", max_iterations=0)
    assert "error" in svc.arm(from_handle="h", text="x", max_iterations=-3)


def test_service_rejects_empty_text():
    svc = LoopService(FakeSM([]))
    assert "error" in svc.arm(from_handle="h", text="   ")


@pytest.mark.asyncio
async def test_tui_bridge_exposes_loop_service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AegisApp({"default": _agent()}, "default", _factory, FakeMCP(),
                   cwd=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.loop_service, LoopService)
        handle = app._active.handle
        assert app.loop_service.arm(
            from_handle=handle, text="keep going")["armed"] is True
        app.loop_service.stop(from_handle=handle)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_loop.py -k service -v`
Expected: FAIL — `ImportError: cannot import name 'LoopService'`

- [ ] **Step 3: Create `src/aegis/queue/loop.py`**

```python
"""LoopService — handle→session routing for `/loop`.

The loop itself lives on ``AgentSession`` (``aegis/core/loop.py`` and the
tier in ``_chain_if_pending``). This service is the thin surface the MCP
plane and the slash-command plane share, mirroring ``ReminderService``: it
resolves a handle to a live session and validates arguments, nothing more.

No timers and no persistence — a loop dies with its session.
"""
from __future__ import annotations

from aegis.core.loop import DEFAULT_MAX_ITERATIONS


class LoopService:
    def __init__(self, session_manager=None) -> None:
        self._sm = session_manager

    def _session_for(self, handle: str):
        get = getattr(self._sm, "get", None)
        return get(handle) if callable(get) else None

    def arm(self, *, from_handle: str, text: str,
            max_iterations: int = DEFAULT_MAX_ITERATIONS) -> dict:
        if not text or not text.strip():
            return {"error": "loop text is empty"}
        try:
            max_iterations = int(max_iterations)
        except (TypeError, ValueError):
            return {"error": f"max must be an integer: {max_iterations!r}"}
        if max_iterations < 1:
            return {"error": f"max must be >= 1: {max_iterations}"}
        session = self._session_for(from_handle)
        if session is None:
            return {"error": f"no live session for handle {from_handle!r}"}
        session.arm_loop(text.strip(), max_iterations)
        return {"armed": True, "text": text.strip(),
                "max_iterations": max_iterations}

    def stop(self, *, from_handle: str, reason: str = "stopped") -> dict:
        session = self._session_for(from_handle)
        if session is None:
            return {"error": f"no live session for handle {from_handle!r}"}
        return {"stopped": session.stop_loop(reason), "reason": reason}

    def status(self, *, from_handle: str) -> dict:
        session = self._session_for(from_handle)
        if session is None:
            return {"error": f"no live session for handle {from_handle!r}"}
        return {"loop": session.loop_status()}
```

- [ ] **Step 4: Export it from `src/aegis/queue/__init__.py`**

Add `from aegis.queue.loop import LoopService` next to the `ReminderService` import, and `"LoopService",` to `__all__` (alphabetically, after `"InboxRouter"`).

- [ ] **Step 5: Declare it on the bridge protocol**

In `src/aegis/mcp/bridge.py`, in the `AppBridge` attribute block, immediately after the `reminder_service` line:

```python
    loop_service: object         # LoopService
```

- [ ] **Step 6: Construct it on both bridges**

In `src/aegis/core/manager.py`, next to `self.reminder_service = None` (line 43):

```python
        self.loop_service = LoopService(self)
```

and import it at the top of the file: `from aegis.queue import LoopService`.

In `src/aegis/tui/app.py`, next to `self.reminder_service = ReminderService(self.inbox_router, self)` (line 284):

```python
        self.loop_service = LoopService(self)
```

with `LoopService` added to the existing `aegis.queue` import in that module. In the remote-mode branch (line 248, where `reminder_service` falls back to `_DisabledPlaneStub`), add the matching stub:

```python
            self.loop_service = getattr(
                manager, "loop_service", _DisabledPlaneStub("loop_service"))
```

- [ ] **Step 7: Run the tests**

Run: `uv run python -m pytest tests/test_loop.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/aegis/queue/loop.py
git commit -m "feat(loop): LoopService + bridge wiring

The handle-to-session shim the MCP and slash-command planes share, mirroring
ReminderService. Constructed on both bridges so the TUI and aegis serve
behave alike; stubbed in remote mode like the other local-only planes." -- src/aegis/queue/loop.py src/aegis/queue/__init__.py src/aegis/mcp/bridge.py src/aegis/core/manager.py src/aegis/tui/app.py tests/test_loop.py
```

---

### Task 5: `aegis_loop_stop` MCP tool

**Files:**
- Modify: `src/aegis/mcp/server.py` (tool + BRIEFING)
- Test: `tests/test_loop.py`

**Interfaces:**
- Consumes: `bridge.loop_service` (Task 4).
- Produces: MCP tool `aegis_loop_stop(from_handle: str, reason: str = "") -> dict`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loop.py`:

```python
# --------------------------------------------------------------------------
# Task 5 — MCP surface
# --------------------------------------------------------------------------
from aegis.mcp.server import BRIEFING, build_server      # noqa: E402


class StubBridge:
    def __init__(self, svc):
        self.loop_service = svc


@pytest.mark.asyncio
async def test_loop_stop_tool_registered():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    server = build_server(StubBridge(LoopService(FakeSM([s]))))
    tools = await server.get_tools()
    assert "aegis_loop_stop" in tools


@pytest.mark.asyncio
async def test_mcp_loop_stop_reaps():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    svc.arm(from_handle="h", text="keep going")
    server = build_server(StubBridge(svc))
    tools = await server.get_tools()
    res = await tools["aegis_loop_stop"].fn(from_handle="h", reason="green")
    assert res["stopped"] is True
    assert s.loop_status() is None


@pytest.mark.asyncio
async def test_mcp_loop_stop_without_a_loop_is_harmless():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    server = build_server(StubBridge(LoopService(FakeSM([s]))))
    tools = await server.get_tools()
    res = await tools["aegis_loop_stop"].fn(from_handle="h")
    assert res["stopped"] is False


def test_briefing_mentions_loop_stop():
    assert "aegis_loop_stop" in BRIEFING
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_loop.py -k "loop_stop or briefing" -v`
Expected: FAIL — `KeyError: 'aegis_loop_stop'`

- [ ] **Step 3: Add the tool**

In `src/aegis/mcp/server.py`, immediately after the `aegis_remind` function (which ends at line 1086). The decorator is `@server.tool` (line 1059) — not `@mcp.tool`:

```python
    @server.tool
    async def aegis_loop_stop(from_handle: str, reason: str = "") -> dict:
        """Reap the `/loop` the operator armed on your session.

        A loop re-delivers its instruction at every turn boundary where you
        would otherwise settle idle. Call this the moment you judge that
        instruction fully satisfied — you are the only thing that ends a loop
        cleanly. If you don't, it runs until its iteration cap and reports
        that it was capped rather than completed.

        ``from_handle`` is your own aegis handle (from your system prompt).
        ``reason`` is a short note on why you consider it done; it is shown to
        the operator. Calling this with no loop armed is harmless and returns
        ``{"stopped": false}``.
        """
        svc = getattr(bridge, "loop_service", None)
        if svc is None:
            return {"error": "loops not available on this bridge"}
        return svc.stop(from_handle=from_handle,
                        reason=reason or "stopped by the agent")
```

- [ ] **Step 4: Add the BRIEFING entry**

In the `BRIEFING` string, immediately after the `aegis_remind` block (ends line 201 with the `aegis_reminder_cancel` sentence):

```python
    "  - aegis_loop_stop(from_handle, reason?) : reap the `/loop` the "
    "operator armed on your session. A loop re-delivers its instruction "
    "every time you would otherwise go idle; you are the only thing that "
    "ends one cleanly. Call it as soon as the instruction is satisfied — "
    "otherwise the loop runs to its iteration cap and reports as capped, "
    "not completed.\n"
```

- [ ] **Step 5: Run the tests**

Run: `uv run python -m pytest tests/test_loop.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(loop): aegis_loop_stop MCP tool

The agent-side reap, and the only clean way a loop ends. Wording in the tool
doc and BRIEFING leans on that: a capped loop reports as capped, not done." -- src/aegis/mcp/server.py tests/test_loop.py
```

---

### Task 6: the `/loop` slash command

**Files:**
- Create: `src/aegis/commands/builtins/loop.py`
- Modify: `src/aegis/commands/builtins/__init__.py`
- Test: `tests/test_loop.py`

**Interfaces:**
- Consumes: `ctx.bridge.loop_service` (Task 4), `ctx.handle`.
- Produces: registered command `loop`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loop.py`:

```python
# --------------------------------------------------------------------------
# Task 6 — the /loop slash command
# --------------------------------------------------------------------------
from aegis.commands import CommandContext, dispatch      # noqa: E402


def _ctx(svc, handle="h"):
    return CommandContext(bridge=StubBridge(svc), handle=handle)


@pytest.mark.asyncio
async def test_slash_loop_arms():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    res = await dispatch("/loop fix the failing tests", _ctx(svc))
    assert res.ok
    assert s.loop_status()["text"] == "fix the failing tests"
    assert s.loop_status()["max_iterations"] == 20
    s.stop_loop()


@pytest.mark.asyncio
async def test_slash_loop_max_flag():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    res = await dispatch("/loop --max 3 fix the failing tests", _ctx(svc))
    assert res.ok
    assert s.loop_status()["max_iterations"] == 3
    assert s.loop_status()["text"] == "fix the failing tests"
    s.stop_loop()


@pytest.mark.asyncio
async def test_slash_loop_max_inside_text_survives():
    """The greedy positional stops flag parsing, so --max in the instruction
    is part of the instruction."""
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    await dispatch("/loop run bench --max 5 until it clears", _ctx(svc))
    assert "--max 5" in s.loop_status()["text"]
    s.stop_loop()


@pytest.mark.asyncio
async def test_slash_loop_stop_is_exact_match_only():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    # More than the bare word -> an instruction, not the verb.
    await dispatch("/loop stop the dev server and restart it", _ctx(svc))
    assert s.loop_status()["text"] == "stop the dev server and restart it"
    # The bare word -> the verb.
    res = await dispatch("/loop stop", _ctx(svc))
    assert res.ok
    assert s.loop_status() is None


@pytest.mark.asyncio
async def test_slash_loop_status_and_empty_cases():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    res = await dispatch("/loop", _ctx(svc))
    assert res.ok and "no loop" in res.title.lower()
    res = await dispatch("/loop stop", _ctx(svc))
    assert res.ok is False
    await dispatch("/loop keep going", _ctx(svc))
    res = await dispatch("/loop", _ctx(svc))
    assert res.ok and "keep going" in res.body
    s.stop_loop()


@pytest.mark.asyncio
async def test_slash_loop_rejects_bad_max():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    res = await dispatch("/loop --max 0 keep going", _ctx(svc))
    assert res.ok is False
    assert s.loop_status() is None


@pytest.mark.asyncio
async def test_slash_loop_arming_twice_replaces():
    s = AgentSession(FakeHarness([_turn("a")]), _agent(), "default", "h")
    svc = LoopService(FakeSM([s]))
    await dispatch("/loop first", _ctx(svc))
    res = await dispatch("/loop second", _ctx(svc))
    assert res.ok
    assert "replaced" in res.title.lower()
    assert s.loop_status()["text"] == "second"
    s.stop_loop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_loop.py -k slash -v`
Expected: FAIL — `unknown command: /loop`

- [ ] **Step 3: Create `src/aegis/commands/builtins/loop.py`**

```python
"""`/loop` — arm a looping instruction on this pane's session.

The instruction is re-delivered at every turn boundary where the session
would otherwise settle idle, until the agent reaps it with aegis_loop_stop,
the iteration cap is reached, or the operator stops it.
"""
from __future__ import annotations

from aegis.commands import (
    CommandContext, CommandResult, SlashCommand, register,
)
from aegis.commands.args import Arg, ArgSpec, Flag
from aegis.core.loop import DEFAULT_MAX_ITERATIONS


def _describe(status: dict) -> str:
    return (f"{status['text']}\n"
            f"  iteration {status['iteration']}/{status['max_iterations']}")


async def _loop(ctx: CommandContext, args) -> CommandResult:
    svc = getattr(ctx.bridge, "loop_service", None)
    if svc is None:
        return CommandResult(False, "loops not available here")

    text = (args.get("text") or "").strip()

    # Status.
    if not text:
        res = svc.status(from_handle=ctx.handle)
        if "error" in res:
            return CommandResult(False, "/loop failed", res["error"])
        status = res["loop"]
        if status is None:
            return CommandResult(True, "no loop armed")
        return CommandResult(True, "loop armed", _describe(status))

    # Reap. Exact match only, so `/loop stop the dev server` still arms.
    if text == "stop":
        res = svc.stop(from_handle=ctx.handle, reason="stopped by the operator")
        if "error" in res:
            return CommandResult(False, "/loop failed", res["error"])
        if not res["stopped"]:
            return CommandResult(False, "no loop armed")
        return CommandResult(True, "loop stopped")

    # Arm.
    had = svc.status(from_handle=ctx.handle).get("loop")
    res = svc.arm(from_handle=ctx.handle, text=text,
                  max_iterations=args.get("max") or DEFAULT_MAX_ITERATIONS)
    if "error" in res:
        return CommandResult(False, "/loop failed", res["error"])
    verb = "loop replaced" if had else "loop armed"
    return CommandResult(
        True, f"{verb} — max {res['max_iterations']} iterations", res["text"])


register(SlashCommand(
    "loop",
    "repeat an instruction until the agent says it's done",
    "/loop [--max N] <instruction> | /loop | /loop stop",
    _loop,
    spec=ArgSpec(
        positionals=(Arg("text", required=False, greedy=True),),
        flags=(Flag("max"),))))
```

- [ ] **Step 4: Register the module**

In `src/aegis/commands/builtins/__init__.py`, add alongside the other imports:

```python
from aegis.commands.builtins import loop as _loop  # noqa: F401
```

- [ ] **Step 5: Run the tests**

Run: `uv run python -m pytest tests/test_loop.py -v`
Expected: PASS

- [ ] **Step 6: Check `/help` still renders and no command collided**

Run: `uv run python -m pytest tests/test_slash_commands.py -q`
Expected: PASS. (If that file doesn't exist, run `uv run python -m pytest tests/ -k "slash or command" -q` instead.)

- [ ] **Step 7: Commit**

```bash
git add src/aegis/commands/builtins/loop.py
git commit -m "feat(loop): the /loop slash command

Harness-agnostic, so it lands in the TUI and the web client at once. 'stop'
is a verb only on exact match — '/loop stop the dev server and restart it'
arms as typed, which is the reading anyone typing it intends." -- src/aegis/commands/builtins/loop.py src/aegis/commands/builtins/__init__.py tests/test_loop.py
```

---

### Task 7: the StatusBar loop segment

**Files:**
- Modify: `src/aegis/tui/widgets.py` (`StatusBar`)
- Modify: `src/aegis/tui/pane.py` (subscribe to `on_loop`)
- Test: `tests/test_loop.py`

**Interfaces:**
- Consumes: `AgentSession.on_loop` (Task 2).
- Produces: `StatusBar.set_loop(status: dict | None) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loop.py`:

```python
# --------------------------------------------------------------------------
# Task 7 — StatusBar segment
# --------------------------------------------------------------------------
from aegis.tui.themes import INK, aegis_colors      # noqa: E402
from aegis.tui.widgets import StatusBar             # noqa: E402


def test_status_bar_shows_and_hides_the_loop_segment():
    bar = StatusBar("opus", "high", aegis_colors(INK))
    bar._refresh()
    assert "loop" not in bar.render_plain()
    bar.set_loop({"text": "keep going", "iteration": 3, "max_iterations": 20})
    assert "loop 3/20" in bar.render_plain()
    bar.set_loop(None)
    assert "loop" not in bar.render_plain()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_loop.py -k status_bar -v`
Expected: FAIL — `AttributeError: 'StatusBar' object has no attribute 'set_loop'`

- [ ] **Step 3: Add the segment to `StatusBar`**

In `src/aegis/tui/widgets.py`, in `StatusBar.__init__`, next to `self._system`:

```python
        self._loop: str = ""
```

Add the setter next to `set_system`:

```python
    def set_loop(self, status: dict | None) -> None:
        """Loop segment (`⟳ loop 3/20`); None hides it."""
        self._loop = ("" if status is None else
                      f"⟳ loop {status['iteration']}/"
                      f"{status['max_iterations']}")
        self._refresh()
```

And in `_refresh`, between the `_metrics` and `_system` blocks:

```python
        if self._loop:
            line += f"    {self._loop}"
```

- [ ] **Step 4: Wire the pane to the session's `on_loop`**

In `src/aegis/tui/pane.py`, the pane installs its `_core` observers at lines 532-535 of `ConversationPane.__init__`, via `add_*_observer` methods:

```python
        self._core.add_event_observer(self._on_core_event)
        self._core.add_state_observer(self._on_core_state)
        self._core.add_inbox_observer(self._on_core_inbox)
        self._core.add_dispatch_observer(self._on_core_dispatch)
```

`on_loop` is a single primary slot (no `add_loop_observer` — one frontend owns
the chip), so assign it directly, immediately after line 535:

```python
        self._core.on_loop = self._on_loop_change
```

Note `self._core` may be a `RemotePaneCore` in remote mode (the `core is not
None` branch at line 528), which has no `on_loop` attribute to speak of;
plain assignment on a Python object is harmless there and the remote bridge
stubs `loop_service` anyway.

Then add the handler method to `ConversationPane`, next to the other observer handlers:

```python
    def _on_loop_change(self, session, state, reason: str) -> None:
        """Drive the StatusBar loop segment, and toast on termination."""
        bars = self.query(StatusBar)
        if bars:
            bars.first().set_loop(state.status() if state is not None else None)
        if state is None and reason not in ("stopped",):
            self.app.notify(f"loop {reason}", timeout=5.0)
```

- [ ] **Step 5: Run the tests**

Run: `uv run python -m pytest tests/test_loop.py -v`
Expected: PASS

- [ ] **Step 6: Run the full TUI-adjacent suites**

Run each as its own step and check the exit code:

```bash
uv run python -m pytest tests/test_loop.py tests/test_reminder.py -q
uv run python -m pytest tests/test_tui.py -q
uv run python -m pytest tests/test_background_mount_hidden.py -q
```

Expected: PASS for each. If `tests/test_tui.py` reports `UnresolvedVariableError: reference to undefined variable '$background'`, that is the pre-existing theme-state leak noted in Global Constraints — re-run that file alone to confirm it passes in isolation.

- [ ] **Step 7: Lint**

Run: `uv run ruff check src/aegis/core/loop.py src/aegis/queue/loop.py src/aegis/commands/builtins/loop.py src/aegis/core/session.py src/aegis/tui/widgets.py src/aegis/tui/pane.py tests/test_loop.py`
Expected: clean. (The `F821 Undefined name 'Workspace'` in `src/aegis/tui/app.py` is pre-existing — don't include that file in the lint invocation and don't fix it.)

- [ ] **Step 8: Commit**

```bash
git commit -m "feat(loop): StatusBar segment and pane wiring

'⟳ loop 3/20' next to the build string, plus a toast when a loop ends for
any reason other than a plain operator stop — a capped loop especially
should not vanish silently." -- src/aegis/tui/widgets.py src/aegis/tui/pane.py tests/test_loop.py
```

---

### Task 8: docs

**Files:**
- Modify: `CHANGELOG.md`, `README.md`, `AGENTS.md`
- Test: none (documentation)

- [ ] **Step 1: Add the CHANGELOG entry**

Under the current unreleased heading (match the surrounding style — read the top of `CHANGELOG.md` first):

```markdown
### Added

- **`/loop <instruction>`** — repeat an instruction until the agent says it's
  done. Fires at every turn boundary where the session would otherwise settle
  idle, strictly behind inbox traffic and reminders, and yields to an armed
  `aegis_monitor` so it composes with monitors instead of spinning against
  them. The agent reaps it with `aegis_loop_stop`; an iteration cap
  (default 20, `--max N`) stops a loop the agent never ends. `/loop` shows
  status, `/loop stop` reaps, Esc cancels. Session-scoped: loops do not
  survive a restart.
- The running build (`aegis 0.21.0+<sha>`) now shows in the TUI status bar.

### Fixed

- `AegisApp.get()` — turn-end `aegis_remind` never resolved its session in the
  TUI (only `aegis serve` had the lookup), so every turn-end reminder answered
  "no live session".
- Panes mounted in the background (agent-spawned sessions, restored terminals,
  restored file tabs) no longer stack visibly on top of the active tab.
```

- [ ] **Step 2: Document the command in `README.md`**

The README does not enumerate individual slash commands — line 554 is a single
row in the input-prefix table pointing at `/help`:

```markdown
| `/cmd` | **Slash command** — aegis runs it directly (`/help` lists them); never reaches the agent |
```

So there is no per-command list to extend. Add `/loop` to the input-prefix
table as its own row directly beneath line 554, since it is the one slash
command whose effect persists past the keystroke and is worth naming up front:

```markdown
| `/loop <instruction>` | Repeat an instruction every turn until the agent calls `aegis_loop_stop` or the cap (default 20) is hit; `/loop stop` or Esc cancels |
```

- [ ] **Step 3: Commit**

```bash
git commit -m "docs(loop): changelog + README entry for /loop" -- CHANGELOG.md README.md
```

---

## Verification

- [ ] `uv run python -m pytest tests/test_loop.py -q` — all green
- [ ] `uv run python -m pytest tests/test_reminder.py tests/test_queue_inbox.py tests/test_queue_session_deliver.py -q` — no regressions
- [ ] `uv run python -m pytest tests/ -q -x --ignore=tests/tui` — full suite, minus the theme-leaking directory
- [ ] Live smoke in the TUI: restart `aegis`, type `/loop count to three, one number per turn, then stop`, confirm the `⟳ loop n/20` segment advances, the `> from loop · iteration n/20` headers appear, and the agent's `aegis_loop_stop` clears it
- [ ] Live smoke of the monitor gate: `/loop <something>` then have the agent call `aegis_monitor` on a slow command; confirm the loop does not fire while the monitor is armed
