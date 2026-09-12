"""Where one view's frames go.

Stage 4 bound ``list.append`` directly, which is right for a test and wrong
for a daemon: a view attached for a day emits frames continuously and
nothing drained that list. So: bytes go straight to a live consumer and are
not kept, and are buffered (capped) only while nobody is listening.

There is deliberately no replay buffer. A reattaching client is given a
full frame by ``View.repaint()``, which is correct against a screen the
client has never seen; replaying deltas against one is not.
"""
from __future__ import annotations

from typing import Callable


class FrameSink:
    def __init__(self, cap: int = 4096) -> None:
        self.frames: list[bytes] = []
        self._cap = cap
        self._consumers: list[Callable[[bytes], None]] = []

    @property
    def consumers(self) -> int:
        return len(self._consumers)

    def attach(self, fn: Callable[[bytes], None]) -> None:
        self._consumers.append(fn)

    def detach(self, fn: Callable[[bytes], None]) -> None:
        # A client that dies mid-handshake runs its finally block without
        # ever having attached. Missing is the normal case, not an error.
        try:
            self._consumers.remove(fn)
        except ValueError:
            pass

    def __call__(self, data: bytes) -> None:
        if self._consumers:
            for fn in list(self._consumers):
                try:
                    fn(data)
                except Exception:  # noqa: BLE001
                    # One dead socket must not blind the view's other
                    # consumers, nor propagate into Textual's render path,
                    # which is what calls this.
                    from traceback import format_exc

                    from textual import log
                    log(format_exc())
            return
        self.frames.append(data)
        if len(self.frames) > self._cap:
            del self.frames[:len(self.frames) - self._cap]
