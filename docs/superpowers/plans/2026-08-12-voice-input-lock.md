# Voice Input Lock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock the input while the mic is open and while the clip decodes,
show a spinner + timer for both phases, and stop discarding text typed during
either.

**Architecture:** A composed one-row `VoiceStrip` above the input, modeled on
`WorkingIndicator`. `GrowingInput` gains a lock (Textual's `read_only` plus a
submit guard). `ConversationPane.set_voice_state(state)` drives all three
surfaces from one place, and `AegisApp` owns the three transitions.

**Tech Stack:** Python 3.13, Textual 8.2.6, pytest + pytest-asyncio. Textual
tests use `app.run_test()`. Package manager is `uv` — run tests with
`uv run pytest`, never bare `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-12-voice-input-lock-design.md`

## Global Constraints

- Work directly on `main`. This repo does not use feature branches.
- Code, comments, identifiers and commit messages in English.
- Never `git add -A` / `git add .`. Stage the explicit paths named in each
  step — this checkout is shared with other live sessions.
- `cd /home/apiad/Workspace/repos/aegis` at the start of every shell command.
  The Bash tool's cwd persists across calls, and `uv run` from the Workspace
  root picks up the wrong environment and fails with
  `ModuleNotFoundError: No module named 'pytest'`.
- Run the gate as its own tool call. Never pipe pytest into `tail`/`head` and
  never append `; echo rc=$?` — both hand the shell a 0 and turn a red gate
  green.
- The full suite has **6 known pre-existing failures**, all in `*_live.py`
  (4 gemini/opencode, `test_scheduler_live`, `test_skill_system_live`).
  A run is green at `6 failed, N passed`.
- **Keep the CSS class name `recording`.** `tests/test_voice_action.py`
  asserts `pane.has_class("recording")` in two places, and the rule at
  `tui/pane.py:812-815` keys on it. The new states add to it, never rename it.

---

### Task 1: The `VoiceStrip` widget

Pure widget: no app, no mic, no voice session. Testable on its own.

**Files:**
- Create: `src/aegis/tui/voice_strip.py`
- Test: `tests/test_voice_strip.py` (create)

**Interfaces:**
- Produces:
  - `VoiceStrip(palette)` — a `Static`, `id="voice-strip"`
  - `VoiceStrip.show_recording(key: str) -> None`
  - `VoiceStrip.show_transcribing() -> None`
  - `VoiceStrip.hide() -> None`
  - `VoiceStrip.set_animating(on: bool) -> None`
  - `VoiceStrip.is_active` property
  - `_fmt_clock(seconds: float) -> str` returning `"0:04"` / `"1:07"`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_voice_strip.py`:

```python
"""The recording/transcribing indicator: states, clock, and timer hygiene."""
from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from aegis.themes import AegisColors
from aegis.tui.voice_strip import VoiceStrip, _fmt_clock

# Same shape tests/test_quota_render.py uses — a literal palette, no theme
# loading. AegisColors has NO `warning` field: the recording colour is
# `working`, which aegis_colors() maps from theme.warning — the same colour
# the .recording border already uses via Textual's $warning.
COLORS = AegisColors(
    ready="green", working="yellow", error="red", accent="blue",
    muted="grey50", ok="green", err="red", user="blue", user_bg="black")


def _palette():
    return COLORS


class _Host(App):
    def compose(self) -> ComposeResult:
        yield VoiceStrip(_palette())


@pytest.mark.parametrize("secs,want", [
    (0, "0:00"), (4, "0:04"), (9.9, "0:09"), (60, "1:00"), (67, "1:07"),
    (600, "10:00"),
])
def test_fmt_clock(secs, want):
    """A recording timer reads as a clock, not as a duration measurement —
    which is why this is not _fmt_elapsed ('4.2s', '1m 03s')."""
    assert _fmt_clock(secs) == want


@pytest.mark.asyncio
async def test_hidden_until_started():
    app = _Host()
    async with app.run_test():
        strip = app.query_one(VoiceStrip)
        assert strip.has_class("-empty")
        assert strip.is_active is False


@pytest.mark.asyncio
async def test_recording_shows_the_key_it_was_given():
    """The binding is configurable, so the strip must never hardcode
    ctrl+g — a rebound key would be told to press the wrong one."""
    app = _Host()
    async with app.run_test():
        strip = app.query_one(VoiceStrip)
        strip.show_recording("ctrl+shift+v")
        shown = str(strip.render())
        assert "Recording" in shown
        assert "ctrl+shift+v" in shown
        assert "ctrl+g" not in shown
        assert not strip.has_class("-empty")
        assert strip.is_active is True


@pytest.mark.asyncio
async def test_transcribing_replaces_recording():
    app = _Host()
    async with app.run_test():
        strip = app.query_one(VoiceStrip)
        strip.show_recording("ctrl+g")
        strip.show_transcribing()
        shown = str(strip.render())
        assert "Transcribing" in shown
        assert "Recording" not in shown
        # The clock restarts: this phase answers "is it stuck?", not
        # "how long did I talk?"
        assert "0:00" in shown


@pytest.mark.asyncio
async def test_hide_clears_and_stops_timers():
    app = _Host()
    async with app.run_test():
        strip = app.query_one(VoiceStrip)
        strip.show_recording("ctrl+g")
        strip.hide()
        assert strip.has_class("-empty")
        assert strip.is_active is False
        assert strip._tick_timer is None, "a hidden strip must not keep ticking"


@pytest.mark.asyncio
async def test_restart_leaks_no_timers():
    """Two recordings in a row must not leave the first one's timer
    running — the WorkingIndicator pattern this copies cancels first."""
    app = _Host()
    async with app.run_test():
        strip = app.query_one(VoiceStrip)
        strip.show_recording("ctrl+g")
        first = strip._tick_timer
        strip.show_recording("ctrl+g")
        assert strip._tick_timer is not first


@pytest.mark.asyncio
async def test_set_animating_freezes_without_losing_state():
    app = _Host()
    async with app.run_test():
        strip = app.query_one(VoiceStrip)
        strip.show_recording("ctrl+g")
        strip.set_animating(False)
        assert strip._tick_timer is None
        assert strip.is_active is True, "freezing is not stopping"
        strip.set_animating(True)
        assert strip._tick_timer is not None
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/test_voice_strip.py -p no:randomly -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.tui.voice_strip'`.

- [ ] **Step 3: Write the widget**

Create `src/aegis/tui/voice_strip.py`:

```python
"""The voice indicator: one row above the input while the mic is open and
while the clip decodes.

Modeled on ``WorkingIndicator`` (``tui/pane.py``) — same 10 Hz tick, same
cancel-first restart, same ``set_animating`` seam so a backgrounded pane
stops redrawing. Elapsed time comes from ``time.monotonic()``, so a frozen
strip's clock is still right when the pane comes back.
"""
from __future__ import annotations

import contextlib
import time

from rich.text import Text
from textual.widgets import Static

# A filled dot reads as a recording light on sight; the braille spinner
# means what it means in WorkingIndicator — the machine is busy, you wait.
_PULSE_FRAMES = "●●●●○○○○"
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_TICK = 0.1


def _fmt_clock(seconds: float) -> str:
    """``0:04`` / ``1:07``. Deliberately not ``_fmt_elapsed`` — a recording
    timer is read as a clock, not as a measurement of a duration."""
    total = int(seconds)
    m, s = divmod(total, 60)
    return f"{m}:{s:02d}"


class VoiceStrip(Static):
    """Hidden when idle; one row while recording or transcribing."""

    DEFAULT_CSS = """
    VoiceStrip { height: 1; padding: 0 2; background: transparent; }
    VoiceStrip.-empty { display: none; }
    """

    def __init__(self, palette) -> None:
        super().__init__("", id="voice-strip")
        self._palette = palette
        self._state = "idle"
        self._key = ""
        self._started_at: float | None = None
        self._frame = 0
        self._tick_timer = None
        self.add_class("-empty")

    @property
    def is_active(self) -> bool:
        return self._state != "idle"

    def show_recording(self, key: str) -> None:
        self._key = key
        self._enter("recording")

    def show_transcribing(self) -> None:
        self._enter("transcribing")

    def _enter(self, state: str) -> None:
        # Cancel first: restarting a live strip must neither leak the old
        # timer nor leave a frozen glyph.
        self._cancel_timers()
        self._state = state
        self._started_at = time.monotonic()
        self._frame = 0
        self.remove_class("-empty")
        self._refresh()
        self._start_timers()

    def hide(self) -> None:
        self._cancel_timers()
        self._state = "idle"
        self._started_at = None
        self.add_class("-empty")
        self.update("")

    def set_animating(self, on: bool) -> None:
        """Freeze or resume the redraw without touching state, so a hidden
        pane costs no pump. No-op while idle."""
        if not self.is_active:
            return
        running = self._tick_timer is not None
        if on and not running:
            self._refresh()
            self._start_timers()
        elif not on and running:
            self._cancel_timers()

    def _start_timers(self) -> None:
        self._tick_timer = self.set_interval(_TICK, self._tick)

    def _cancel_timers(self) -> None:
        if self._tick_timer is not None:
            with contextlib.suppress(Exception):
                self._tick_timer.stop()
        self._tick_timer = None

    def _tick(self) -> None:
        self._frame += 1
        self._refresh()

    def _refresh(self) -> None:
        if self._started_at is None:
            return
        clock = _fmt_clock(time.monotonic() - self._started_at)
        if self._state == "recording":
            glyph = _PULSE_FRAMES[self._frame % len(_PULSE_FRAMES)]
            body = f"{glyph}  Recording  {clock} — {self._key} to stop"
            style = self._palette.working
        else:
            glyph = _SPINNER_FRAMES[self._frame % len(_SPINNER_FRAMES)]
            body = f"{glyph}  Transcribing  {clock}"
            style = self._palette.muted
        # layout=False: 10 Hz for the whole recording, and the row's height
        # is fixed by its own CSS, so it can never need a re-layout.
        self.update(Text(body, style=f"italic {style}"), layout=False)
```

- [ ] **Step 4: Run to verify they pass**

```bash
cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/test_voice_strip.py -p no:randomly -q
```

Expected: PASS (12 passed — 6 parametrised `_fmt_clock` + 6 widget tests).

- [ ] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/voice_strip.py tests/test_voice_strip.py
git commit -m "feat(tui): a voice strip with a pulse, a spinner and a clock

One row above the input, hidden when idle. Modeled on WorkingIndicator:
same 10 Hz tick, cancel-first restart, and a set_animating seam so a
backgrounded pane stops redrawing while its clock stays correct.

The key is a parameter, never a literal: the binding is configurable, so
a hardcoded ctrl+g would tell anyone who rebound it to press the wrong
key."
```

---

### Task 2: Lock `GrowingInput`

Independently testable: a widget-level lock with no voice involved.

**Files:**
- Modify: `src/aegis/tui/widgets.py` (`action_submit` at line 100)
- Test: `tests/test_growing_input_lock.py` (create)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `GrowingInput.locked` property (`bool`, get/set). Setting it
  drives Textual's inherited `read_only` and gates `action_submit`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_growing_input_lock.py`:

```python
"""While the mic is open the input is not editable and cannot be sent."""
from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from aegis.tui.widgets import GrowingInput


class _Host(App):
    def compose(self) -> ComposeResult:
        yield GrowingInput(placeholder="type…")


@pytest.mark.asyncio
async def test_locked_sets_read_only():
    app = _Host()
    async with app.run_test():
        inp = app.query_one(GrowingInput)
        assert inp.locked is False
        assert inp.read_only is False
        inp.locked = True
        assert inp.read_only is True, "the lock rides Textual's read_only"


@pytest.mark.asyncio
async def test_unlocking_restores_editing():
    app = _Host()
    async with app.run_test():
        inp = app.query_one(GrowingInput)
        inp.locked = True
        inp.locked = False
        assert inp.read_only is False


@pytest.mark.asyncio
async def test_typing_is_ignored_while_locked():
    app = _Host()
    async with app.run_test() as pilot:
        inp = app.query_one(GrowingInput)
        inp.value = "kept"
        inp.focus()
        inp.locked = True
        await pilot.press("x", "y", "z")
        await pilot.pause()
        assert inp.value == "kept", "keystrokes must not reach a locked input"


@pytest.mark.asyncio
async def test_submit_is_refused_while_locked():
    """Submit clears the input, and the pending transcript assignment would
    then resurrect the text just sent."""
    app = _Host()
    async with app.run_test():
        inp = app.query_one(GrowingInput)
        inp.value = "half a message"
        sent: list[str] = []
        inp.post_message = lambda m: sent.append(getattr(m, "value", None))
        inp.locked = True
        await inp.action_submit()
        assert sent == [], "a locked input must not submit"


@pytest.mark.asyncio
async def test_submit_works_again_after_unlock():
    app = _Host()
    async with app.run_test():
        inp = app.query_one(GrowingInput)
        inp.value = "a message"
        sent: list[str] = []
        inp.post_message = lambda m: sent.append(getattr(m, "value", None))
        inp.locked = True
        await inp.action_submit()
        inp.locked = False
        await inp.action_submit()
        assert sent == ["a message"]
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/test_growing_input_lock.py -p no:randomly -q
```

Expected: FAIL — `AttributeError: 'GrowingInput' object has no attribute 'locked'`.

- [ ] **Step 3: Add the lock**

In `src/aegis/tui/widgets.py`, add the property next to the existing `value`
property (around line 55) :

```python
    @property
    def locked(self) -> bool:
        """True while the mic is open or the clip is decoding.

        Rides Textual's ``read_only`` for text mutation and paste, which it
        already handles; ``action_submit`` is gated separately because it is
        an action, not a key handler, and ``read_only`` does not cover it.
        """
        return bool(getattr(self, "_locked", False))

    @locked.setter
    def locked(self, on: bool) -> None:
        self._locked = bool(on)
        self.read_only = bool(on)
```

And guard `action_submit` (line 100) by making its body start with:

```python
    async def action_submit(self, kind: str = "enqueue") -> None:
        # Submit clears the input, and a transcript landing afterwards would
        # write the sent text straight back into the box.
        if self.locked:
            return
        self._record_history(self.text)
```

- [ ] **Step 4: Run to verify they pass**

```bash
cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/test_growing_input_lock.py -p no:randomly -q
```

Expected: PASS (5 passed).

- [ ] **Step 5: Prove the gate can fail**

Temporarily delete the `if self.locked: return` guard from `action_submit`
and re-run Step 4. Expected: `test_submit_is_refused_while_locked` FAILS.
Restore it and re-run to confirm green.

- [ ] **Step 6: Run the input's own suite**

```bash
cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/test_growing_input_lock.py tests/test_growing_input_history.py tests/test_growing_input_keys.py tests/test_pane_input_state_outline.py tests/test_tui.py -p no:randomly -q
```

Expected: PASS. `test_pane_input_state_outline.py` is in the list on purpose:
it pins the input's outline classes, which the new `transcribing` class
joins.

- [ ] **Step 7: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/widgets.py tests/test_growing_input_lock.py
git commit -m "feat(tui): a lock on GrowingInput

Rides Textual's read_only for typing and paste; action_submit is gated
separately because it is an action rather than a key handler, so
read_only does not reach it.

Blocking submit is not cosmetic: submit clears the input, and a
transcript landing afterwards would write the sent text back into the
box."
```

---

### Task 3: Wire the states, and stop discarding text

The behaviour change. Everything before this was inert.

**Files:**
- Modify: `src/aegis/tui/pane.py` — compose (line 1036), `set_recording`
  (line 1792)
- Modify: `src/aegis/tui/app.py` — `action_toggle_voice` (1465),
  `_apply_voice_text` (1500), `_stop_voice` (1507)
- Test: `tests/test_voice_action.py` (extend)

**Interfaces:**
- Consumes: `VoiceStrip` (Task 1), `GrowingInput.locked` (Task 2).
- Produces: `ConversationPane.set_voice_state(state: str) -> None` over
  `"idle" | "recording" | "transcribing"`, replacing `set_recording(bool)`.

- [ ] **Step 1: Write the failing tests**

The existing `_StubVoice` calls `on_final` **synchronously inside `stop()`**,
so the transcribing window never exists for it. These tests need a stub that
defers delivery. Append to `tests/test_voice_action.py`:

```python
class _DeferredStubVoice:
    """Like _StubVoice, but the transcript lands only when the test says so
    — which is the only way the transcribing window is observable."""
    last = None

    def __init__(self, cfg, on_final, **_):
        self.cfg = cfg
        self.on_final = on_final
        self._running = False
        _DeferredStubVoice.last = self

    @property
    def is_running(self):
        return self._running

    def start(self):
        self._running = True

    def stop(self):
        self._running = False      # decode "in flight"; no on_final yet

    def deliver(self, text):
        self.on_final(text)


async def _start_recording(app):
    await app.action_toggle_voice()
    return app._active


@pytest.mark.asyncio
async def test_input_locked_while_recording(monkeypatch):
    monkeypatch.setattr("aegis.tui.app.voice_available", lambda: True)
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _DeferredStubVoice
    async with app.run_test():
        pane = await _start_recording(app)
        assert pane.input_widget().locked is True


@pytest.mark.asyncio
async def test_input_stays_locked_while_transcribing(monkeypatch):
    """The overwrite window is record AND decode, so the lock spans both."""
    monkeypatch.setattr("aegis.tui.app.voice_available", lambda: True)
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _DeferredStubVoice
    async with app.run_test() as pilot:
        pane = await _start_recording(app)
        await app.action_toggle_voice()          # stop -> transcribing
        await pilot.pause()
        assert pane.input_widget().locked is True
        assert pane.has_class("transcribing")
        _DeferredStubVoice.last.deliver("spoken")
        await pilot.pause()
        assert pane.input_widget().locked is False
        assert not pane.has_class("transcribing")


@pytest.mark.asyncio
async def test_text_typed_before_recording_survives(monkeypatch):
    """The regression: the transcript appends to what is there now, not to
    a value captured when recording started."""
    monkeypatch.setattr("aegis.tui.app.voice_available", lambda: True)
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _DeferredStubVoice
    async with app.run_test() as pilot:
        pane = app._active
        pane.input_widget().value = "prefix "
        await app.action_toggle_voice()
        await app.action_toggle_voice()
        _DeferredStubVoice.last.deliver("hello world")
        await pilot.pause()
        assert pane.input_widget().value == "prefix hello world"


@pytest.mark.asyncio
async def test_empty_transcript_still_unlocks(monkeypatch):
    """A recording with no speech returns early — and would strand the
    input locked and the strip spinning forever."""
    monkeypatch.setattr("aegis.tui.app.voice_available", lambda: True)
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _DeferredStubVoice
    async with app.run_test() as pilot:
        pane = await _start_recording(app)
        await app.action_toggle_voice()
        _DeferredStubVoice.last.deliver("")
        await pilot.pause()
        assert pane.input_widget().locked is False
        assert not pane.has_class("recording")
        assert not pane.has_class("transcribing")


@pytest.mark.asyncio
async def test_second_press_while_transcribing_is_ignored(monkeypatch):
    """_stop_voice clears _voice before the decode runs, so without a
    guard a second press starts a NEW recording whose state the in-flight
    decode then clears."""
    monkeypatch.setattr("aegis.tui.app.voice_available", lambda: True)
    app = _app(voice=VoiceConfig(enabled=True))
    app._voice_session_factory = _DeferredStubVoice
    async with app.run_test() as pilot:
        pane = await _start_recording(app)
        first = _DeferredStubVoice.last
        await app.action_toggle_voice()          # -> transcribing
        await app.action_toggle_voice()          # must be ignored
        await pilot.pause()
        assert _DeferredStubVoice.last is first, "no new recording started"
        assert app._voice is None
        assert pane.has_class("transcribing")
        first.deliver("done")
        await pilot.pause()
        assert pane.input_widget().locked is False


@pytest.mark.asyncio
async def test_strip_shows_the_configured_key(monkeypatch):
    monkeypatch.setattr("aegis.tui.app.voice_available", lambda: True)
    app = _app(voice=VoiceConfig(enabled=True, key="ctrl+shift+v"))
    app._voice_session_factory = _DeferredStubVoice
    async with app.run_test() as pilot:
        from aegis.tui.voice_strip import VoiceStrip
        pane = await _start_recording(app)
        await pilot.pause()
        assert "ctrl+shift+v" in str(pane.query_one(VoiceStrip).render())
```

**On the spec's "a decode failure still unlocks": no test is added here, and
that is not an oversight.** The chain is already pinned end to end across two
files. `tests/test_voice_session.py:44`
(`test_decode_error_delivers_empty_string`) proves a raising decode still
calls `on_final("")`, and `test_empty_transcript_still_unlocks` above proves
`""` unlocks. A third test at the app level would deliver `""` through the
same stub as the empty-transcript case and assert the same thing — a
duplicate wearing a different name.

- [ ] **Step 2: Run to verify they fail**

```bash
cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/test_voice_action.py -p no:randomly -q
```

Expected: the six new tests FAIL (`AttributeError: 'GrowingInput' object has
no attribute 'locked'` is already fixed by Task 2, so these fail on the
missing `transcribing` class and the missing `VoiceStrip` in the pane). The
three pre-existing tests still PASS — they use the synchronous `_StubVoice`
and assert `has_class("recording")`, which is preserved.

- [ ] **Step 3: Mount the strip in the pane**

In `src/aegis/tui/pane.py`, import it near the other strip imports:

```python
from aegis.tui.voice_strip import VoiceStrip
```

and yield it in compose immediately before `GrowingInput` (line 1037):

```python
                yield PendingStrip(self._palette)
                yield VoiceStrip(self._palette)
                yield GrowingInput(placeholder="type a message…")
```

- [ ] **Step 4: Replace `set_recording` with `set_voice_state`**

In `src/aegis/tui/pane.py`, replace the method at line 1792:

```python
    def set_voice_state(self, state: str) -> None:
        """Drive every voice surface from one place — the CSS class, the
        strip, and the input lock — so the three cannot drift apart.

        ``state`` is "idle", "recording" or "transcribing". The "recording"
        class name is kept from the old set_recording(bool): the stylesheet
        and two existing tests key on it.
        """
        self.set_class(state == "recording", "recording")
        self.set_class(state == "transcribing", "transcribing")
        self.input_widget().locked = state != "idle"
        strip = self.query_one(VoiceStrip)
        if state == "recording":
            strip.show_recording(self._voice_key)
        elif state == "transcribing":
            strip.show_transcribing()
        else:
            strip.hide()
```

The pane needs the key to render. Add a plain attribute in
`ConversationPane.__init__` (beside the other display fields):

```python
        # The voice binding, for the strip's "<key> to stop" hint. Set by
        # the app at mount; the binding is configurable, so this is never a
        # literal.
        self._voice_key = "ctrl+g"
```

and add the transcribing border rule beside the recording one at
`tui/pane.py:812`:

```css
    ConversationPane.transcribing GrowingInput,
    ConversationPane.transcribing GrowingInput:focus {
                             border-top: solid $primary;
                             border-bottom: solid $primary; }
```

- [ ] **Step 5: Drive the three transitions from the app**

In `src/aegis/tui/app.py`:

Add the decoding flag beside `self._voice` in `__init__` (line 338):

```python
        # True from _stop_voice until the transcript lands. _voice is
        # already None during that window, so without this the voice key
        # would start a fresh recording mid-decode.
        self._voice_decoding = False
```

In `action_toggle_voice` (line 1465), make the first two statements:

```python
    async def action_toggle_voice(self) -> None:
        if self._voice_decoding:
            return          # decode in flight; a new recording would race it
        if self._voice is not None:
            self._stop_voice()
            return
```

Delete the `base` capture (line 1475) and simplify the callback:

```python
        def on_final(text: str, pane=pane) -> None:
            # Fires from the decode worker thread -> marshal onto the UI
            # loop. The pane is captured per-recording so a late decode
            # always lands on the input it started from.
            self._marshal(self._apply_voice_text, pane, text)
```

Tell the pane which key to advertise, and replace the state call at the end
of `action_toggle_voice` (line 1491):

```python
        self._voice_pane = pane
        pane._voice_key = self._voice_cfg.key
        pane.set_voice_state("recording")
```

Replace `_apply_voice_text` (line 1500) entirely:

```python
    def _apply_voice_text(self, pane, text: str) -> None:
        # Unlock FIRST and unconditionally. An empty transcript and a failed
        # decode both reach here, and both used to return early — which
        # would now strand the input locked and the strip spinning.
        self._voice_decoding = False
        pane.set_voice_state("idle")
        text = (text or "").strip()
        if not text:
            return
        # Read the CURRENT value, not one captured when recording started.
        # That capture is what discarded anything typed in between; reading
        # it here means a hole in the lock degrades to "both survive, in
        # order" rather than "your text vanishes".
        base = pane.input_widget().value
        joiner = "" if (not base or base.endswith((" ", "\n"))) else " "
        pane.input_widget().value = base + joiner + text
```

Replace the body of `_stop_voice` (line 1507):

```python
    def _stop_voice(self) -> None:
        voice, pane = self._voice, self._voice_pane
        self._voice = None
        # Deliberately NOT clearing _voice_pane: _apply_voice_text still
        # needs it, and the callback holds its own reference anyway.
        if voice is not None:
            self._voice_decoding = True
            if pane is not None:
                pane.set_voice_state("transcribing")
            voice.stop()   # non-blocking; decode + insert happen off-thread
        elif pane is not None:
            pane.set_voice_state("idle")
```

- [ ] **Step 6: Run the voice suite**

```bash
cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/test_voice_action.py tests/test_voice_strip.py tests/test_growing_input_lock.py -p no:randomly -q
```

Expected: PASS — 9 in `test_voice_action.py` (3 pre-existing + 6 new), 12 in
`test_voice_strip.py`, 5 in `test_growing_input_lock.py`.

- [ ] **Step 7: Prove the strand guard can fail**

Move `pane.set_voice_state("idle")` in `_apply_voice_text` to *after* the
`if not text: return`, then re-run Step 6. Expected:
`test_empty_transcript_still_unlocks` FAILS. Restore it and re-run to confirm
green. This is the edge most likely to be "tidied" back later.

- [ ] **Step 8: Check for other `set_recording` callers**

```bash
cd /home/apiad/Workspace/repos/aegis && grep -rn "set_recording" src/ tests/
```

Expected: no hits. Any remaining caller is a `TypeError` waiting to happen —
fix it to `set_voice_state` before continuing.

- [ ] **Step 9: Run the full suite as the real gate**

This changes a widget every pane composes, so the subset is not enough. Run
it as its own tool call, unpiped:

```bash
cd /home/apiad/Workspace/repos/aegis && uv run pytest -p no:randomly -q
```

Expected: `6 failed, N passed`, and the 6 are exactly the known `*_live.py`
failures from Global Constraints. Any other failure is yours.

- [ ] **Step 10: Update the changelog**

In `CHANGELOG.md`, under `## [Unreleased]`, add to the existing `### Added`
section:

```markdown
- **The input locks while the mic is open, and says so.** A one-row strip
  above the input shows `● Recording 0:04 — ctrl+g to stop` and then
  `⠋ Transcribing 0:02`, with the key it is actually bound to rather than a
  hardcoded one. Typing and submitting are refused for the whole span.

  This was losing text. The input's contents were captured when recording
  *started* and reassigned when the transcript landed, so anything typed in
  between — during the recording *or* during the decode, which had no
  indicator at all — was silently overwritten. The transcript now appends to
  whatever is in the box at the moment it arrives.

  Pressing the voice key again during the decode is ignored. It used to
  start a fresh recording, because the session handle is already cleared by
  then, and the in-flight decode would have cleared the new recording's
  state.
```

- [ ] **Step 11: Commit and push**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/pane.py src/aegis/tui/app.py tests/test_voice_action.py CHANGELOG.md
git commit -m "feat(voice): lock the input while recording and transcribing

set_recording(bool) becomes set_voice_state(idle|recording|transcribing),
driving the CSS class, the strip and the input lock from one place so the
three cannot drift apart.

Fixes the defect under it: _apply_voice_text read a value captured when
recording started and ASSIGNED base + transcript, so anything typed in
between was discarded. It now reads the current value at apply time.

Three edges the lock would otherwise have broken: an empty transcript and
a failed decode both returned early, which would strand the input locked
forever — so the unlock is now the first thing that happens,
unconditionally. And a second voice-key press during the decode started a
fresh recording (_voice is already None by then), which the in-flight
decode would then clear; it is now ignored."
git push origin main
```

- [ ] **Step 12: Mark the spec implemented**

In `docs/superpowers/specs/2026-08-12-voice-input-lock-design.md`, change the
status line to:

```markdown
**Status:** implemented 2026-08-12; plan at
`docs/superpowers/plans/2026-08-12-voice-input-lock.md`.
```

Add a `## Deviations` section if anything in this plan turned out wrong, then:

```bash
cd /home/apiad/Workspace/repos/aegis
git add docs/superpowers/specs/2026-08-12-voice-input-lock-design.md docs/superpowers/plans/2026-08-12-voice-input-lock.md
git commit -m "docs: mark the voice input lock implemented"
git push origin main
```

---

## Not in this plan

From the spec's *Out of scope*, so a reader does not think they were missed:

- **A discard path.** No way to abandon a recording without transcribing it.
  Escape already carries four meanings, and a transcript lands as editable
  text, so the undo is select-all-delete.
- **Streaming or partial transcripts.** The clip decodes once on stop.
- **A web-client equivalent.** `VoiceStrip` is a Textual widget and the web
  frontend has no voice path.
- **Restarting the running aegis.** None of this reaches Alex's live sessions
  until he restarts, which is his call.
