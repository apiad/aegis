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
