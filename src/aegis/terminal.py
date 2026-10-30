"""One terminal: a child on a PTY, the reader that drains it, and its store
(#362, docs/superpowers/specs/2026-10-14-terminals-design.html).

The child runs in a session of its own with the PTY as its controlling
terminal (``setsid --ctty``), so Ctrl-C, job control and SIGWINCH reach it as
they do over SSH. asyncio's subprocess cannot make a PTY the controlling
terminal, and a preexec_fn in a threaded server is unsafe, so util-linux does
(launch.pty).

The reader reads at most READ_PER_S bytes a second. Past that it stops reading
until the next tick, the kernel's PTY buffer fills, and the program blocks on
write, as it would over a slow SSH link: a flood costs aegis a bounded amount
and loses nothing. Output decodes as UTF-8 incrementally, so a character split
across two reads arrives whole, and a byte that is not UTF-8 becomes U+FFFD.
Chunks go to subscribers once per tick, TICK_S, never through the sessions
channel's 250 ms throttle: typing has to echo within a frame.

Input the program has not read yet waits in memory, at most INPUT_MAX bytes;
past that, input fails ``busy`` rather than growing without bound.

Nothing here decides who may type; the registry and the operations do.
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import fcntl
import logging
import os
import re
import signal
import struct
import termios
from collections.abc import Callable
from pathlib import Path

from . import launch
from .ops import OpError
from .term_store import CastStore

READ_PER_S = 4 * 1024 * 1024
TICK_S = 0.016
READ_MAX = 65536
DRAIN_READS = 16  # what is left after the child exits: a PTY buffer or two
KILL_WAIT_S = 2.0  # after SIGKILL, before stop gives up and logs
INPUT_MAX = 1024 * 1024  # input the program has not read yet
ANSI = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]"
)

Ops = list[dict]

log = logging.getLogger("aegis.terminal")


class Terminal:
    def __init__(
        self,
        meta: dict,
        store: CastStore,
        on_output: Callable[[Terminal, Ops], None],
        on_exit: Callable[[Terminal], None],
    ) -> None:
        self.meta = meta
        self.store = store
        self._on_output = on_output
        self._on_exit = on_exit
        self.proc: asyncio.subprocess.Process | None = None
        self.user: str | None = None
        self.fd: int | None = None
        self.bytes_read = 0
        self.exited = asyncio.Event()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._pending: Ops = []
        self._budget = 0
        self._paused = False
        self._eof = False
        self._tick: asyncio.TimerHandle | None = None
        self._last_flush = 0.0
        self._out = bytearray()
        self._writing = False
        self._waiter: asyncio.Task | None = None
        self._starting = asyncio.Lock()

    @property
    def id(self) -> str:
        return self.meta["id"]

    @property
    def running(self) -> bool:
        return self.fd is not None

    @property
    def starting(self) -> bool:
        return self._starting.locked()

    async def started(self) -> None:
        """Return once a start under way has finished, or failed."""
        async with self._starting:
            pass

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None and self.running else None

    async def start(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        cgroup: Path | None,
        user: str | None = None,
        via: str | None = None,
    ) -> None:
        if self.running or self.starting:
            raise OpError("running", f"terminal {self.meta['name']} is running")
        async with self._starting:
            await self._start(argv, cwd, env, cgroup, user, via)

    async def _start(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        cgroup: Path | None,
        user: str | None,
        via: str | None,
    ) -> None:
        cols, rows = self.meta["cols"], self.meta["rows"]
        master, slave = os.openpty()
        _winsize(master, cols, rows)
        os.set_blocking(master, False)
        try:
            self.proc = await launch.pty(
                argv, tty=slave, cwd=cwd, env=env, cgroup=cgroup, user=user, via=via
            )
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self.user = user
        self.fd = master
        self.exited = asyncio.Event()
        self._decoder.reset()
        self._budget = int(READ_PER_S * TICK_S)
        self._paused = self._eof = False
        self.store.open(cols, rows, {"id": self.id, "profile": self.meta["profile"]})
        self.store.marker("start")
        asyncio.get_running_loop().add_reader(master, self._readable, master)
        self._waiter = asyncio.create_task(self._wait())

    def input(self, data: str) -> None:
        if self.fd is None:
            raise OpError("not_running", f"terminal {self.meta['name']} is not running")
        raw = data.encode("utf-8", "replace")
        if len(self._out) + len(raw) > INPUT_MAX:
            raise OpError("busy", f"terminal {self.meta['name']} is not reading input")
        self._out += raw
        self._write()

    def resize(self, cols: int, rows: int) -> None:
        if (cols, rows) == (self.meta["cols"], self.meta["rows"]):
            return
        self.meta["cols"], self.meta["rows"] = cols, rows
        if self.fd is not None:
            _winsize(self.fd, cols, rows)
            self._pending.append(
                {"rev": self.store.resize(cols, rows), "r": [cols, rows]}
            )
            self._schedule()

    async def stop(self, grace: float) -> None:
        """SIGHUP to the terminal's process group and its foreground job's,
        then SIGKILL after ``grace`` seconds to whichever is left, as closing
        an SSH session does. A shell's job control puts a foreground job in a
        group of its own, and a child that has not run setsid yet has no group,
        so the child itself is signalled too."""
        if self.proc is None or self.fd is None:
            return
        groups = {self.proc.pid}
        with contextlib.suppress(OSError):
            groups.add(os.tcgetpgrp(self.fd))  # 0 while the PTY has no session
        groups -= {0, os.getpgrp()}
        await self._signal(groups, signal.SIGHUP)
        loop = asyncio.get_running_loop()
        end = loop.time() + grace
        while not self.exited.is_set() or any(map(_alive, groups)):
            if loop.time() >= end:
                await self._signal(groups, signal.SIGKILL)
                break
            await asyncio.sleep(0.05)
        # SIGKILL lands, and the dead are reaped, a moment after it is sent.
        end = loop.time() + KILL_WAIT_S
        while not self.exited.is_set() or any(map(_alive, groups)):
            if loop.time() >= end:
                log.warning("aegis: terminal %s outlived SIGKILL", self.meta["name"])
                return
            await asyncio.sleep(0.05)

    async def _signal(self, groups: set[int], sig: int) -> None:
        for pgid in groups:
            try:
                await launch.signal_process_async(pgid, sig, user=self.user, group=True)
            except OSError as e:
                log.warning(
                    "aegis: could not signal terminal group %s as %s: %s",
                    pgid,
                    self.user,
                    e,
                )
        if self.proc is not None:
            try:
                await launch.signal_process_async(self.proc.pid, sig, user=self.user)
            except OSError as e:
                log.warning(
                    "aegis: could not signal terminal process %s as %s: %s",
                    self.proc.pid,
                    self.user,
                    e,
                )

    def last_line(self) -> str:
        """The newest line of output that is not blank, as plain text, with a
        carriage return's overwrite applied."""
        for n, (_rev, code, data) in enumerate(reversed(self.store.ring)):
            if n > 20:
                break
            if code != "o":
                continue
            for line in reversed(ANSI.sub("", data).split("\n")):
                if text := line.rstrip("\r").rsplit("\r", 1)[-1].strip():
                    return text[:200]
        return ""

    def _readable(self, fd: int) -> None:
        if fd != self.fd:  # a reader left from an earlier child: never read another's
            asyncio.get_running_loop().remove_reader(fd)
            return
        try:
            data = os.read(fd, min(READ_MAX, self._budget))
        except BlockingIOError:
            return
        except OSError:  # EIO: every descriptor on the slave side is closed
            self._stop_reading()
            return
        if not data:
            self._stop_reading()
            return
        self._take(data)
        self._budget -= len(data)
        if self._budget <= 0:
            asyncio.get_running_loop().remove_reader(fd)
            self._paused = True
        self._schedule()

    def _take(self, data: bytes) -> None:
        self.bytes_read += len(data)
        if text := self._decoder.decode(data):
            self._pending.append({"rev": self.store.output(text), "o": text})

    def _schedule(self) -> None:
        if self._tick is None:
            loop = asyncio.get_running_loop()
            wait = max(0.0, self._last_flush + TICK_S - loop.time())
            self._tick = loop.call_later(wait, self._flush)

    def _flush(self) -> None:
        loop = asyncio.get_running_loop()
        self._tick = None
        self._last_flush = loop.time()
        self._budget = int(READ_PER_S * TICK_S)
        if self._paused and self.fd is not None and not self._eof:
            self._paused = False
            loop.add_reader(self.fd, self._readable, self.fd)
        self.store.flush()
        if self._pending:
            ops, self._pending = self._pending, []
            self._on_output(self, ops)

    def _stop_reading(self) -> None:
        if not self._paused and self.fd is not None:
            asyncio.get_running_loop().remove_reader(self.fd)
        self._paused = False
        self._eof = True

    def _write(self) -> None:
        loop = asyncio.get_running_loop()
        fd = self.fd
        while self._out and fd is not None:
            try:
                n = os.write(fd, self._out)
            except BlockingIOError:
                break
            except OSError:  # the child is gone; _wait ends the terminal
                self._out.clear()
                break
            del self._out[:n]
        if fd is None:
            self._writing = False
            return
        want = bool(self._out)
        if want and not self._writing:
            loop.add_writer(fd, self._write)
        elif not want and self._writing:
            loop.remove_writer(fd)
        self._writing = want

    async def _wait(self) -> None:
        proc, fd = self.proc, self.fd
        assert proc is not None and fd is not None, "start() set both"
        code = await proc.wait()
        for _ in range(DRAIN_READS):
            try:
                data = os.read(fd, READ_MAX)
            except OSError:  # nothing left, or EIO
                break
            if not data:
                break
            self._take(data)
        loop = asyncio.get_running_loop()
        if not self._eof and not self._paused:
            loop.remove_reader(fd)
        if self._writing:
            loop.remove_writer(fd)
            self._writing = False
        self._out.clear()
        os.close(fd)
        self.fd = None
        if self._tick is not None:
            self._tick.cancel()
            self._tick = None
        if rest := self._decoder.decode(b"", final=True):
            self._pending.append({"rev": self.store.output(rest), "o": rest})
        self.store.marker(f"exit {code}")
        self._flush()
        self.meta["code"] = code
        self._on_exit(self)
        self.exited.set()


def _winsize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True
