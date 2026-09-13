"""Terminal output as the rig sees it: DEC 2026 frames and markers.

Textual wraps each screen update in synchronized output once the terminal
confirms support, so ``\\e[?2026l`` ends one complete frame. The rig
answers the support query itself; a target that never negotiates produces
no frames, which the run reports as a failed gate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SYNC_BEGIN = b"\x1b[?2026h"
SYNC_END = b"\x1b[?2026l"
SYNC_QUERY = b"\x1b[?2026$p"
SYNC_REPLY = b"\x1b[?2026;2$y"

_ANSI = re.compile(
    rb"\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"   # CSI
    rb"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"          # OSC
    rb"|\x1bP[^\x1b]*\x1b\\"                        # DCS
    rb"|\x1b[\x20-\x2f]+[\x30-\x7e]"                # nF, e.g. charset
    rb"|\x1b[\x30-\x7e]")                           # Fp / Fe / Fs
_MARKER = re.compile(r"«b\d{4,}»")


def strip_ansi(data: bytes) -> str:
    return _ANSI.sub(b"", data).decode("utf-8", "replace")


def marker(n: int) -> str:
    return f"«b{n:04d}»"


def find_markers(text: str) -> list[str]:
    return _MARKER.findall(text)


@dataclass(frozen=True)
class Frame:
    t_ns: int
    nbytes: int
    text: str


class FrameSplitter:
    """Feed raw pty chunks; get back each frame the moment it completes.

    ``t_ns`` of a frame is the arrival time of the chunk that carried its
    closing ``SYNC_END`` — the moment the terminal could have shown it.
    """

    def __init__(self) -> None:
        self._buf = b""
        self.queries = 0
        self.saw_sync_begin = False

    def feed(self, chunk: bytes, t_ns: int) -> list[Frame]:
        self._buf += chunk
        n = self._buf.count(SYNC_QUERY)
        if n:
            self.queries += n
            self._buf = self._buf.replace(SYNC_QUERY, b"")
        if SYNC_BEGIN in self._buf:
            self.saw_sync_begin = True
        frames: list[Frame] = []
        while (i := self._buf.find(SYNC_END)) >= 0:
            raw = self._buf[:i].replace(SYNC_BEGIN, b"")
            self._buf = self._buf[i + len(SYNC_END):]
            frames.append(Frame(t_ns=t_ns, nbytes=i, text=strip_ansi(raw)))
        return frames
