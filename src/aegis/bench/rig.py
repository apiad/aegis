"""The terminal the benchmark pretends to be.

It owns the pty master, answers Textual's DEC 2026 support query the way a
modern terminal does, and records one line per complete frame with the
monotonic time its last byte arrived. It measures up to the bytes reaching
the terminal, not the emulator drawing them: that cost is the same for
every TUI and a pty cannot see it.
"""
from __future__ import annotations

import contextlib
import os
import select
import signal
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

import psutil
from ptyprocess import PtyProcess

from aegis.bench.frames import (
    SYNC_REPLY, Frame, FrameSplitter, ScreenGrid, find_markers, locate,
    sgr_click)
from aegis.bench.records import Recorder

_TEXT_KEEP = 200_000
_RAW_FRAMES_KEEP = 400


class Rig:
    def __init__(self, argv: list[str], *, cwd: Path, env: dict[str, str],
                 cols: int, rows: int, label: str,
                 recorder: Recorder) -> None:
        self.argv, self.cwd, self.env = argv, cwd, env
        self.cols, self.rows, self.label = cols, rows, label
        self.recorder = recorder
        self.splitter = FrameSplitter()
        self.proc: PtyProcess | None = None
        self.closed = False
        self.t_start_ns = 0
        self.frames = 0
        self.first_frame_ns: int | None = None
        self.last_frame_ns: int | None = None
        self.markers_seen: dict[str, int] = {}
        self.listeners: list[Callable[[Frame], None]] = []
        self._answered = 0
        self._text = ""
        self._raw_frames: deque[bytes] = deque(maxlen=_RAW_FRAMES_KEEP)
        self.grid = ScreenGrid(cols, rows)
        self._last_sample = 0.0

    def start(self) -> None:
        self.t_start_ns = time.monotonic_ns()
        self.proc = PtyProcess.spawn(self.argv, cwd=str(self.cwd),
                                     env=self.env,
                                     dimensions=(self.rows, self.cols))

    @property
    def pid(self) -> int:
        assert self.proc is not None
        return self.proc.pid

    @property
    def fd(self) -> int:
        assert self.proc is not None
        return self.proc.fd

    @property
    def saw_sync(self) -> bool:
        return self.splitter.saw_sync_begin

    def on_readable(self) -> None:
        try:
            chunk = os.read(self.fd, 65536)
        except OSError:
            chunk = b""
        if not chunk:
            self.closed = True
            return
        t = time.monotonic_ns()
        frames = self.splitter.feed(chunk, t)
        while self._answered < self.splitter.queries:
            os.write(self.fd, SYNC_REPLY)
            self._answered += 1
        for f in frames:
            self.frames += 1
            if self.first_frame_ns is None:
                self.first_frame_ns = f.t_ns
            self.last_frame_ns = f.t_ns
            marks = find_markers(f.text)
            for m in marks:
                self.markers_seen.setdefault(m, f.t_ns)
            self._text = (self._text + f.text)[-_TEXT_KEEP:]
            self._raw_frames.append(f.raw)
            self.grid.apply(f.raw)
            self.recorder.write({"k": "frame", "client": self.label,
                                 "t_ns": f.t_ns, "nbytes": f.nbytes,
                                 "markers": marks})
            for fn in self.listeners:
                fn(f)

    def write(self, data: bytes) -> int:
        t = time.monotonic_ns()
        os.write(self.fd, data)
        return t

    def resize(self, cols: int, rows: int) -> int:
        assert self.proc is not None
        t = time.monotonic_ns()
        self.proc.setwinsize(rows, cols)
        self.cols, self.rows = cols, rows
        # Textual redraws everything after a resize; start the model clean.
        self.grid.resize(cols, rows)
        return t

    def contains(self, s: str) -> bool:
        """Whether ``s`` appeared in any frame so far (frames are diffs, so
        this is text the client drew, not the current screen)."""
        return s in self._text

    def screen(self) -> list[str]:
        """What the client's screen shows now, one string per row."""
        return self.grid.lines()

    def click_text(self, needle: str) -> bool:
        """Click where ``needle`` was last drawn, as a user does to focus a
        widget; False when no kept frame drew it."""
        for raw in reversed(self._raw_frames):
            pos = locate(raw, needle)
            if pos is not None:
                self.write(sgr_click(*pos))
                return True
        return False

    def sample_proc(self) -> None:
        now = time.monotonic()
        if self.closed or now - self._last_sample < 1.0:
            return
        self._last_sample = now
        try:
            p = psutil.Process(self.pid)
            cpu = p.cpu_times()
            rss = p.memory_info().rss
        except psutil.Error:
            return
        self.recorder.write({"k": "proc", "client": self.label,
                             "t_ns": time.monotonic_ns(),
                             "cpu_s": cpu.user + cpu.system, "rss": rss})

    def close(self, timeout_s: float = 5.0) -> None:
        if self.proc is None:
            return
        if not self.closed:
            with contextlib.suppress(OSError):
                self.write(b"\x04")  # Ctrl+D detaches
            wait_until([self], lambda: self.closed, timeout_s)
        # ptyprocess spawns the child as a session leader, so its group
        # holds an in-process target's fake agents too. SIGINT first: a
        # client wrapped by py-spy only writes its profile on a graceful
        # exit.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.pid, signal.SIGINT)
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and self.proc.isalive():
                time.sleep(0.05)
            os.killpg(self.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            self.proc.close(force=True)
        self.closed = True


def pump(rigs: list[Rig], timeout_s: float = 0.02) -> None:
    live = [r for r in rigs if not r.closed]
    if not live:
        time.sleep(timeout_s)
        return
    ready, _, _ = select.select([r.fd for r in live], [], [], timeout_s)
    for r in live:
        if r.fd in ready:
            r.on_readable()
        r.sample_proc()


def wait_until(rigs: list[Rig], predicate: Callable[[], bool],
               timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        pump(rigs)
    return predicate()
