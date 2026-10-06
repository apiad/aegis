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
    rb"\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"  # CSI
    rb"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    rb"|\x1bP[^\x1b]*\x1b\\"  # DCS
    rb"|\x1b[\x20-\x2f]+[\x30-\x7e]"  # nF, e.g. charset
    rb"|\x1b[\x30-\x7e]"
)  # Fp / Fe / Fs
_MARKER = re.compile(r"«b\d{4,}»")
_CUP = re.compile(rb"\x1b\[(\d+);(\d+)H")


def strip_ansi(data: bytes) -> str:
    return _ANSI.sub(b"", data).decode("utf-8", "replace")


def marker(n: int) -> str:
    return f"«b{n:04d}»"


def find_markers(text: str) -> list[str]:
    return _MARKER.findall(text)


def locate(raw: bytes, needle: str) -> tuple[int, int] | None:
    """1-based (row, col) of the last place ``needle`` was drawn in ``raw``.

    Textual writes every changed span after an absolute cursor move, so the
    text between two moves sits on one row starting at the first move's
    column. Wide characters before the needle would shift the column; the
    rig only locates plain ASCII labels, where that cannot happen.
    """
    found = None
    moves = list(_CUP.finditer(raw))
    for i, m in enumerate(moves):
        end = moves[i + 1].start() if i + 1 < len(moves) else len(raw)
        text = strip_ansi(raw[m.end() : end])
        pos = text.rfind(needle)
        if pos >= 0 and "\n" not in text[:pos]:
            found = (int(m.group(1)), int(m.group(2)) + pos)
    return found


def sgr_click(row: int, col: int) -> bytes:
    """A left-button press and release in SGR mouse encoding (mode 1006)."""
    return (f"\x1b[<0;{col};{row}M\x1b[<0;{col};{row}m").encode()


class ScreenGrid:
    """The screen as frames have left it: each span written at its cursor
    position, later spans over earlier ones.

    Approximate on purpose. Wide characters and relative cursor motion are
    ignored, which is enough to ask what text a row shows now. Frames are
    diffs, so the grid must see every frame since the last full redraw.
    """

    def __init__(self, cols: int, rows: int) -> None:
        self.resize(cols, rows)

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        self._grid = [[" "] * cols for _ in range(rows)]

    def apply(self, raw: bytes) -> None:
        moves = list(_CUP.finditer(raw))
        for i, m in enumerate(moves):
            row, col = int(m.group(1)) - 1, int(m.group(2)) - 1
            if not 0 <= row < self.rows:
                continue
            end = moves[i + 1].start() if i + 1 < len(moves) else len(raw)
            text = strip_ansi(raw[m.end() : end]).replace("\r", "")
            text = text.replace("\n", "")
            line = self._grid[row]
            for j, ch in enumerate(text):
                c = col + j
                if c >= self.cols:
                    break
                if c >= 0:
                    line[c] = ch

    def lines(self) -> list[str]:
        return ["".join(r) for r in self._grid]


def render_screen(raws, cols: int, rows: int) -> list[str]:
    """The screen a sequence of raw frames leaves behind."""
    grid = ScreenGrid(cols, rows)
    for raw in raws:
        grid.apply(raw)
    return grid.lines()


@dataclass(frozen=True)
class Frame:
    t_ns: int
    nbytes: int
    text: str
    raw: bytes = b""


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
            self._buf = self._buf[i + len(SYNC_END) :]
            frames.append(Frame(t_ns=t_ns, nbytes=i, text=strip_ansi(raw), raw=raw))
        return frames
