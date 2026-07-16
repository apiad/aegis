# Aegis TUI input handling + handoff interrupt — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five input-key behaviors to the TUI chat input (Esc clear/interrupt, Alt+Enter send-with-interrupt, Up/Down history recall) and give `aegis_handoff` an `interrupt` flag so peers can cut each other's live turn.

**Architecture:** Key-level decisions live in `GrowingInput._on_key` (a Textual `TextArea` subclass); the `Submitted` message carries a `kind` field (`"enqueue"` | `"interrupt"`). The pane branches on `kind` and drives an interrupt-then-send. Esc stays a single app-level priority binding whose `action_interrupt` clears the input first when non-empty. `aegis_handoff` gains `interrupt: bool` and calls a new `AppBridge.interrupt(handle)` before delivering.

**Tech Stack:** Python 3.13+, Textual 8.2.6, FastMCP, `uv`, pytest (`pytest-asyncio`).

## Global Constraints

- Python 3.13+; use `uv` (`uv run pytest`, `uv pip install -e .`), never bare `pip`/`python`.
- TDD: failing test first, minimal implementation, commit per logical unit.
- Textual is 8.x. Interrupt key is `Escape` (Textual reserves `ctrl+c`). The TUI requires a TTY; live/driver tests do not go through the App.
- Test gate for iteration: `uv run python -m pytest <touched files> -q` (the full suite intermittently flakes 1–2 TUI/watchdog tests on inotify limits — re-run a failing TUI test in isolation before treating it as real).
- Do NOT use `-k "not live"` (matches `live` as a substring); use `-m "not live"` for the hermetic suite.
- Backward compatibility: `GrowingInput.Submitted(sender, value)` and `aegis_handoff(from_handle, target_handle, context)` must keep working with no new args passed (defaults preserve today's behavior).
- Boundary detection uses Textual's `TextArea.cursor_at_first_line` / `cursor_at_last_line` properties; cursor-to-end uses `self.move_cursor(self.document.end)`.

---

### Task 1: `Submitted.kind` + interrupt-send key routing

Add a `kind` discriminator to the submit message and wire `alt+enter` / `ctrl+enter` to submit with `kind="interrupt"`. Move `alt+enter` off newline duty (newline keeps `ctrl+j` + `shift+enter`).

**Files:**
- Modify: `src/aegis/tui/widgets.py` (`GrowingInput.Submitted`, `action_submit`, `_on_key`)
- Test: `tests/test_growing_input_keys.py` (create)

**Interfaces:**
- Produces:
  - `GrowingInput.Submitted(sender, value, kind="enqueue")` — `kind: str` is `"enqueue"` or `"interrupt"`; attribute `event.kind`.
  - `GrowingInput.action_submit(kind: str = "enqueue")` — posts `Submitted` with that kind.

- [ ] **Step 1: Write the failing test**

Create `tests/test_growing_input_keys.py`:

```python
"""GrowingInput key routing: enqueue vs interrupt submit, and newline keys."""
from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from aegis.tui.widgets import GrowingInput


class _Host(App):
    def compose(self) -> ComposeResult:
        yield GrowingInput(id="inp")


@pytest.mark.asyncio
async def test_enter_submits_enqueue():
    app = _Host()
    async with app.run_test() as pilot:
        inp = app.query_one(GrowingInput)
        inp.focus()
        inp.value = "hello"
        got = []
        app.on_growing_input_submitted = lambda e: got.append(e.kind)  # noqa
        inp.post_message(GrowingInput.Submitted(inp, inp.text))
        await pilot.pause()
        assert got == ["enqueue"]


@pytest.mark.asyncio
async def test_alt_enter_submits_interrupt():
    app = _Host()
    async with app.run_test() as pilot:
        inp = app.query_one(GrowingInput)
        inp.focus()
        inp.value = "urgent"
        kinds = []

        def handler(event: GrowingInput.Submitted) -> None:
            kinds.append(event.kind)

        app.on_growing_input_submitted = handler  # noqa
        await pilot.press("alt+enter")
        await pilot.pause()
        assert kinds == ["interrupt"]


@pytest.mark.asyncio
async def test_alt_enter_no_longer_inserts_newline():
    app = _Host()
    async with app.run_test() as pilot:
        inp = app.query_one(GrowingInput)
        inp.focus()
        inp.value = "line"
        app.on_growing_input_submitted = lambda e: None  # noqa
        await pilot.press("alt+enter")
        await pilot.pause()
        assert "\n" not in inp.text


@pytest.mark.asyncio
async def test_ctrl_j_inserts_newline():
    app = _Host()
    async with app.run_test() as pilot:
        inp = app.query_one(GrowingInput)
        inp.focus()
        inp.value = "line"
        inp.move_cursor(inp.document.end)
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert "\n" in inp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_growing_input_keys.py -q`
Expected: FAIL — `Submitted.__init__` has no `kind`; `alt+enter` still inserts a newline.

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/tui/widgets.py`, update `Submitted`, `action_submit`, and `_on_key`:

```python
    class Submitted(Message):
        def __init__(self, sender: "GrowingInput", value: str,
                     kind: str = "enqueue") -> None:
            super().__init__()
            self.input = sender
            self.value = value
            self.kind = kind

        @property
        def control(self) -> "GrowingInput":
            return self.input
```

```python
    async def action_submit(self, kind: str = "enqueue") -> None:
        self.post_message(self.Submitted(self, self.text, kind))

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            await self.action_submit("enqueue")
            return
        if event.key in ("alt+enter", "ctrl+enter"):
            event.stop()
            event.prevent_default()
            await self.action_submit("interrupt")
            return
        if event.key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return
        await super()._on_key(event)
```

Also update the class docstring: `enter` enqueues; `alt+enter` / `ctrl+enter` send-with-interrupt; `shift+enter` / `ctrl+j` insert a newline.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_growing_input_keys.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/widgets.py tests/test_growing_input_keys.py
git commit -m "feat(tui): Submitted.kind + alt/ctrl+enter interrupt-send routing"
```

---

### Task 2: Per-pane input history ring (Up/Down recall)

Add a session-lifetime history ring to `GrowingInput`: Up recalls the previous sent message when the cursor is on the first line; Down recalls newer / restores the stashed draft when on the last line. Both submit kinds record history.

**Files:**
- Modify: `src/aegis/tui/widgets.py` (`GrowingInput.__init__`, `action_submit`, `_on_key`, new helpers)
- Test: `tests/test_growing_input_history.py` (create)

**Interfaces:**
- Consumes: `GrowingInput.action_submit(kind)` from Task 1 (records history before posting).
- Produces:
  - `GrowingInput._record_history(text: str) -> None`
  - `GrowingInput._history_prev() -> None` / `_history_next() -> None`
  - Instance state: `_history: list[str]`, `_hist_idx: int | None`, `_hist_draft: str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_growing_input_history.py`:

```python
"""GrowingInput history ring: boundary-aware Up/Down recall, draft stash."""
from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from aegis.tui.widgets import GrowingInput


class _Host(App):
    def compose(self) -> ComposeResult:
        yield GrowingInput(id="inp")


async def _send(app, pilot, text: str) -> None:
    inp = app.query_one(GrowingInput)
    inp.value = text
    await inp.action_submit("enqueue")
    inp.value = ""            # the pane clears after submit; mimic it here
    await pilot.pause()


@pytest.mark.asyncio
async def test_up_recalls_previous_then_older():
    app = _Host()
    async with app.run_test() as pilot:
        app.on_growing_input_submitted = lambda e: None  # noqa
        inp = app.query_one(GrowingInput)
        inp.focus()
        await _send(app, pilot, "first")
        await _send(app, pilot, "second")

        await pilot.press("up")
        await pilot.pause()
        assert inp.text == "second"
        await pilot.press("up")
        await pilot.pause()
        assert inp.text == "first"
        # Already oldest: another Up stays put.
        await pilot.press("up")
        await pilot.pause()
        assert inp.text == "first"


@pytest.mark.asyncio
async def test_down_past_newest_restores_draft():
    app = _Host()
    async with app.run_test() as pilot:
        app.on_growing_input_submitted = lambda e: None  # noqa
        inp = app.query_one(GrowingInput)
        inp.focus()
        await _send(app, pilot, "old")
        inp.value = "half-typed draft"
        inp.move_cursor(inp.document.end)

        await pilot.press("up")       # enter recall — stashes the draft
        await pilot.pause()
        assert inp.text == "old"
        await pilot.press("down")     # past newest — restore draft, exit recall
        await pilot.pause()
        assert inp.text == "half-typed draft"


@pytest.mark.asyncio
async def test_up_mid_buffer_moves_cursor_not_history():
    app = _Host()
    async with app.run_test() as pilot:
        app.on_growing_input_submitted = lambda e: None  # noqa
        inp = app.query_one(GrowingInput)
        inp.focus()
        await _send(app, pilot, "history entry")
        inp.value = "line one\nline two"
        inp.move_cursor(inp.document.end)   # on the last line, not first

        await pilot.press("up")             # cursor moves up a line, no recall
        await pilot.pause()
        assert inp.text == "line one\nline two"


@pytest.mark.asyncio
async def test_consecutive_duplicate_sends_collapse():
    app = _Host()
    async with app.run_test() as pilot:
        app.on_growing_input_submitted = lambda e: None  # noqa
        inp = app.query_one(GrowingInput)
        inp.focus()
        await _send(app, pilot, "same")
        await _send(app, pilot, "same")
        assert inp._history == ["same"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_growing_input_history.py -q`
Expected: FAIL — no `_history` attribute; Up/Down move the cursor only.

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/tui/widgets.py`, initialize ring state in `__init__` (after `super().__init__(...)`):

```python
        self._history: list[str] = []
        self._hist_idx: int | None = None
        self._hist_draft: str = ""
```

Record history inside `action_submit` (before posting), then add the recall helpers:

```python
    async def action_submit(self, kind: str = "enqueue") -> None:
        self._record_history(self.text)
        self.post_message(self.Submitted(self, self.text, kind))

    def _record_history(self, text: str) -> None:
        text = text.strip()
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._hist_idx = None
        self._hist_draft = ""

    def _history_prev(self) -> None:
        if not self._history:
            return
        if self._hist_idx is None:
            self._hist_draft = self.text
            self._hist_idx = len(self._history) - 1
        elif self._hist_idx > 0:
            self._hist_idx -= 1
        else:
            return
        self.value = self._history[self._hist_idx]
        self.move_cursor(self.document.end)

    def _history_next(self) -> None:
        if self._hist_idx is None:
            return
        if self._hist_idx < len(self._history) - 1:
            self._hist_idx += 1
            self.value = self._history[self._hist_idx]
        else:
            self._hist_idx = None
            self.value = self._hist_draft
            self._hist_draft = ""
        self.move_cursor(self.document.end)
```

Add the Up/Down branches to `_on_key`, before the final `await super()._on_key(event)`:

```python
        if event.key == "up" and self.cursor_at_first_line and self._history:
            event.stop()
            event.prevent_default()
            self._history_prev()
            return
        if event.key == "down" and self.cursor_at_last_line \
                and self._hist_idx is not None:
            event.stop()
            event.prevent_default()
            self._history_next()
            return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_growing_input_history.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/widgets.py tests/test_growing_input_history.py
git commit -m "feat(tui): per-pane input history ring with boundary-aware up/down"
```

---

### Task 3: Pane interrupt-send branch

Branch `on_growing_input_submitted` on `event.kind`: `"interrupt"` while working cuts the live turn (`self._core.interrupt()`) and then delivers the message now; idle degrades to a normal enqueue.

**Files:**
- Modify: `src/aegis/tui/pane.py` (`on_growing_input_submitted`)
- Test: `tests/test_pane_interrupt_send.py` (create)

**Interfaces:**
- Consumes: `GrowingInput.Submitted(..., kind)` from Task 1; `AgentSession.interrupt()` (existing, `src/aegis/core/session.py:470`); `AgentSession.deliver(msg)` (existing).

- [ ] **Step 1: Write the failing test**

Create `tests/test_pane_interrupt_send.py` (reuses the gated-fake harness pattern from `tests/test_pane_pending_chips.py`):

```python
"""ConversationPane: alt/ctrl+enter interrupt-send cuts the live turn and
sends the message as the next turn; idle degrades to a normal enqueue."""
from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.events import Result
from aegis.tui.app import AegisApp
from aegis.tui.pane import ConversationPane
from aegis.tui.pending import PendingStrip
from aegis.tui.state import AgentState
from aegis.tui.widgets import GrowingInput


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class GatedSession:
    def __init__(self):
        self.sent: list[str] = []
        self.started = self.closed = self.interrupted = False
        self._gate = asyncio.Event()

    async def start(self):
        self.started = True

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        await self._gate.wait()
        yield Result(duration_ms=1, is_error=False, usage=None)
        self._gate.clear()

    async def interrupt(self):
        self.interrupted = True

    async def close(self):
        self.closed = True

    def release(self):
        self._gate.set()


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def __init__(self):
        self.started = self.stopped = False
        self.bound = None

    def bind(self, bridge):
        self.bound = bridge

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


def _factory(session):
    def make(agent, mcp_url, handle):
        return session
    return make


def _app(session):
    return AegisApp({"default": _agent()}, "default",
                    _factory(session), FakeMCP())


async def _submit(pane, text, kind="enqueue"):
    inp = pane.query_one(GrowingInput)
    await pane.on_growing_input_submitted(
        GrowingInput.Submitted(inp, text, kind))


@pytest.mark.asyncio
async def test_interrupt_send_while_working_cuts_turn_and_sends():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await _submit(pane, "first")            # idle → lands, turn blocks
        await pilot.pause()
        assert pane.state is AgentState.working

        await _submit(pane, "urgent", kind="interrupt")
        await pilot.pause()
        # The live turn was interrupted, not queued as a chip.
        assert sess.interrupted is True
        assert not pane.query_one(PendingStrip).chips
        sess.release()


@pytest.mark.asyncio
async def test_interrupt_send_while_idle_is_a_plain_send():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await _submit(pane, "hello", kind="interrupt")   # idle
        await pilot.pause()
        assert sess.interrupted is False
        assert pane.state is AgentState.working
        sess.release()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_pane_interrupt_send.py -q`
Expected: FAIL — `on_growing_input_submitted` ignores `kind`; the interrupt-send queues a chip instead of cutting the turn.

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/tui/pane.py`, replace the body of `on_growing_input_submitted`:

```python
    async def on_growing_input_submitted(self,
                                  event: GrowingInput.Submitted) -> None:
        event.stop()
        text = event.value.strip()
        if not text:
            return
        inp = self.query_one(GrowingInput)
        inp.value = ""
        from aegis.queue import InboxMessage, now_iso, sender_user
        msg = InboxMessage(sender=sender_user(), timestamp=now_iso(),
                           body=text)
        self._flush_streaming()
        # Interrupt-send (alt/ctrl+enter): cut the live turn first so the
        # message lands now as the next turn instead of queuing behind it.
        # Idle → nothing to interrupt; falls through to a normal deliver.
        if event.kind == "interrupt" and self.state is AgentState.working:
            await self._core.interrupt()
        receipt = await self._core.deliver(msg)
        if receipt.disposition == "queued":
            self.query_one(PendingStrip).add(msg)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_pane_interrupt_send.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/pane.py tests/test_pane_interrupt_send.py
git commit -m "feat(tui): interrupt-send path in pane (alt/ctrl+enter cuts live turn)"
```

---

### Task 4: Esc clears input when non-empty, else interrupts

Keep the single app-level `escape` priority binding. `action_interrupt` clears the active pane's input if it holds text; otherwise falls through to interrupt. Modal dismiss still wins first.

**Files:**
- Modify: `src/aegis/tui/pane.py` (add `clear_input_if_present`)
- Modify: `src/aegis/tui/app.py` (`action_interrupt`)
- Test: `tests/test_esc_clear_or_interrupt.py` (create)

**Interfaces:**
- Produces: `ConversationPane.clear_input_if_present() -> bool` — clears the input and returns `True` when it held non-whitespace text; returns `False` (no-op) otherwise.
- Consumes: `ConversationPane.interrupt()` (existing).

- [ ] **Step 1: Write the failing test**

Create `tests/test_esc_clear_or_interrupt.py` (reuse the gated-fake harness — copy `GatedSession`, `FakeMCP`, `_factory`, `_app`, `_agent` from `tests/test_pane_interrupt_send.py`):

```python
"""Esc clears a non-empty input; on an empty input it interrupts the turn."""
from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.events import Result
from aegis.tui.app import AegisApp
from aegis.tui.state import AgentState
from aegis.tui.widgets import GrowingInput


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class GatedSession:
    def __init__(self):
        self.sent: list[str] = []
        self.started = self.closed = self.interrupted = False
        self._gate = asyncio.Event()

    async def start(self):
        self.started = True

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        await self._gate.wait()
        yield Result(duration_ms=1, is_error=False, usage=None)
        self._gate.clear()

    async def interrupt(self):
        self.interrupted = True

    async def close(self):
        self.closed = True

    def release(self):
        self._gate.set()


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def __init__(self):
        self.started = self.stopped = False
        self.bound = None

    def bind(self, bridge):
        self.bound = bridge

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


def _factory(session):
    def make(agent, mcp_url, handle):
        return session
    return make


def _app(session):
    return AegisApp({"default": _agent()}, "default",
                    _factory(session), FakeMCP())


@pytest.mark.asyncio
async def test_esc_clears_nonempty_input_and_does_not_interrupt():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await pane._core.send("go")             # drive into working
        await pilot.pause()
        assert pane.state is AgentState.working
        inp = pane.query_one(GrowingInput)
        inp.value = "half typed"
        inp.focus()

        await pilot.press("escape")
        await pilot.pause()
        assert inp.value == ""                  # cleared
        assert sess.interrupted is False        # turn NOT interrupted
        sess.release()


@pytest.mark.asyncio
async def test_esc_on_empty_input_interrupts_turn():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await pane._core.send("go")
        await pilot.pause()
        assert pane.state is AgentState.working
        inp = pane.query_one(GrowingInput)
        inp.value = ""
        inp.focus()

        await pilot.press("escape")
        await pilot.pause()
        assert sess.interrupted is True
        sess.release()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_esc_clear_or_interrupt.py -q`
Expected: FAIL — Esc interrupts unconditionally, so `test_esc_clears_nonempty_input_and_does_not_interrupt` fails (input not cleared / turn interrupted).

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/tui/pane.py`, add the helper (near `input_widget`):

```python
    def clear_input_if_present(self) -> bool:
        """Esc handler: clear a non-empty input and report we consumed the
        key. Empty input → no-op, return False so the app interrupts."""
        inp = self.query_one(GrowingInput)
        if inp.value.strip():
            inp.value = ""
            return True
        return False
```

In `src/aegis/tui/app.py`, update `action_interrupt`:

```python
    def action_interrupt(self) -> None:
        # The escape binding is priority=True at the app level, so it
        # would otherwise eat escape presses meant to dismiss a modal
        # (the dashboard, the agent picker). Dismiss the modal first
        # and only fall through on the default screen.
        from textual.screen import ModalScreen
        if isinstance(self.screen, ModalScreen):
            self.screen.dismiss()
            return
        active = self._active
        # Esc clears a half-typed message before it interrupts the turn.
        if active is not None and hasattr(active, "clear_input_if_present"):
            if active.clear_input_if_present():
                return
        if active is not None and hasattr(active, "interrupt"):
            active.interrupt()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_esc_clear_or_interrupt.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/pane.py src/aegis/tui/app.py tests/test_esc_clear_or_interrupt.py
git commit -m "feat(tui): esc clears non-empty input before interrupting the turn"
```

---

### Task 5: `AppBridge.interrupt(handle)` + `AegisApp` implementation

Add an interrupt-by-handle entry point to the `AppBridge` protocol so the MCP server can cut a *named* peer's turn. `SessionManager.interrupt(handle)` already satisfies it; `AegisApp` gains a matching per-handle method.

**Files:**
- Modify: `src/aegis/mcp/bridge.py` (`AppBridge` protocol)
- Modify: `src/aegis/tui/app.py` (`AegisApp.interrupt`)
- Verify (no change): `src/aegis/core/manager.py:182` already has `async def interrupt(self, handle: str) -> None`.
- Test: `tests/test_app_bridge_interrupt.py` (create)

**Interfaces:**
- Produces: `AppBridge.interrupt(handle: str) -> None` (async); `AegisApp.interrupt(handle)` finds the pane by handle and calls `pane.interrupt()`.
- Consumes: `ConversationPane.interrupt()` (existing).

- [ ] **Step 1: Write the failing test**

Create `tests/test_app_bridge_interrupt.py` (reuse `GatedSession`/`FakeMCP`/`_factory`/`_agent` as in Task 3):

```python
"""AegisApp.interrupt(handle) cuts the named pane's live turn."""
from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.events import Result
from aegis.tui.app import AegisApp
from aegis.tui.state import AgentState


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class GatedSession:
    def __init__(self):
        self.sent: list[str] = []
        self.started = self.closed = self.interrupted = False
        self._gate = asyncio.Event()

    async def start(self):
        self.started = True

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        await self._gate.wait()
        yield Result(duration_ms=1, is_error=False, usage=None)
        self._gate.clear()

    async def interrupt(self):
        self.interrupted = True

    async def close(self):
        self.closed = True

    def release(self):
        self._gate.set()


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def __init__(self):
        self.bound = None

    def bind(self, bridge):
        self.bound = bridge

    async def start(self):
        pass

    async def stop(self):
        pass


def _factory(session):
    def make(agent, mcp_url, handle):
        return session
    return make


@pytest.mark.asyncio
async def test_app_interrupt_by_handle_cuts_that_pane():
    sess = GatedSession()
    app = AegisApp({"default": _agent()}, "default",
                   _factory(sess), FakeMCP())
    async with app.run_test() as pilot:
        pane = app._panes[0]
        await pane._core.send("go")
        await pilot.pause()
        assert pane.state is AgentState.working

        await app.interrupt(pane.handle)
        await pilot.pause()
        assert sess.interrupted is True


@pytest.mark.asyncio
async def test_app_interrupt_unknown_handle_is_noop():
    sess = GatedSession()
    app = AegisApp({"default": _agent()}, "default",
                   _factory(sess), FakeMCP())
    async with app.run_test() as pilot:
        await app.interrupt("nobody-here")   # must not raise
        await pilot.pause()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_app_bridge_interrupt.py -q`
Expected: FAIL — `AegisApp` has no `interrupt` method (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/mcp/bridge.py`, add to the `AppBridge` protocol (near `close`):

```python
    async def interrupt(self, handle: str) -> None: ...
```

In `src/aegis/tui/app.py`, add the method next to `close` (mirrors its lookup pattern):

```python
    async def interrupt(self, handle: str) -> None:
        """AppBridge-shaped: cut the named pane's live turn (a peer's, not
        just the active one). Unknown handle → no-op."""
        pane = next((p for p in self._panes
                     if isinstance(p, ConversationPane)
                     and p.handle == handle), None)
        if pane is not None:
            pane.interrupt()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_app_bridge_interrupt.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/aegis/mcp/bridge.py src/aegis/tui/app.py tests/test_app_bridge_interrupt.py
git commit -m "feat: AppBridge.interrupt(handle) + AegisApp per-handle interrupt"
```

---

### Task 6: `aegis_handoff(interrupt: bool = False)` + agent guidance

Add the `interrupt` flag to the MCP tool. When `True` and the target is working, cut its turn (via `bridge.interrupt`) before delivering so the handoff lands now; return-string distinguishes the cases. Update the docstring and the BRIEFING/PRIMING guidance.

**Files:**
- Modify: `src/aegis/mcp/server.py` (`aegis_handoff` signature, body, docstring; BRIEFING/PRIMING text)
- Test: `tests/test_handoff_interrupt.py` (create)
- Live (extend, optional if `claude` present): `tests/test_mcp_live.py`

**Interfaces:**
- Consumes: `AppBridge.interrupt(handle)` (Task 5); `bridge.inbox_router.deliver` and `bridge.list_sessions` (existing).
- Produces: `aegis_handoff(from_handle, target_handle, context, interrupt: bool = False) -> str`.
  - Return strings: `interrupt=True` & target working → `"interrupted & landed at <target>"`; `interrupt=True` & idle → `"landed at <target>"`; `interrupt=False` → unchanged (`"landed at <target>"` / `"queued for <target> (position N)"`); rejections unchanged.

- [ ] **Step 1: Write the failing test**

Create `tests/test_handoff_interrupt.py` with a fake bridge exercising the tool through the FastMCP registration used elsewhere in `tests/` (mirror the construction in the existing MCP unit tests — locate with `grep -n "build_server\|FastMCP\|def _bridge\|aegis_handoff" tests/test_*.py` and reuse that pattern). Minimal fake bridge:

```python
"""aegis_handoff(interrupt=True) cuts the target's live turn before delivering."""
from __future__ import annotations

import pytest

from aegis.queue import Delivery


class _Info:
    def __init__(self, handle, state):
        self.handle = handle
        self.state = state
        self.agent_slug = "default"
        self.active = False
        self.unseen = False
        self.spawned_by = None


class _Inbox:
    def __init__(self):
        self.delivered = []

    async def deliver(self, handle, msg):
        self.delivered.append((handle, msg.body))
        return Delivery(disposition="landed", depth=0)


class _Bridge:
    def __init__(self, target_state):
        self.inbox_router = _Inbox()
        self.interrupted = []
        self._sessions = [_Info("alpha", "ready"),
                          _Info("beta", target_state)]

    def list_sessions(self):
        return list(self._sessions)

    async def interrupt(self, handle):
        self.interrupted.append(handle)


@pytest.mark.asyncio
async def test_handoff_interrupt_cuts_working_target():
    from aegis.mcp.server import make_handoff  # thin accessor; see Step 3
    bridge = _Bridge(target_state="working")
    handoff = make_handoff(bridge)
    out = await handoff("alpha", "beta", "stop, wrong file", interrupt=True)
    assert bridge.interrupted == ["beta"]
    assert bridge.inbox_router.delivered == [("beta", "stop, wrong file")]
    assert out == "interrupted & landed at beta"


@pytest.mark.asyncio
async def test_handoff_interrupt_idle_target_is_plain_land():
    from aegis.mcp.server import make_handoff
    bridge = _Bridge(target_state="ready")
    handoff = make_handoff(bridge)
    out = await handoff("alpha", "beta", "fyi", interrupt=True)
    assert bridge.interrupted == []
    assert out == "landed at beta"


@pytest.mark.asyncio
async def test_handoff_default_does_not_interrupt():
    from aegis.mcp.server import make_handoff
    bridge = _Bridge(target_state="working")
    handoff = make_handoff(bridge)
    out = await handoff("alpha", "beta", "later")
    assert bridge.interrupted == []
    assert out == "queued for beta (position 0)"
```

> Note: `aegis_handoff` is currently a closure registered with the bare `@server.tool` decorator inside the server-build function (see `src/aegis/mcp/server.py:714`, `server` is the FastMCP instance in enclosing scope). Step 3 extracts the body into a module-level `make_handoff(bridge)` factory returning the async callable, then registers it in-place with `server.tool(make_handoff(bridge))`. This makes the logic unit-testable without standing up FastMCP. The only enclosing-scope name the closure uses is `bridge` (all else is imported inside the function), so the extraction is clean.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_handoff_interrupt.py -q`
Expected: FAIL — no `make_handoff`; `interrupt` param does not exist.

- [ ] **Step 3: Write minimal implementation**

In `src/aegis/mcp/server.py`, extract a module-level factory and register it. Replace the existing closure (around line 715) with a call to the factory, and define:

```python
def make_handoff(bridge):
    async def aegis_handoff(from_handle: str, target_handle: str,
                            context: str, interrupt: bool = False) -> str:
        """One-way context transfer to a live peer aegis session.

        Delivered via the universal inbox channel: the target receives a
        normal user-message tagged ``sender=agent:<from_handle>`` — the same
        shape queue callbacks use, so peers read handoffs and callbacks
        through one surface.

        from_handle is your own aegis handle (read it from your system
        prompt).

        interrupt (default False): when False, a busy target buffers your
        context and chains it at its next turn boundary. Set interrupt=True
        ONLY when you have a blocking correction the peer needs NOW — e.g. it
        is about to act on a wrong assumption. interrupt=True cuts the peer's
        in-progress turn (discarding its current work) and lands your context
        as its next turn. It is a deliberate act, not the default.

        Returns 'landed at <target>' (idle target), 'queued for <target>
        (position N)' (busy, not interrupted), or 'interrupted & landed at
        <target>' (busy, interrupted). Returns 'handoff rejected: …' for
        self / unknown target.
        """
        from aegis.queue import InboxMessage, now_iso, sender_agent

        if from_handle == target_handle:
            return "handoff rejected: cannot hand off to yourself"
        sessions = list(bridge.list_sessions())
        target_info = next(
            (s for s in sessions if s.handle == target_handle), None)
        if target_info is None:
            return (f"handoff rejected: no session {target_handle!r} "
                    f"(use aegis_list_sessions)")
        was_working = target_info.state == "working"
        if interrupt and was_working:
            await bridge.interrupt(target_handle)
        receipt = await bridge.inbox_router.deliver(
            target_handle,
            InboxMessage(
                sender=sender_agent(from_handle),
                timestamp=now_iso(),
                body=context))
        if interrupt and was_working:
            return f"interrupted & landed at {target_handle}"
        if target_info.state == "working":
            return (f"queued for {target_handle} "
                    f"(position {receipt.depth})")
        return f"landed at {target_handle}"

    return aegis_handoff
```

At the original registration site (`src/aegis/mcp/server.py:714`), replace the `@server.tool` decorator + inline `async def aegis_handoff(...)` block with a single registration line:

```python
    server.tool(make_handoff(bridge))
```

`server.tool` is the same callable the bare `@server.tool` decorator uses on neighboring tools, so this registers `aegis_handoff` identically.

Update the BRIEFING/PRIMING strings that mention `aegis_handoff` (around `src/aegis/mcp/server.py:149`, `:302`, and the handoff line in PRIMING near `:333`) to note the `interrupt=True` option and when to use it — one clause, e.g. *"…or aegis_handoff(interrupt=True) to cut a peer's current turn when it needs a blocking correction now."*

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_handoff_interrupt.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: (Optional, if `claude` on PATH) extend the live round-trip**

In `tests/test_mcp_live.py`, add a `@pytest.mark.live` test that spawns two real sessions, puts the target into a long turn, calls `aegis_handoff(interrupt=True)`, and asserts the target's turn is cut and the handoff runs next. Skip-guard on `claude` availability exactly like the sibling live tests.

Run: `uv run python -m pytest tests/test_mcp_live.py -q` (auto-skips without `claude`).

- [ ] **Step 6: Commit**

```bash
git add src/aegis/mcp/server.py tests/test_handoff_interrupt.py tests/test_mcp_live.py
git commit -m "feat(mcp): aegis_handoff(interrupt=False) — cut a peer's live turn"
```

---

### Task 7: Docs — AGENTS.md + CHANGELOG

Record the new input gestures and the handoff flag where agents and users will look.

**Files:**
- Modify: `AGENTS.md` (Conventions section — key gestures)
- Modify: `CHANGELOG.md` (new entry)

- [ ] **Step 1: Update AGENTS.md**

Under `## Conventions` in `AGENTS.md`, add a short block documenting the input keys (Enter enqueue; Alt+Enter / Ctrl+Enter send-with-interrupt; Shift+Enter / Ctrl+J newline; Esc clear-then-interrupt; Up/Down history), and note that `aegis_handoff` takes `interrupt: bool = False`.

- [ ] **Step 2: Update CHANGELOG.md**

Add a dated entry summarizing: TUI input handling (Esc clear/interrupt, Alt+Enter interrupt-send, Up/Down history) + `aegis_handoff(interrupt=…)`.

- [ ] **Step 3: Full hermetic suite (gate)**

Run: `uv run python -m pytest -q -m "not live"`
Expected: PASS (re-run any lone TUI/watchdog flake in isolation per AGENTS.md before treating it as real).

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md CHANGELOG.md
git commit -m "docs: TUI input gestures + aegis_handoff interrupt flag"
```

---

## Self-Review

**Spec coverage:**
- Esc clear/interrupt → Task 4. ✓
- Alt+Enter (+Ctrl+Enter) send-with-interrupt → Tasks 1 (routing) + 3 (pane path). ✓
- Enter enqueues unchanged → Task 1 (regression test) + Task 3 (path unchanged). ✓
- Shift+Enter / Ctrl+J newline; Alt+Enter off newline → Task 1. ✓
- Up/Down boundary-aware history, draft stash/restore, per-pane, session-lifetime → Task 2. ✓
- `aegis_handoff(interrupt=False)` + bridge interrupt + return strings + agent guidance → Tasks 5 + 6. ✓
- Testing (hermetic widget/unit + live handoff) → each task's tests; live in Task 6 Step 5. ✓
- Out-of-scope (no persistence, no Ctrl+R search) → not built. ✓

**Type consistency:** `Submitted(sender, value, kind="enqueue")` and `event.kind` used identically in Tasks 1/3. `clear_input_if_present() -> bool` defined in Task 4 pane, consumed in Task 4 app. `AppBridge.interrupt(handle)` defined Task 5, consumed Task 6. `make_handoff(bridge)` returns the async `aegis_handoff(from_handle, target_handle, context, interrupt=False)` — signature matches tests. Return strings match across Task 6 test and impl.

**Placeholder scan:** No TBD/TODO; every code step shows real code. The only deferred detail is matching the exact FastMCP registration idiom (Task 6 Step 3) — flagged with the grep to locate it, not hand-waved.
