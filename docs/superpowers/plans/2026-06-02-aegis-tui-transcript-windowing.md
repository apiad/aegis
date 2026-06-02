# Aegis TUI Transcript Windowing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cap mounted `CopyableBlock` widget count in `ConversationPane` at ~300 so long-session scroll stays snappy, with debounced scroll-up to restore older blocks.

**Architecture:** Introduce `_history: list[BlockRecord]` as the source of truth for every block ever rendered; `_window_start` tracks the first mounted index. New events evict the top batch when the user is at the tail; scrolling near the top triggers a debounced batch remount with scroll-anchor preservation. Sticky-bottom flag gates both `scroll_end` on new events and eviction.

**Tech Stack:** Python 3.13, Textual 8.x (`VerticalScroll.scroll_y` reactive + `App.watch`), pytest-asyncio for hermetic TUI tests via `AegisApp.run_test()`.

**Spec:** `docs/superpowers/specs/2026-06-02-aegis-tui-transcript-windowing-design.md`

---

## File Structure

- `src/aegis/tui/pane.py` — all production changes (one file).
- `tests/test_pane_windowing.py` — new hermetic test file.

Constants live at module scope in `pane.py`:

```python
N_MAX = 300
EVICT_BATCH = 50
LOAD_BATCH = 100
STICKY_EPS = 2
LOAD_MORE_EPS = 3
DEBOUNCE_S = 0.15
```

`BlockRecord` dataclass also lives in `pane.py` (defined near `CopyableBlock`).

---

### Task 1: Add `BlockRecord` dataclass and `_history` bookkeeping (no behavior change)

**Files:**
- Modify: `src/aegis/tui/pane.py`
- Test: `tests/test_pane_windowing.py`

- [ ] **Step 1: Create the test file with the failing test**

Create `tests/test_pane_windowing.py`:

```python
"""Transcript windowing: bounded mounted widget count, scroll-up reloads."""
import pytest

from aegis.config import Agent
from aegis.events import AssistantText, Result, ToolUse
from aegis.tui.app import AegisApp
from aegis.tui.pane import CopyableBlock


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    def __init__(self):
        self.sent = []
        self.started = self.closed = False
    async def start(self): self.started = True
    async def send(self, text): self.sent.append(text)
    async def events(self):
        if False:
            yield  # pragma: no cover
    async def close(self): self.closed = True


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"
    def __init__(self):
        self.started = self.stopped = False
        self.bound = None
    def bind(self, bridge): self.bound = bridge
    async def start(self): self.started = True
    async def stop(self): self.stopped = True


def _factory(*sessions):
    it = iter(sessions or (FakeSession(),))
    def make(agent, mcp_url, handle):
        try:
            return next(it)
        except StopIteration:
            return FakeSession()
    return make


def _app():
    return AegisApp({"default": _agent()}, "default",
                    _factory(), FakeMCP())


@pytest.mark.asyncio
async def test_history_records_every_event():
    """Every rendered event appends a BlockRecord to _history."""
    app = _app()
    async with app.run_test():
        pane = app._panes[0]
        pane._on_core_event(None, AssistantText(text="hello", usage=None))
        pane._on_core_event(None, ToolUse(name="Read", summary="x.py", kind="read"))
        pane._on_core_event(None, Result(duration_ms=1, is_error=False))
        # Streaming text + tool use + result → 3 records.
        assert len(pane._history) == 3
        # Each record has a renderable and a payload string.
        for rec in pane._history:
            assert rec.renderable is not None
            assert isinstance(rec.payload, str)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/apiad/Workspace/repos/aegis
uv run pytest tests/test_pane_windowing.py::test_history_records_every_event -v
```

Expected: FAIL with `AttributeError: 'ConversationPane' object has no attribute '_history'`.

- [ ] **Step 3: Add `BlockRecord` dataclass and `_history` in pane.py**

In `src/aegis/tui/pane.py`, after the existing imports block (near line 32 after `from aegis.tui.widgets import ...`), add:

```python
from dataclasses import dataclass


N_MAX = 300
EVICT_BATCH = 50
LOAD_BATCH = 100
STICKY_EPS = 2
LOAD_MORE_EPS = 3
DEBOUNCE_S = 0.15


@dataclass(slots=True)
class BlockRecord:
    """One transcript entry. The renderable + payload are the same values
    that would be passed to a CopyableBlock; tight mirrors the widget flag.
    Mutable so streaming aggregation can update in place."""
    renderable: object
    payload: str
    tight: bool
```

In `ConversationPane.__init__`, after the existing streaming-state lines
(currently `self._streaming_text: str = ""`), append:

```python
        # Windowing: every rendered block lives here; only
        # _history[_window_start:] is mounted.
        self._history: list[BlockRecord] = []
        self._window_start: int = 0
        self._streaming_history_idx: int | None = None
```

In `_mount_block`, *before* the `block = CopyableBlock(...)` line, append the record:

```python
    def _mount_block(self, renderable: RenderableType,
                     text_payload: str,
                     *, tight: bool = False) -> CopyableBlock:
        self._history.append(BlockRecord(renderable, text_payload, tight))
        block = CopyableBlock(renderable, text_payload, tight=tight)
        t = self._transcript()
        ind = self._working_indicator()
        if ind is not None and ind.parent is t:
            t.mount(block, before=ind)
        else:
            t.mount(block)
        t.scroll_end(animate=False)
        return block
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_pane_windowing.py::test_history_records_every_event -v
```

Expected: PASS.

- [ ] **Step 5: Run the full pane-related suite to confirm no regression**

```bash
uv run pytest tests/test_tui.py tests/test_pane_replay.py tests/test_pane_windowing.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/pane.py tests/test_pane_windowing.py
git commit -m "feat(tui): add BlockRecord history bookkeeping to ConversationPane"
```

---

### Task 2: Sync streaming aggregation with `_history`

**Files:**
- Modify: `src/aegis/tui/pane.py`
- Test: `tests/test_pane_windowing.py`

Streaming chunks currently mutate the live widget via `update_content` but
leave the `BlockRecord` carrying the first chunk only. Without this fix,
scrollback for a long streamed turn would show truncated content.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pane_windowing.py`:

```python
@pytest.mark.asyncio
async def test_streaming_updates_history_record_in_place():
    """Three streamed AssistantText chunks coalesce into one widget AND
    one BlockRecord whose payload reflects the full concatenated text."""
    app = _app()
    async with app.run_test():
        pane = app._panes[0]
        pane._on_core_event(None, AssistantText(text="hel", usage=None))
        pane._on_core_event(None, AssistantText(text="lo ", usage=None))
        pane._on_core_event(None, AssistantText(text="world", usage=None))
        assert len(pane._history) == 1
        assert pane._history[0].payload == "hello world"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_pane_windowing.py::test_streaming_updates_history_record_in_place -v
```

Expected: FAIL — `_history[0].payload == "hel"` (only first chunk).

- [ ] **Step 3: Update `_stream_append` to maintain the index and mutate the record**

In `src/aegis/tui/pane.py`, replace the `_stream_append` method (currently
lines ~494–508) with:

```python
    def _stream_append(self, kind: str, new_text: str) -> None:
        if self._streaming_kind != kind:
            self._flush_streaming()
            self._streaming_kind = kind
            self._streaming_text = new_text
            r = self._render_for_stream(kind, self._streaming_text)
            self._streaming_block = self._mount_block(
                r, self._streaming_text)
            # The block just appended is the last entry in _history.
            self._streaming_history_idx = len(self._history) - 1
        else:
            self._streaming_text += new_text
            if self._streaming_block is not None:
                r = self._render_for_stream(
                    kind, self._streaming_text)
                self._streaming_block.update_content(
                    r, self._streaming_text)
                if self._streaming_history_idx is not None:
                    rec = self._history[self._streaming_history_idx]
                    rec.renderable = r
                    rec.payload = self._streaming_text
```

Also update `_flush_streaming` to clear the index:

```python
    def _flush_streaming(self) -> None:
        self._streaming_block = None
        self._streaming_kind = None
        self._streaming_text = ""
        self._streaming_history_idx = None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_pane_windowing.py -v
```

Expected: both tests pass.

- [ ] **Step 5: Regression check**

```bash
uv run pytest tests/test_tui.py tests/test_pane_replay.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/tui/pane.py tests/test_pane_windowing.py
git commit -m "feat(tui): sync streaming aggregation with _history record"
```

---

### Task 3: Sticky-bottom flag and gated `scroll_end`

**Files:**
- Modify: `src/aegis/tui/pane.py`
- Test: `tests/test_pane_windowing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pane_windowing.py`:

```python
@pytest.mark.asyncio
async def test_sticky_bottom_flag_starts_true_and_flips_on_scroll_up():
    """Pane starts sticky; scrolling away from the bottom flips the flag."""
    from textual.containers import VerticalScroll
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        # Fresh pane is at scroll_y=0 == max_scroll_y=0 → sticky.
        assert pane._stick_to_bottom is True

        # Pump enough events to make the transcript scrollable.
        for i in range(60):
            pane._on_core_event(None, AssistantText(text=f"line {i}", usage=None))
            # Each AssistantText is streamed; break the run so each becomes
            # its own block.
            pane._flush_streaming()
        await pilot.pause()
        await pilot.pause()

        t = pane.query_one("#transcript", VerticalScroll)
        # Scroll to top.
        t.scroll_y = 0
        await pilot.pause()
        assert pane._stick_to_bottom is False

        # Scroll back to bottom.
        t.scroll_y = t.max_scroll_y
        await pilot.pause()
        assert pane._stick_to_bottom is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_pane_windowing.py::test_sticky_bottom_flag_starts_true_and_flips_on_scroll_up -v
```

Expected: FAIL — `_stick_to_bottom` attribute does not exist.

- [ ] **Step 3: Add the flag, the watcher, and gate `scroll_end`**

In `ConversationPane.__init__`, after the `self._streaming_history_idx`
line added in Task 1, append:

```python
        self._stick_to_bottom: bool = True
        self._loading_older: bool = False
        self._load_timer = None
```

Replace the `_mount_block` method so `scroll_end` is gated on the flag:

```python
    def _mount_block(self, renderable: RenderableType,
                     text_payload: str,
                     *, tight: bool = False) -> CopyableBlock:
        self._history.append(BlockRecord(renderable, text_payload, tight))
        block = CopyableBlock(renderable, text_payload, tight=tight)
        t = self._transcript()
        ind = self._working_indicator()
        if ind is not None and ind.parent is t:
            t.mount(block, before=ind)
        else:
            t.mount(block)
        if self._stick_to_bottom:
            t.scroll_end(animate=False)
        return block
```

Extend `on_mount` to install the scroll watcher:

```python
    async def on_mount(self) -> None:
        self.query_one(StatusBar).set_state(AgentState.ready)
        self._mount_replay()
        self.refresh_metrics()
        t = self._transcript()
        self.watch(t, "scroll_y", self._on_scroll_y)
```

Add the watcher method anywhere on the class (e.g. just below
`_transcript`):

```python
    def _on_scroll_y(self, _value: float) -> None:
        t = self._transcript()
        self._stick_to_bottom = (
            (t.max_scroll_y - t.scroll_y) <= STICKY_EPS)
```

- [ ] **Step 4: Run the new test**

```bash
uv run pytest tests/test_pane_windowing.py::test_sticky_bottom_flag_starts_true_and_flips_on_scroll_up -v
```

Expected: PASS.

- [ ] **Step 5: Regression sweep**

```bash
uv run pytest tests/test_tui.py tests/test_pane_replay.py tests/test_pane_windowing.py -q
```

Expected: all pass. Note: `test_submit_sends_renders_and_bells` continues
to pass because a freshly-mounted pane is sticky at the bottom, so the
user message and echo still scroll into view.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/tui/pane.py tests/test_pane_windowing.py
git commit -m "feat(tui): track sticky-bottom and gate auto-scroll on it"
```

---

### Task 4: Eviction of older mounted blocks at the tail

**Files:**
- Modify: `src/aegis/tui/pane.py`
- Test: `tests/test_pane_windowing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pane_windowing.py`:

```python
@pytest.mark.asyncio
async def test_eviction_caps_mounted_widget_count():
    """Once history exceeds N_MAX and user is at the bottom, eviction
    keeps the mounted CopyableBlock count bounded."""
    from aegis.tui.pane import N_MAX
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        # Pump enough non-streaming events to exceed N_MAX.
        for i in range(N_MAX + 80):
            pane._on_core_event(None, ToolUse(
                name="Read", summary=f"f{i}.py", kind="read"))
        await pilot.pause()
        await pilot.pause()
        assert len(pane._history) == N_MAX + 80
        mounted = len(pane.query(CopyableBlock))
        assert mounted <= N_MAX
        # The first mounted block is somewhere in the upper part of history.
        assert pane._window_start >= 80


@pytest.mark.asyncio
async def test_no_eviction_while_user_scrolled_up():
    """User reading old content does not get yanked when new events arrive."""
    from textual.containers import VerticalScroll
    from aegis.tui.pane import N_MAX
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        # Fill close to but under N_MAX.
        for i in range(N_MAX - 10):
            pane._on_core_event(None, ToolUse(
                name="Read", summary=f"a{i}.py", kind="read"))
        await pilot.pause()
        # Scroll up.
        t = pane.query_one("#transcript", VerticalScroll)
        t.scroll_y = 0
        await pilot.pause()
        assert pane._stick_to_bottom is False
        start_before = pane._window_start
        # Pump more events that, with sticky=True, would have triggered eviction.
        for i in range(50):
            pane._on_core_event(None, ToolUse(
                name="Read", summary=f"b{i}.py", kind="read"))
        await pilot.pause()
        # No eviction happened.
        assert pane._window_start == start_before
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_pane_windowing.py -v -k "eviction or no_eviction"
```

Expected: FAIL — eviction not yet implemented; mounted count grows unbounded.

- [ ] **Step 3: Implement eviction in `_mount_block`**

Replace `_mount_block` with the evicting version:

```python
    def _mount_block(self, renderable: RenderableType,
                     text_payload: str,
                     *, tight: bool = False) -> CopyableBlock:
        self._history.append(BlockRecord(renderable, text_payload, tight))
        block = CopyableBlock(renderable, text_payload, tight=tight)
        t = self._transcript()
        ind = self._working_indicator()
        if ind is not None and ind.parent is t:
            t.mount(block, before=ind)
        else:
            t.mount(block)
        if self._stick_to_bottom:
            t.scroll_end(animate=False)
            if len(self._history) - self._window_start > N_MAX:
                self._evict_top(EVICT_BATCH)
        return block

    def _evict_top(self, n: int) -> None:
        """Unmount the first n mounted CopyableBlocks and advance _window_start.

        Safe to call only when self._stick_to_bottom is True — the user is at
        the tail, so removing widgets above the viewport doesn't disturb them.
        """
        t = self._transcript()
        blocks = list(t.query(CopyableBlock))
        # The first mounted block corresponds to _history[_window_start].
        # CopyableBlocks may be interleaved with banners (resume-banner,
        # resume-failure) and the WorkingIndicator — only CopyableBlocks count.
        for b in blocks[:n]:
            with contextlib.suppress(Exception):
                b.remove()
        self._window_start += n
```

Note: `contextlib` is already imported at the top of the file.

- [ ] **Step 4: Run the new tests**

```bash
uv run pytest tests/test_pane_windowing.py -v
```

Expected: all pass.

- [ ] **Step 5: Regression sweep**

```bash
uv run pytest tests/test_tui.py tests/test_pane_replay.py tests/test_pane_windowing.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/tui/pane.py tests/test_pane_windowing.py
git commit -m "feat(tui): evict top blocks when mounted count exceeds N_MAX"
```

---

### Task 5: Debounced scroll-up reloads older blocks

**Files:**
- Modify: `src/aegis/tui/pane.py`
- Test: `tests/test_pane_windowing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pane_windowing.py`:

```python
@pytest.mark.asyncio
async def test_scroll_up_reloads_older_blocks():
    """Scrolling to the top re-mounts up to LOAD_BATCH older blocks."""
    import asyncio
    from textual.containers import VerticalScroll
    from aegis.tui.pane import N_MAX, LOAD_BATCH, DEBOUNCE_S
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        # Build a history big enough that eviction has happened.
        for i in range(N_MAX + 200):
            pane._on_core_event(None, ToolUse(
                name="Read", summary=f"f{i}.py", kind="read"))
        await pilot.pause()
        await pilot.pause()
        start_before = pane._window_start
        assert start_before > 0  # eviction ran

        # Scroll to top to trigger load-older.
        t = pane.query_one("#transcript", VerticalScroll)
        t.scroll_y = 0
        # Allow debounce + load to run.
        await asyncio.sleep(DEBOUNCE_S + 0.1)
        await pilot.pause()
        await pilot.pause()

        # _window_start moved back by LOAD_BATCH (or to 0).
        expected = max(0, start_before - LOAD_BATCH)
        assert pane._window_start == expected
        # Mounted widgets grew accordingly.
        assert len(pane.query(CopyableBlock)) >= start_before - expected


@pytest.mark.asyncio
async def test_load_older_is_idempotent_while_pending():
    """Multiple rapid scroll events near the top coalesce into one load."""
    import asyncio
    from textual.containers import VerticalScroll
    from aegis.tui.pane import N_MAX, LOAD_BATCH, DEBOUNCE_S
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for i in range(N_MAX + 250):
            pane._on_core_event(None, ToolUse(
                name="Read", summary=f"f{i}.py", kind="read"))
        await pilot.pause()
        start_before = pane._window_start

        t = pane.query_one("#transcript", VerticalScroll)
        # Burst of three scroll-near-top events before the timer fires.
        t.scroll_y = 0
        t.scroll_y = 1
        t.scroll_y = 0
        await asyncio.sleep(DEBOUNCE_S + 0.1)
        await pilot.pause()
        # Only one batch loaded, not three.
        assert pane._window_start == max(0, start_before - LOAD_BATCH)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_pane_windowing.py -v -k "scroll_up or idempotent"
```

Expected: FAIL — `_window_start` does not move on scroll-up.

- [ ] **Step 3: Extend the scroll watcher and add `_load_older`**

Replace `_on_scroll_y` with the version that also schedules load-older:

```python
    def _on_scroll_y(self, _value: float) -> None:
        t = self._transcript()
        self._stick_to_bottom = (
            (t.max_scroll_y - t.scroll_y) <= STICKY_EPS)
        near_top = t.scroll_y <= LOAD_MORE_EPS
        if near_top and self._window_start > 0 and not self._loading_older:
            if self._load_timer is not None:
                with contextlib.suppress(Exception):
                    self._load_timer.stop()
            self._load_timer = self.set_timer(
                DEBOUNCE_S, self._load_older)
```

Add the `_load_older` method (place it just below `_on_scroll_y`):

```python
    def _load_older(self) -> None:
        if self._loading_older or self._window_start == 0:
            return
        self._loading_older = True
        try:
            t = self._transcript()
            new_start = max(0, self._window_start - LOAD_BATCH)
            existing = list(t.query(CopyableBlock))
            anchor = existing[0] if existing else None
            anchor_y_before = (
                (anchor.region.y - t.region.y) if anchor is not None else 0)
            for rec in self._history[new_start : self._window_start]:
                block = CopyableBlock(
                    rec.renderable, rec.payload, tight=rec.tight)
                if anchor is not None:
                    t.mount(block, before=anchor)
                else:
                    t.mount(block)
            self._window_start = new_start

            def _restore() -> None:
                if anchor is None:
                    return
                anchor_y_after = anchor.region.y - t.region.y
                delta = anchor_y_after - anchor_y_before
                if delta:
                    t.scroll_to(
                        y=t.scroll_y + delta, animate=False)
                self._loading_older = False

            self.call_after_refresh(_restore)
        except Exception:
            self._loading_older = False
            raise
```

Note: `_loading_older` is cleared inside `_restore` (after layout) so a
new scroll-up trigger during anchor-restore doesn't double-fire. The
`except` branch resets it on the synchronous failure path.

- [ ] **Step 4: Run the new tests**

```bash
uv run pytest tests/test_pane_windowing.py -v
```

Expected: all pass.

- [ ] **Step 5: Regression sweep**

```bash
uv run pytest tests/test_tui.py tests/test_pane_replay.py tests/test_pane_windowing.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/tui/pane.py tests/test_pane_windowing.py
git commit -m "feat(tui): debounced scroll-up restores older blocks with anchor preservation"
```

---

### Task 6: Window the initial replay

**Files:**
- Modify: `src/aegis/tui/pane.py`
- Test: `tests/test_pane_windowing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pane_windowing.py`:

```python
@pytest.mark.asyncio
async def test_replay_populates_full_history_but_mounts_at_most_n_max():
    """_mount_replay fills _history from the full replay yet keeps the
    mounted set bounded by N_MAX. Drives the existing pane's replay
    machinery directly rather than constructing a second pane (which
    would bypass AegisApp._panes bookkeeping)."""
    from aegis.state.session_log import EventReplay
    from aegis.tui.pane import N_MAX

    events = [
        ToolUse(name="Read", summary=f"f{i}.py", kind="read")
        for i in range(N_MAX + 150)
    ]

    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        # Wipe any state from the default on_mount so we exercise replay
        # on a clean pane.
        for b in list(pane.query(CopyableBlock)):
            b.remove()
        pane._history.clear()
        pane._window_start = 0
        pane._replay = EventReplay(events=events, interrupted=False)
        await pilot.pause()

        pane._mount_replay()
        await pilot.pause()
        await pilot.pause()

        assert len(pane._history) == N_MAX + 150
        assert len(pane.query(CopyableBlock)) <= N_MAX
        # _trim_to_window enforces the exact invariant at end-of-replay.
        assert pane._window_start == len(pane._history) - N_MAX
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_pane_windowing.py::test_replay_populates_full_history_but_mounts_at_most_n_max -v
```

Expected: FAIL — `_window_start` stays at 0 and all blocks are mounted.

- [ ] **Step 3: Add `_trim_to_window` and call it at end of `_mount_replay`**

Add this helper method on `ConversationPane` (anywhere; e.g. just below
`_evict_top`):

```python
    def _trim_to_window(self) -> None:
        """Reduce the mounted set to the last N_MAX records.

        Used at startup after replay-driven mounting fills the history.
        Equivalent to a forced eviction independent of the sticky flag.
        """
        excess = (len(self._history) - self._window_start) - N_MAX
        if excess <= 0:
            return
        t = self._transcript()
        blocks = list(t.query(CopyableBlock))
        for b in blocks[:excess]:
            with contextlib.suppress(Exception):
                b.remove()
        self._window_start += excess
```

Update `_mount_replay` to call it at the very end:

```python
    def _mount_replay(self) -> None:
        """Paint prior events from a replay onto the transcript, then
        mark an interrupted turn if the session ended mid-turn. Trims
        the mounted set down to N_MAX so resumed long sessions don't
        start out laggy."""
        if self._replay is None:
            return
        for ev in self._replay.events:
            self._on_core_event(None, ev)
        if self._replay.interrupted:
            self._flush_streaming()
            self._mount_block(
                Text("⚠ interrupted", style="yellow"),
                "⚠ interrupted")
        self._trim_to_window()
```

- [ ] **Step 4: Run the new test**

```bash
uv run pytest tests/test_pane_windowing.py::test_replay_populates_full_history_but_mounts_at_most_n_max -v
```

Expected: PASS.

- [ ] **Step 5: Full regression sweep**

```bash
uv run pytest -q -m "not live"
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/tui/pane.py tests/test_pane_windowing.py
git commit -m "feat(tui): trim initial replay to N_MAX so resumed sessions start snappy"
```

---

### Task 7: Manual smoke test in a real TUI session

**Files:** none (verification only)

- [ ] **Step 1: Start aegis in this repo and pump a long session**

From `/home/apiad/Workspace/repos/aegis/` with `.aegis.yaml` present:

```bash
uv run aegis
```

Drive a conversation that produces well over 300 events (e.g. ask the
agent to enumerate something long, or paste a multi-step task). Observe:

- Scrolling stays smooth — no perceptible lag at the bottom even after
  500+ events.
- Scrolling up from the bottom loads older content in batches (~100 per
  trigger), with no visible jump-to-top.
- New events keep auto-scrolling while you're at the bottom.
- Scrolling up partway and waiting for a new event: viewport stays put,
  new content appears below the fold (no auto-scroll).
- Scrolling back to the bottom and a new event arrives: eviction quietly
  trims old content; mounted count stays bounded.

- [ ] **Step 2: Exit cleanly with `ctrl+q`**

No tracebacks expected at shutdown.

- [ ] **Step 3: Push the branch**

```bash
git push origin main
```

(Per Alex's standing instruction: aegis work commits to `main`.)

---

## Notes for the executor

- All production changes are in `src/aegis/tui/pane.py`. No new modules, no
  config schema changes.
- `BlockRecord` is intentionally tiny and mutable: streaming aggregation
  needs in-place mutation; making it frozen would cost a re-allocation per
  chunk on long streamed turns.
- The eviction call site (`_mount_block`) is the *only* place that mutates
  `_window_start` forward. The load-older path is the only place that
  moves it backward. This invariant is what keeps reasoning simple.
- The scroll watcher fires on every wheel tick — it is on the hot path.
  Keep its body fast (no allocations, no queries beyond the cached
  transcript). The debounced timer absorbs bursts before any real work
  happens.
- If `call_after_refresh` proves flaky for anchor restoration in CI (it
  has been historically reliable in Textual 8.x), fall back to
  `await asyncio.sleep(0); _restore()` in a worker. Spec leaves this open.
