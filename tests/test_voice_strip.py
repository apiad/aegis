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
