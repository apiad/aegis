"""A terminal's output, as an asciicast v2 recording in two segments (#362).

``cast.jsonl`` is the segment being written and ``cast.1.jsonl`` the one
before it. Each segment opens with a header line; aegis's own fields sit under
``aegis`` in it, where players ignore them, and ``first`` there is the
sequence number of the segment's first event. Each event after it is one line,
``[t, code, data]``: ``"o"`` for output, ``"r"`` for a resize as
``"COLSxROWS"``, ``"m"`` for aegis's markers (start, exit), which players
skip. So the store is already a recording ``asciinema play`` reads.

An event's sequence number, its rev, is its line number across segments,
header lines not counted. It is recovered from the files, never from the meta,
so a crash between a write and a meta flush loses no numbering. At
SEGMENT_BYTES the segment rolls and the older one is deleted, so a terminal
never holds more than twice that on disk. The newest RING_BYTES of events also
live in memory, so a browser that resubscribes reads no file.

A person's input is never stored: the program's echo already is, and with
echo off nothing would be.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from pathlib import Path

SEGMENT_BYTES = 8 * 1024 * 1024
RING_BYTES = 1024 * 1024
SNAPSHOT_BYTES = 256 * 1024
# A full-screen clear, \x1b[2J or \x1bc, and the alternate screen's enter (h)
# and leave (l). Nothing printed before a clear on the main screen is still on
# it, nor before entering the alternate screen while a program is on it. A
# program that left it (vim, quit) gave the main screen back as it was.
CLEAR = re.compile(r"\x1b\[2J|\x1bc|\x1b\[\?1049([hl])")

Event = tuple[int, str, str]  # rev, code, data


class CastStore:
    def __init__(
        self,
        dir: Path,
        segment_bytes: int = SEGMENT_BYTES,
        ring_bytes: int = RING_BYTES,
    ) -> None:
        self.dir = dir
        self.path = dir / "cast.jsonl"
        self.older = dir / "cast.1.jsonl"
        self._segment_bytes = segment_bytes
        self._ring_bytes = ring_bytes
        self._f = None
        self._size = 0
        self._t0 = 0
        self._aegis: dict = {}
        self.cols, self.rows = 120, 40
        self._loaded = False
        self.rev = 0
        self.ring: deque[Event] = deque()
        self._ring_size = 0

    def load(self) -> None:
        """Recover the rev and the ring from the files, once. Reads the whole
        current segment to count its lines, at most SEGMENT_BYTES."""
        if self._loaded:
            return
        self._loaded = True
        events: list[Event] = []
        for path in (self.older, self.path):
            if path.exists():
                events += _tail_events(path, 2 * self._ring_bytes)
        if events:
            self.rev = events[-1][0]
        for e in events:
            self._remember(e)

    def open(self, cols: int, rows: int, aegis: dict) -> None:
        """Start appending; a new or empty segment gets its header first."""
        self.load()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cols, self.rows, self._aegis = cols, rows, aegis
        if self.path.exists() and self.path.stat().st_size:
            _cut_partial_line(self.path)
            with self.path.open("rb") as f:
                self._t0 = json.loads(f.readline()).get("timestamp", int(time.time()))
            self._f = self.path.open("a", encoding="utf-8")
            self._size = self.path.stat().st_size
        else:
            self._start_segment()

    def output(self, text: str) -> int:
        return self._event("o", text)

    def resize(self, cols: int, rows: int) -> int:
        self.cols, self.rows = cols, rows
        return self._event("r", f"{cols}x{rows}")

    def marker(self, label: str) -> int:
        return self._event("m", label)

    def since(self, rev: int) -> list[Event] | None:
        """The events after ``rev``, or None when the ring no longer holds all
        of them and the caller needs a snapshot."""
        self.load()
        if rev > self.rev:
            return None
        if rev == self.rev:
            return []
        if not self.ring or self.ring[0][0] > rev + 1:
            return None
        return [e for e in self.ring if e[0] > rev]

    def tail(self, limit: int = SNAPSHOT_BYTES) -> str:
        """What a browser with nothing replays: the output since the last
        full-screen clear in the ring, at most ``limit`` characters, cut at a
        line start or an escape sequence's start when the limit cuts it."""
        self.load()
        parts: list[str] = []
        size = 0
        for _rev, code, data in reversed(self.ring):
            if code != "o":
                continue
            parts.append(data)
            size += len(data)
            if size >= limit:
                break
        text = "".join(reversed(parts))
        cut = len(text) > limit
        if cut:
            text = text[-limit:]
        start, alt = None, None  # the last clear on the main screen; the enter
        for m in CLEAR.finditer(text):
            if m[1] == "h":
                alt = m.start()
            elif m[1] == "l":
                alt = None
            elif alt is None:
                start = m.start()
        if alt is not None or start is not None:
            return text[alt if alt is not None else start :]
        if cut:
            # Never start inside an escape sequence: at the next one or the next
            # line, whichever comes first.
            esc, nl = text.find("\x1b"), text.find("\n")
            if starts := [i for i in (esc, nl + 1 if nl >= 0 else -1) if i >= 0]:
                return text[min(starts) :]
        return text

    def flush(self) -> None:
        if self._f is not None:
            self._f.flush()

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None

    def _event(self, code: str, data: str) -> int:
        self.rev += 1
        line = json.dumps(
            [round(time.time() - self._t0, 6), code, data], ensure_ascii=False
        )
        f = self._f
        assert f is not None, "open() comes before the first event"
        f.write(line + "\n")
        self._size += len(line.encode()) + 1
        self._remember((self.rev, code, data))
        if self._size >= self._segment_bytes:
            f.close()
            os.replace(self.path, self.older)
            self._start_segment()
        return self.rev

    def _start_segment(self) -> None:
        self._t0 = int(time.time())
        head = {
            "version": 2,
            "width": self.cols,
            "height": self.rows,
            "timestamp": self._t0,
            "env": {"TERM": "xterm-256color"},
            "aegis": self._aegis | {"first": self.rev + 1},
        }
        line = json.dumps(head) + "\n"
        self._f = self.path.open("w", encoding="utf-8")
        self._f.write(line)
        self._size = len(line.encode())

    def _remember(self, e: Event) -> None:
        self.ring.append(e)
        self._ring_size += len(e[2])
        while self._ring_size > self._ring_bytes and len(self.ring) > 1:
            self._ring_size -= len(self.ring.popleft()[2])


def _tail_events(path: Path, limit: int) -> list[Event]:
    """A segment's newest events, at most ``limit`` bytes of them, numbered
    from its header's ``first``. A line a crash cut is not an event."""
    with path.open("rb") as f:
        head = json.loads(f.readline() or b"{}")
        body = f.read()
    lines = body.split(b"\n")[:-1]  # the last piece is empty, or a cut line
    rev = head.get("aegis", {}).get("first", 1) + len(lines) - 1
    out: list[Event] = []
    size = 0
    for raw in reversed(lines):
        size += len(raw)
        if size > limit:
            break
        try:
            _t, code, data = json.loads(raw)
        except ValueError:
            pass
        else:
            out.append((rev, code, data))
        rev -= 1
    out.reverse()
    return out


def _cut_partial_line(path: Path) -> None:
    with path.open("rb+") as f:
        data = f.read()
        if data and not data.endswith(b"\n"):
            f.truncate(data.rfind(b"\n") + 1)
