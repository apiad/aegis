"""Leaving application mode must undo what entering it did.

`ViewDriver.start_application_mode` writes the escapes that put a real
terminal into alt screen, turn on mouse reporting and movement reporting,
hide the cursor and enable bracketed paste. Its `stop_application_mode`
came from `WebDriver`, which writes none of the inverses: a browser client
does not need them, because the page tears down the emulator.

A terminal is not a page. Detaching left the user's shell inside the alt
screen with the cursor hidden and mouse reporting still on, so every mouse
move printed escape sequences into their prompt. The client cannot fix
this either: it restores termios, which is line discipline, not modes, and
by design it parses no frames and acts on no meta.

Asserted as bytes on the sink, because that is what reaches the terminal.
"""
from __future__ import annotations

from aegis.views.driver import view_driver_for
from aegis.views.sink import FrameSink


class _App:
    """Enough App for the driver's constructor and its two mode calls."""

    def __init__(self) -> None:
        self.is_headless = False
        self.is_inline = False
        self._driver = None

    def call_later(self, *_a, **_kw) -> None:
        return None

    def post_message(self, *_a, **_kw) -> None:
        return None


def _driver_and_frames():
    sink = FrameSink()
    written: list[bytes] = []
    sink.attach(written.append)
    driver = view_driver_for(sink)(_App(), size=(80, 24))
    return driver, written


async def test_stop_application_mode_undoes_start():
    driver, written = _driver_and_frames()
    driver.start_application_mode()
    entered = b"".join(written)
    written.clear()

    driver.stop_application_mode()
    left = b"".join(written)

    # Each pair is one mode the start path turned on.
    for what, on, off in [
        ("alt screen", b"?1049h", b"?1049l"),
        ("mouse movement reporting", b"?1003h", b"?1003l"),
        ("cursor", b"?25l", b"?25h"),
        ("bracketed paste", b"?2004h", b"?2004l"),
    ]:
        assert on in entered, f"start did not enable {what}"
        assert off in left, (
            f"stop left {what} on; a real terminal keeps it after detach")

    # The mouse is enabled through Textual's helper, which writes several
    # modes; the matching disable must undo all of them.
    for seq in (b"?1000l", b"?1006l", b"?1015l"):
        assert seq in left, f"mouse mode {seq!r} was never disabled"
