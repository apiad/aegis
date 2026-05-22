# Live Terminals v1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each slice ends with `uv run pytest -q` clean and a conventional commit on `main`. Do **not** create feature branches — per repo memory, aegis commits straight to main.

**Goal:** Add a fifth coordination primitive to aegis: live shared PTY terminals that both Alex and any agent can spawn, run commands on, send raw keystrokes to, read history from, and subscribe to command-finish events on.

**Architecture:** Three new modules under `src/aegis/terminal/` (`parser.py`, `pty.py`, `manager.py`) plus `notify.py`. OSC 133 escape sequence parser detects command boundaries and exit codes deterministically. `TerminalManager` owns per-terminal PTY processes and ledgers, exposes async methods that mirror the canvas manager's shape. Notifier wakes subscribers through the existing `InboxRouter`. Eight new MCP tools register through `AppBridge`. A new `TerminalTab` widget renders a command-session log in the TUI. `SessionManager` persists terminal metadata for `aegis --resume`.

**Tech Stack:** Python 3.13+, asyncio, `ptyprocess` (add to deps if not transitive), Textual 8.x, FastMCP, pytest. State JSONL at `.aegis/state/terminals/<name>/`.

**Spec:** `docs/superpowers/specs/2026-05-21-live-terminals-design.md` — read it before starting.

**Precondition checks (run first, hard-stop if any fail):**

```bash
test -f docs/superpowers/specs/2026-05-21-live-terminals-design.md || { echo "Spec missing; aborting." >&2; exit 1; }
test -d src/aegis/canvas || { echo "Canvas module missing — wrong checkout." >&2; exit 1; }
uv run pytest -q 2>&1 | tail -5
```

The last line should show all-green; if not, stop and ping Alex before doing anything else.

---

## Slice 1 — OSC 133 parser

**Files:**
- Create: `src/aegis/terminal/__init__.py`
- Create: `src/aegis/terminal/parser.py`
- Create: `tests/test_terminal_parser.py`

The parser is pure (no I/O, no asyncio). It consumes byte chunks from a PTY stream and yields `(stripped_chunk, events)` where events name prompt/command boundaries.

- [ ] **Step 1: Write failing tests.**

```python
# tests/test_terminal_parser.py
import pytest
from aegis.terminal.parser import (
    OSC133Parser, PromptStart, CommandStart, CommandEnd,
)


def test_strips_prompt_start_marker():
    parser = OSC133Parser()
    stripped, events = parser.feed(b"\x1b]133;A\x07$ ")
    assert stripped == b"$ "
    assert events == [PromptStart()]


def test_strips_command_start_marker():
    parser = OSC133Parser()
    stripped, events = parser.feed(b"\x1b]133;B\x07")
    assert stripped == b""
    assert events == [CommandStart()]


def test_command_end_with_exit_code():
    parser = OSC133Parser()
    stripped, events = parser.feed(b"hello\n\x1b]133;D;0\x07")
    assert stripped == b"hello\n"
    assert events == [CommandEnd(exit_code=0)]


def test_command_end_nonzero_exit():
    parser = OSC133Parser()
    stripped, events = parser.feed(b"\x1b]133;D;130\x07")
    assert events == [CommandEnd(exit_code=130)]


def test_command_end_missing_exit():
    parser = OSC133Parser()
    stripped, events = parser.feed(b"\x1b]133;D;\x07")
    assert events == [CommandEnd(exit_code=None)]


def test_sequence_split_across_chunks():
    parser = OSC133Parser()
    stripped1, events1 = parser.feed(b"hello\x1b]133;")
    stripped2, events2 = parser.feed(b"A\x07world")
    assert stripped1 == b"hello"
    assert events1 == []
    assert stripped2 == b"world"
    assert events2 == [PromptStart()]


def test_multibyte_utf8_split_mid_chunk():
    # "é" is 0xC3 0xA9; split between chunks. Parser must not corrupt it.
    parser = OSC133Parser()
    s1, _ = parser.feed(b"caf\xc3")
    s2, _ = parser.feed(b"\xa9\n")
    assert (s1 + s2).decode("utf-8") == "café\n"


def test_multiple_events_one_chunk():
    parser = OSC133Parser()
    stripped, events = parser.feed(
        b"\x1b]133;A\x07$ pytest\n\x1b]133;B\x07ok\n\x1b]133;D;0\x07"
    )
    assert stripped == b"$ pytest\nok\n"
    assert events == [PromptStart(), CommandStart(), CommandEnd(exit_code=0)]


def test_marker_at_buffer_boundary_preserved():
    # If chunk ends mid-marker, parser holds bytes back until completion.
    parser = OSC133Parser()
    s1, e1 = parser.feed(b"\x1b]13")
    s2, e2 = parser.feed(b"3;A\x07")
    assert s1 == b""
    assert e1 == []
    assert s2 == b""
    assert e2 == [PromptStart()]


def test_bytes_resembling_marker_in_output_passed_through():
    # The text "\x1b]133" appearing in literal output is rare but not
    # impossible; parser cannot distinguish — accept passthrough for
    # incomplete sequences that never resolve.
    parser = OSC133Parser()
    s, e = parser.feed(b"hello world\n")
    assert s == b"hello world\n"
    assert e == []
```

- [ ] **Step 2: Run tests — expect ImportError / fail.**

```bash
uv run pytest tests/test_terminal_parser.py -q
```

- [ ] **Step 3: Implement `src/aegis/terminal/parser.py`.**

```python
"""OSC 133 escape-sequence parser for live terminals.

Pure byte-level. No I/O, no asyncio. Yields stripped output chunks and
prompt/command-boundary events.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class PromptStart:
    pass


@dataclass(frozen=True)
class CommandStart:
    pass


@dataclass(frozen=True)
class CommandEnd:
    exit_code: int | None


Event = Union[PromptStart, CommandStart, CommandEnd]

# OSC 133 sequences are framed by ESC ] 133 ; <body> BEL
_ESC = 0x1B
_BEL = 0x07
_OSC133_PREFIX = b"\x1b]133;"


class OSC133Parser:
    """Stateful parser. Holds back trailing bytes that might be the start
    of an incomplete OSC sequence, so split-across-chunks is safe."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> tuple[bytes, list[Event]]:
        self._buf.extend(chunk)
        out = bytearray()
        events: list[Event] = []
        i = 0
        while i < len(self._buf):
            b = self._buf[i]
            if b != _ESC:
                out.append(b)
                i += 1
                continue
            # Possible OSC 133 sequence. Need at least len(_OSC133_PREFIX)
            # bytes to confirm; otherwise hold back.
            if i + len(_OSC133_PREFIX) > len(self._buf):
                break
            if bytes(self._buf[i:i + len(_OSC133_PREFIX)]) != _OSC133_PREFIX:
                # ESC followed by something else (e.g. another OSC, CSI).
                # Pass ESC through and continue.
                out.append(b)
                i += 1
                continue
            # Search for the terminating BEL within the buffer.
            end = self._buf.find(_BEL, i + len(_OSC133_PREFIX))
            if end < 0:
                # Incomplete; hold from i.
                break
            body = bytes(self._buf[i + len(_OSC133_PREFIX):end])
            events.append(_parse_body(body))
            i = end + 1
        # Anything we passed through goes to out; anything from i onward
        # we hold for the next feed.
        remainder = bytes(self._buf[i:])
        self._buf.clear()
        self._buf.extend(remainder)
        return bytes(out), events


def _parse_body(body: bytes) -> Event:
    if body == b"A":
        return PromptStart()
    if body == b"B":
        return CommandStart()
    if body.startswith(b"D"):
        rest = body[1:]
        if rest.startswith(b";"):
            payload = rest[1:]
            if not payload:
                return CommandEnd(exit_code=None)
            try:
                return CommandEnd(exit_code=int(payload))
            except ValueError:
                return CommandEnd(exit_code=None)
        return CommandEnd(exit_code=None)
    # Unknown 133;<X> sequence — drop silently.
    return CommandEnd(exit_code=None) if body[:1] == b"D" else PromptStart() if body == b"A" else CommandStart() if body == b"B" else _unknown_event()


def _unknown_event() -> Event:
    # Defensive — should be unreachable given the dispatch above.
    return PromptStart()
```

- [ ] **Step 4: Run tests — all pass.**

```bash
uv run pytest tests/test_terminal_parser.py -q
```

- [ ] **Step 5: Commit.**

```bash
git add src/aegis/terminal/__init__.py src/aegis/terminal/parser.py tests/test_terminal_parser.py
git commit -m "feat(terminal): OSC 133 byte-level parser for command boundaries"
```

---

## Slice 2 — PTY wrapper + manager skeleton (spawn / list / close)

**Files:**
- Create: `src/aegis/terminal/pty.py`
- Create: `src/aegis/terminal/manager.py`
- Create: `tests/test_terminal_manager_lifecycle.py`
- Modify: `pyproject.toml` (add `ptyprocess` to dependencies)

- [ ] **Step 1: Add `ptyprocess` to `pyproject.toml`.**

Open `pyproject.toml`, find the `dependencies` array under `[project]`, append `"ptyprocess>=0.7.0"`. Run `uv sync`.

- [ ] **Step 2: Write failing tests.**

```python
# tests/test_terminal_manager_lifecycle.py
import asyncio
import pytest
from pathlib import Path
from aegis.terminal.manager import TerminalManager, TerminalAlreadyExists


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "aegis" / "state" / "terminals"


@pytest.mark.asyncio
async def test_spawn_creates_state_dir_and_meta(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    info = await mgr.spawn(name="build", shell="/bin/bash", cwd=str(state_dir.parent))
    assert info.name == "build"
    assert info.shell == "/bin/bash"
    assert info.pid > 0
    assert (state_dir / "build" / "meta.json").exists()
    await mgr.close("build")


@pytest.mark.asyncio
async def test_spawn_duplicate_name_errors(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="dup", shell="/bin/bash")
    with pytest.raises(TerminalAlreadyExists):
        await mgr.spawn(name="dup", shell="/bin/bash")
    await mgr.close("dup")


@pytest.mark.asyncio
async def test_list_returns_spawned_terminals(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="a", shell="/bin/bash")
    await mgr.spawn(name="b", shell="/bin/bash")
    names = {t.name for t in mgr.list()}
    assert names == {"a", "b"}
    await mgr.close("a")
    await mgr.close("b")


@pytest.mark.asyncio
async def test_close_removes_from_list(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="gone", shell="/bin/bash")
    await mgr.close("gone")
    assert all(t.name != "gone" for t in mgr.list())


@pytest.mark.asyncio
async def test_close_preserves_ledger_by_default(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="kept", shell="/bin/bash")
    await mgr.close("kept")
    assert (state_dir / "kept" / "meta.json").exists()
```

- [ ] **Step 3: Implement `src/aegis/terminal/pty.py`.**

```python
"""Thin async wrapper around ptyprocess for live terminals."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from ptyprocess import PtyProcessUnicode


class AsyncPty:
    """Async-friendly wrapper. Reads from the PTY in a background thread
    via run_in_executor; writes are non-blocking via os.write on the fd."""

    def __init__(self, proc: PtyProcessUnicode) -> None:
        self._proc = proc

    @classmethod
    def spawn(
        cls,
        argv: list[str],
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        dimensions: tuple[int, int] = (24, 80),
    ) -> "AsyncPty":
        proc = PtyProcessUnicode.spawn(
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            dimensions=dimensions,
        )
        return cls(proc)

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def is_alive(self) -> bool:
        return self._proc.isalive()

    async def read(self, n: int = 4096) -> bytes:
        loop = asyncio.get_running_loop()
        try:
            chunk = await loop.run_in_executor(None, self._proc.read, n)
            return chunk.encode("utf-8", errors="replace") if isinstance(chunk, str) else chunk
        except EOFError:
            return b""

    def write(self, data: bytes) -> None:
        self._proc.write(data.decode("utf-8", errors="replace"))

    def close(self, force: bool = False) -> None:
        try:
            if force:
                self._proc.kill(9)
            else:
                self._proc.terminate(force=False)
        except Exception:
            pass
```

- [ ] **Step 4: Implement `src/aegis/terminal/manager.py` (skeleton — spawn/list/close only).**

```python
"""TerminalManager — owns live PTY terminals and their ledgers."""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from aegis.terminal.pty import AsyncPty
from aegis.terminal.parser import OSC133Parser


class TerminalAlreadyExists(Exception):
    pass


class TerminalNotFound(Exception):
    pass


@dataclass
class TerminalInfo:
    name: str
    pid: int
    shell: str
    cwd: str
    started_at: str
    last_cmd_at: str | None = None
    last_exit: int | None = None


@dataclass
class _TerminalState:
    info: TerminalInfo
    pty: AsyncPty
    state_dir: Path
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    parser: OSC133Parser = field(default_factory=OSC133Parser)
    subscribers: set[str] = field(default_factory=set)
    reader_task: asyncio.Task | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class TerminalManager:
    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._terminals: dict[str, _TerminalState] = {}
        self._spawn_lock = asyncio.Lock()

    async def spawn(
        self,
        *,
        name: str,
        shell: str | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> TerminalInfo:
        async with self._spawn_lock:
            if name in self._terminals:
                raise TerminalAlreadyExists(name)
            shell = shell or os.environ.get("SHELL") or "/bin/bash"
            cwd = cwd or os.getcwd()
            term_dir = self.state_dir / name
            term_dir.mkdir(parents=True, exist_ok=True)
            argv = _build_argv(shell, term_dir)
            term_env = _build_env(shell, term_dir, env or dict(os.environ))
            pty = AsyncPty.spawn(argv, cwd=cwd, env=term_env)
            info = TerminalInfo(
                name=name,
                pid=pty.pid,
                shell=shell,
                cwd=cwd,
                started_at=_now_iso(),
            )
            state = _TerminalState(info=info, pty=pty, state_dir=term_dir)
            self._terminals[name] = state
            _write_meta(term_dir, info, shell)
            return info

    def list(self) -> list[TerminalInfo]:
        return [s.info for s in self._terminals.values()]

    def get(self, name: str) -> TerminalInfo:
        state = self._terminals.get(name)
        if state is None:
            raise TerminalNotFound(name)
        return state.info

    async def close(self, name: str, *, purge: bool = False) -> None:
        state = self._terminals.pop(name, None)
        if state is None:
            raise TerminalNotFound(name)
        if state.reader_task is not None:
            state.reader_task.cancel()
        state.pty.close()
        await asyncio.sleep(0)  # let cancellation propagate
        state.pty.close(force=True)
        if purge:
            import shutil
            shutil.rmtree(state.state_dir, ignore_errors=True)


def _build_argv(shell: str, term_dir: Path) -> list[str]:
    name = Path(shell).name
    if name in {"bash", "sh"}:
        init = term_dir / "init.sh"
        _write_bash_init(init)
        return [shell, "--rcfile", str(init), "-i"]
    if name == "zsh":
        zdotdir = term_dir / ".zdotdir"
        zdotdir.mkdir(exist_ok=True)
        _write_zsh_init(zdotdir / ".zshrc")
        return [shell, "-i"]
    # Fallback: no shell-integration init; rely on marker-injection fallback.
    return [shell, "-i"]


def _build_env(shell: str, term_dir: Path, base: dict[str, str]) -> dict[str, str]:
    env = dict(base)
    if Path(shell).name == "zsh":
        env["ZDOTDIR"] = str(term_dir / ".zdotdir")
    env["AEGIS_TERM"] = "1"
    return env


def _write_bash_init(path: Path) -> None:
    path.write_text(
        '# Sourced by aegis-spawned bash for OSC 133 shell integration.\n'
        '[ -f /etc/bashrc ] && . /etc/bashrc\n'
        '[ -f ~/.bashrc ] && . ~/.bashrc\n'
        '__aegis_prompt_start() { printf "\\033]133;A\\007"; }\n'
        '__aegis_command_start() { printf "\\033]133;B\\007"; }\n'
        '__aegis_command_end() { local ec=$?; printf "\\033]133;D;%d\\007" "$ec"; }\n'
        'PROMPT_COMMAND="__aegis_command_end; __aegis_prompt_start; ${PROMPT_COMMAND}"\n'
        'trap \'__aegis_command_start\' DEBUG\n'
    )


def _write_zsh_init(path: Path) -> None:
    path.write_text(
        '# Sourced by aegis-spawned zsh for OSC 133 shell integration.\n'
        '[ -f ~/.zshrc ] && . ~/.zshrc\n'
        'precmd() { print -n "\\033]133;D;$?\\007\\033]133;A\\007"; }\n'
        'preexec() { print -n "\\033]133;B\\007"; }\n'
    )


def _write_meta(term_dir: Path, info: TerminalInfo, shell: str) -> None:
    meta = {
        "name": info.name,
        "shell": shell,
        "cwd": info.cwd,
        "started_at": info.started_at,
        "version": 1,
    }
    (term_dir / "meta.json").write_text(json.dumps(meta, indent=2))
```

- [ ] **Step 5: Tests pass; commit.**

```bash
uv run pytest tests/test_terminal_parser.py tests/test_terminal_manager_lifecycle.py -q
git add src/aegis/terminal/pty.py src/aegis/terminal/manager.py tests/test_terminal_manager_lifecycle.py pyproject.toml uv.lock
git commit -m "feat(terminal): PTY wrapper + manager skeleton (spawn/list/close)"
```

---

## Slice 3 — `run` + lock + `read` + ledger

**Files:**
- Modify: `src/aegis/terminal/manager.py` (add `run`, `send_keys`, `read`, reader task)
- Create: `tests/test_terminal_manager_run.py`

The reader task consumes from the PTY, runs bytes through `OSC133Parser`, writes raw bytes to `raw.log`, segments stdout/stderr into the in-progress `_PendingCommand`, and finalizes records on `CommandEnd`.

- [ ] **Step 1: Write failing tests using a real bash subshell.**

```python
# tests/test_terminal_manager_run.py
import asyncio
import pytest
from pathlib import Path
from aegis.terminal.manager import TerminalManager, TerminalNotFound


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "aegis" / "state" / "terminals"


@pytest.mark.asyncio
async def test_run_returns_exit_zero_for_true(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="t1", shell="/bin/bash")
    rec = await mgr.run("t1", "true", writer="agent:tester")
    assert rec.exit == 0
    assert rec.cmd == "true"
    assert rec.writer == "agent:tester"
    await mgr.close("t1")


@pytest.mark.asyncio
async def test_run_returns_nonzero_exit_for_false(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="t2", shell="/bin/bash")
    rec = await mgr.run("t2", "false", writer="agent:tester")
    assert rec.exit == 1
    await mgr.close("t2")


@pytest.mark.asyncio
async def test_run_captures_stdout(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="t3", shell="/bin/bash")
    rec = await mgr.run("t3", "echo hello", writer="agent:tester")
    assert rec.exit == 0
    assert "hello" in rec.stdout
    await mgr.close("t3")


@pytest.mark.asyncio
async def test_run_serializes_concurrent_calls(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="t4", shell="/bin/bash")
    r1, r2 = await asyncio.gather(
        mgr.run("t4", "echo first; sleep 0.05", writer="agent:a"),
        mgr.run("t4", "echo second", writer="agent:b"),
    )
    # Both completed; ledger has them in submission order.
    records = mgr.read("t4", last_n=10)
    cmds = [r.cmd for r in records]
    assert cmds.index("echo first; sleep 0.05") < cmds.index("echo second")
    await mgr.close("t4")


@pytest.mark.asyncio
async def test_read_last_n(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="t5", shell="/bin/bash")
    for i in range(3):
        await mgr.run("t5", f"echo {i}", writer="human")
    recs = mgr.read("t5", last_n=2)
    assert len(recs) == 2
    assert recs[-1].cmd == "echo 2"
    await mgr.close("t5")


@pytest.mark.asyncio
async def test_read_since_seq(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="t6", shell="/bin/bash")
    for i in range(3):
        await mgr.run("t6", f"echo {i}", writer="human")
    recs = mgr.read("t6", since_seq=1)
    # seq 1 is the second record; "since" means strictly after.
    assert [r.cmd for r in recs] == ["echo 2"]
    await mgr.close("t6")


@pytest.mark.asyncio
async def test_run_unknown_terminal_errors(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    with pytest.raises(TerminalNotFound):
        await mgr.run("nope", "true", writer="human")
```

These tests spawn real bash. They are not unit tests in the strict
sense — they're integration tests against a real shell with OSC 133
integration. Mark them `@pytest.mark.integration` if a marker exists
in the repo; otherwise leave unmarked (they run fast).

- [ ] **Step 2: Implement `run`, `send_keys`, `read`, and the reader task.**

Add to `src/aegis/terminal/manager.py`:

```python
# Add to imports
from dataclasses import field
from aegis.terminal.parser import PromptStart, CommandStart, CommandEnd

# Add dataclass
@dataclass
class CommandRecord:
    seq: int
    cmd: str
    writer: str
    started_at: str
    finished_at: str | None
    duration_s: float | None
    exit: int | None
    stdout: str
    stderr: str
    killed_by_restart: bool = False
    timed_out: bool = False

# Add to _TerminalState
@dataclass
class _PendingCommand:
    cmd: str
    writer: str
    started_at: str
    started_monotonic: float
    stdout: bytearray = field(default_factory=bytearray)
    waiter: asyncio.Future | None = None

# Extend _TerminalState with: pending: _PendingCommand | None = None
#                              next_seq: int = 0
#                              ledger_path: Path = ...  (set on init)
#                              raw_log_path: Path = ...
#                              osc133_ok: bool = True

# Implement on TerminalManager:

async def run(
    self,
    name: str,
    cmd: str,
    *,
    writer: str,
    timeout: float | None = None,
) -> CommandRecord:
    state = self._terminals.get(name)
    if state is None:
        raise TerminalNotFound(name)
    async with state.lock:
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future = loop.create_future()
        state.pending = _PendingCommand(
            cmd=cmd,
            writer=writer,
            started_at=_now_iso(),
            started_monotonic=loop.time(),
            waiter=waiter,
        )
        # Write command + newline
        state.pty.write((cmd + "\n").encode("utf-8"))
        try:
            if timeout is None:
                record = await waiter
            else:
                record = await asyncio.wait_for(waiter, timeout=timeout)
        except asyncio.TimeoutError:
            pending = state.pending
            state.pending = None
            record = CommandRecord(
                seq=state.next_seq,
                cmd=cmd,
                writer=writer,
                started_at=pending.started_at,
                finished_at=_now_iso(),
                duration_s=loop.time() - pending.started_monotonic,
                exit=None,
                stdout=pending.stdout.decode("utf-8", errors="replace"),
                stderr="",
                timed_out=True,
            )
            state.next_seq += 1
            _append_ledger(state.ledger_path, record)
        return record

async def send_keys(self, name: str, keys: str, *, writer: str) -> None:
    state = self._terminals.get(name)
    if state is None:
        raise TerminalNotFound(name)
    state.pty.write(keys.encode("utf-8"))

def read(
    self,
    name: str,
    *,
    last_n: int = 5,
    since_seq: int | None = None,
) -> list[CommandRecord]:
    state = self._terminals.get(name)
    if state is None:
        # Allow reading closed-terminal ledger
        term_dir = self.state_dir / name
        if not (term_dir / "ledger.jsonl").exists():
            raise TerminalNotFound(name)
        ledger_path = term_dir / "ledger.jsonl"
    else:
        ledger_path = state.ledger_path
    records = _read_ledger(ledger_path)
    if since_seq is not None:
        return [r for r in records if r.seq > since_seq]
    return records[-last_n:]
```

Update `spawn` to start the reader task and initialize ledger/raw paths:

```python
# Inside spawn, after state is created:
state.ledger_path = term_dir / "ledger.jsonl"
state.raw_log_path = term_dir / "raw.log"
state.raw_log_fh = open(state.raw_log_path, "ab")
state.reader_task = asyncio.create_task(self._reader_loop(state))
```

And implement the reader loop:

```python
async def _reader_loop(self, state: _TerminalState) -> None:
    try:
        while True:
            chunk = await state.pty.read(4096)
            if not chunk:
                # PTY closed
                self._finalize_pending_on_eof(state)
                break
            state.raw_log_fh.write(chunk)
            state.raw_log_fh.flush()
            stripped, events = state.parser.feed(chunk)
            if state.pending is not None and stripped:
                state.pending.stdout.extend(stripped)
            for ev in events:
                self._handle_event(state, ev)
    except asyncio.CancelledError:
        return
    except Exception:
        import traceback
        traceback.print_exc()


def _handle_event(self, state: _TerminalState, ev) -> None:
    if isinstance(ev, CommandEnd) and state.pending is not None:
        pending = state.pending
        state.pending = None
        loop = asyncio.get_running_loop()
        record = CommandRecord(
            seq=state.next_seq,
            cmd=pending.cmd,
            writer=pending.writer,
            started_at=pending.started_at,
            finished_at=_now_iso(),
            duration_s=loop.time() - pending.started_monotonic,
            exit=ev.exit_code,
            stdout=_decode_capped(pending.stdout),
            stderr="",
        )
        state.next_seq += 1
        state.info.last_cmd_at = record.finished_at
        state.info.last_exit = record.exit
        _append_ledger(state.ledger_path, record)
        if pending.waiter is not None and not pending.waiter.done():
            pending.waiter.set_result(record)
        # Slice 4 will hook in notifier here.


def _finalize_pending_on_eof(self, state: _TerminalState) -> None:
    pending = state.pending
    if pending is None or pending.waiter is None or pending.waiter.done():
        return
    loop = asyncio.get_running_loop()
    record = CommandRecord(
        seq=state.next_seq,
        cmd=pending.cmd,
        writer=pending.writer,
        started_at=pending.started_at,
        finished_at=_now_iso(),
        duration_s=loop.time() - pending.started_monotonic,
        exit=None,
        stdout=_decode_capped(pending.stdout),
        stderr="pty closed",
    )
    state.next_seq += 1
    _append_ledger(state.ledger_path, record)
    pending.waiter.set_result(record)
    state.pending = None
```

Helpers (module level):

```python
def _decode_capped(buf: bytearray, cap: int = 64 * 1024) -> str:
    if len(buf) <= cap:
        return buf.decode("utf-8", errors="replace")
    head = buf[: cap // 2]
    tail = buf[-cap // 2 :]
    omitted = len(buf) - cap
    return (
        head.decode("utf-8", errors="replace")
        + f"\n[… {omitted} bytes truncated …]\n"
        + tail.decode("utf-8", errors="replace")
    )


def _append_ledger(path: Path, rec: CommandRecord) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(asdict(rec)) + "\n")


def _read_ledger(path: Path) -> list[CommandRecord]:
    if not path.exists():
        return []
    out: list[CommandRecord] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        out.append(CommandRecord(**d))
    return out
```

- [ ] **Step 3: Tests pass; commit.**

```bash
uv run pytest tests/test_terminal_manager_run.py -q
git add src/aegis/terminal/manager.py tests/test_terminal_manager_run.py
git commit -m "feat(terminal): run + send_keys + read with ledger + lock semantics"
```

---

## Slice 4 — Subscriptions + notifier

**Files:**
- Create: `src/aegis/terminal/notify.py`
- Modify: `src/aegis/terminal/manager.py` (add subscribe/unsubscribe, wire notifier into `_handle_event`)
- Create: `tests/test_terminal_notify.py`

- [ ] **Step 1: Write failing tests.**

```python
# tests/test_terminal_notify.py
import asyncio
import pytest
from pathlib import Path
from aegis.terminal.manager import TerminalManager, CommandRecord
from aegis.terminal.notify import build_inbox_message, make_terminal_notifier


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


def test_build_inbox_message_shape():
    rec = CommandRecord(
        seq=0, cmd="pytest", writer="agent:alice",
        started_at="2026-05-22T14:03:21Z",
        finished_at="2026-05-22T14:03:25Z",
        duration_s=4.2, exit=0,
        stdout="line1\nline2\nline3\n", stderr="",
    )
    msg = build_inbox_message("build", rec)
    assert msg.sender == "term:build"
    assert "pytest" in msg.body
    assert "exit 0" in msg.body
    assert "agent:alice" in msg.body
    assert "line3" in msg.body


def test_build_inbox_message_includes_stderr_block_when_present():
    rec = CommandRecord(
        seq=1, cmd="bad", writer="human",
        started_at="x", finished_at="y", duration_s=0.1, exit=1,
        stdout="", stderr="boom\n",
    )
    msg = build_inbox_message("t", rec)
    assert "stderr" in msg.body
    assert "boom" in msg.body


@pytest.mark.asyncio
async def test_notifier_wakes_subscribers_except_writer(state_dir):
    delivered: list[tuple[str, str]] = []

    class FakeRouter:
        async def deliver(self, handle: str, message) -> None:
            delivered.append((handle, message.body))

    mgr = TerminalManager(state_dir=state_dir)
    mgr.set_notifier(make_terminal_notifier(FakeRouter()))
    await mgr.spawn(name="n1", shell="/bin/bash")
    mgr.subscribe("n1", "agent:alice")
    mgr.subscribe("n1", "agent:bob")
    await mgr.run("n1", "true", writer="agent:alice")
    await asyncio.sleep(0.05)  # let notifier dispatch
    # Bob got the wake; Alice did not (she's the writer).
    handles = {h for h, _ in delivered}
    assert "agent:bob" in handles
    assert "agent:alice" not in handles
    await mgr.close("n1")


@pytest.mark.asyncio
async def test_human_writer_wakes_all_subscribers(state_dir):
    delivered: list[str] = []

    class FakeRouter:
        async def deliver(self, handle: str, message) -> None:
            delivered.append(handle)

    mgr = TerminalManager(state_dir=state_dir)
    mgr.set_notifier(make_terminal_notifier(FakeRouter()))
    await mgr.spawn(name="n2", shell="/bin/bash")
    mgr.subscribe("n2", "agent:alice")
    mgr.subscribe("n2", "agent:bob")
    await mgr.run("n2", "true", writer="human")
    await asyncio.sleep(0.05)
    assert set(delivered) == {"agent:alice", "agent:bob"}
    await mgr.close("n2")
```

- [ ] **Step 2: Implement `src/aegis/terminal/notify.py`.**

```python
"""Inbox notifications for terminal command-finish events."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from aegis.terminal.manager import CommandRecord


STDOUT_TAIL_LINES = 8
STDERR_TAIL_LINES = 4
LINE_TRUNC = 200


@dataclass
class InboxMessage:
    sender: str
    body: str


class InboxRouterLike(Protocol):
    async def deliver(self, handle: str, message: InboxMessage) -> None: ...


def _tail(text: str, n: int) -> str:
    lines = text.splitlines()[-n:]
    return "\n".join(_trunc(ln, LINE_TRUNC) for ln in lines)


def _trunc(line: str, n: int) -> str:
    return line if len(line) <= n else line[: n - 1] + "…"


def build_inbox_message(name: str, rec: CommandRecord) -> InboxMessage:
    exit_part = f"exit {rec.exit}" if rec.exit is not None else "exit ?"
    duration_part = f"{rec.duration_s:.2f}s" if rec.duration_s else "?"
    body = (
        f"$ {rec.cmd}  · run by {rec.writer}\n"
        f"{exit_part} · {duration_part}\n"
        f"──\n"
        f"{_tail(rec.stdout, STDOUT_TAIL_LINES)}"
    )
    if rec.stderr:
        body += f"\n── stderr ──\n{_tail(rec.stderr, STDERR_TAIL_LINES)}"
    return InboxMessage(sender=f"term:{name}", body=body)


Notifier = Callable[[str, CommandRecord, list[str]], Awaitable[None]]


def make_terminal_notifier(router: InboxRouterLike) -> Notifier:
    async def notifier(name: str, rec: CommandRecord, subscribers: list[str]) -> None:
        msg = build_inbox_message(name, rec)
        for handle in subscribers:
            if handle == rec.writer:
                continue
            await router.deliver(handle, msg)
    return notifier
```

- [ ] **Step 3: Wire notifier into manager.**

Add to `TerminalManager`:

```python
def __init__(self, state_dir: str | Path) -> None:
    # ... existing init ...
    self._notifier = None  # set by set_notifier

def set_notifier(self, notifier) -> None:
    self._notifier = notifier

def subscribe(self, name: str, handle: str) -> list[str]:
    state = self._terminals.get(name)
    if state is None:
        raise TerminalNotFound(name)
    state.subscribers.add(handle)
    return sorted(state.subscribers)

def unsubscribe(self, name: str, handle: str) -> None:
    state = self._terminals.get(name)
    if state is None:
        raise TerminalNotFound(name)
    state.subscribers.discard(handle)
```

And in `_handle_event`, after writing the record, dispatch:

```python
if self._notifier is not None:
    asyncio.create_task(
        self._notifier(state.info.name, record, sorted(state.subscribers))
    )
```

- [ ] **Step 4: Tests pass; commit.**

```bash
uv run pytest tests/test_terminal_notify.py -q
git add src/aegis/terminal/notify.py src/aegis/terminal/manager.py tests/test_terminal_notify.py
git commit -m "feat(terminal): subscriber wake on command-finish via inbox router"
```

---

## Slice 5 — Eight MCP tools

**Files:**
- Modify: `src/aegis/mcp/server.py` — register 8 new `@server.tool`s
- Modify: `src/aegis/mcp/bridge.py` — add `terminal_manager` to `AppBridge` Protocol
- Modify: `src/aegis/core/manager.py` — `SessionManager` gains `terminal_manager` attribute + `attach_terminal_manager`
- Modify: `src/aegis/tui/app.py` — instantiate `TerminalManager`, set notifier, pass to `SessionManager`
- Modify: `src/aegis/cli.py` — wire `TerminalManager` in `_serve` and `_run`
- Create: `tests/test_terminal_mcp.py`
- Modify: `tests/test_mcp_server.py` (update expected tool list)

Pattern is identical to how canvas was wired. Read `src/aegis/mcp/server.py` to see the canvas tools — copy that shape.

- [ ] **Step 1: Add `terminal_manager` to `AppBridge` Protocol.**

In `src/aegis/mcp/bridge.py`:

```python
class AppBridge(Protocol):
    queue_manager: object
    inbox_router: object
    canvas_manager: object
    terminal_manager: object  # add this
    # ... rest unchanged
```

- [ ] **Step 2: Add `terminal_manager` to `SessionManager`.**

In `src/aegis/core/manager.py`, add to `__init__`:

```python
self.terminal_manager = None
```

And add method:

```python
def attach_terminal_manager(self, tm) -> None:
    self.terminal_manager = tm
```

- [ ] **Step 3: Write failing MCP tests.**

```python
# tests/test_terminal_mcp.py
import asyncio
import pytest
from pathlib import Path
from aegis.mcp.server import build_server
from aegis.terminal.manager import TerminalManager
from aegis.terminal.notify import make_terminal_notifier


class FakeRouter:
    def __init__(self):
        self.delivered = []
    async def deliver(self, handle, msg):
        self.delivered.append((handle, msg))


class FakeBridge:
    def __init__(self, tm):
        self.queue_manager = None
        self.inbox_router = FakeRouter()
        self.canvas_manager = None
        self.terminal_manager = tm
    # Add other attributes AppBridge expects — copy from existing FakeBridge


@pytest.mark.asyncio
async def test_term_spawn_and_list(tmp_path):
    tm = TerminalManager(state_dir=tmp_path / "s")
    bridge = FakeBridge(tm)
    server = build_server(bridge)
    res = await server.call_tool("aegis_term_spawn", {"name": "build", "from_handle": "agent:a"})
    data = res.structured_content or res.data
    assert data["name"] == "build"
    res2 = await server.call_tool("aegis_term_list", {})
    names = [t["name"] for t in (res2.structured_content or res2.data)]
    assert "build" in names
    await tm.close("build")


@pytest.mark.asyncio
async def test_term_run_returns_record(tmp_path):
    tm = TerminalManager(state_dir=tmp_path / "s")
    bridge = FakeBridge(tm)
    server = build_server(bridge)
    await server.call_tool("aegis_term_spawn", {"name": "t", "from_handle": "agent:a"})
    res = await server.call_tool("aegis_term_run", {"name": "t", "cmd": "true", "from_handle": "agent:a"})
    data = res.structured_content or res.data
    assert data["exit"] == 0
    await tm.close("t")
```

- [ ] **Step 4: Register the 8 tools in `src/aegis/mcp/server.py`.**

Follow the canvas-tools pattern. Each tool:
1. Accepts `from_handle` (optional except for subscribe/unsubscribe).
2. Forwards to `bridge.terminal_manager.<method>`.
3. Serializes the return value via small helpers (`_terminal_info_to_dict`, `_command_record_to_dict`).
4. Errors map: `TerminalAlreadyExists` → `409`, `TerminalNotFound` → `404` (or whatever pattern canvas uses).

Update `BRIEFING` constant to add a `## SHARED TERMINALS` section enumerating the new tools and the `term:<name>` inbox-sender prefix.

Tool list to register: `aegis_term_spawn`, `aegis_term_list`, `aegis_term_run`, `aegis_term_keys`, `aegis_term_read`, `aegis_term_subscribe`, `aegis_term_unsubscribe`, `aegis_term_close`.

- [ ] **Step 5: Wire `TerminalManager` into `AegisApp` (`src/aegis/tui/app.py`) and `_serve` / `_run` (`src/aegis/cli.py`).**

In each:
```python
terminal_manager = TerminalManager(state_dir=workspace_root / ".aegis" / "state" / "terminals")
terminal_manager.set_notifier(make_terminal_notifier(inbox_router))
session_manager.attach_terminal_manager(terminal_manager)
```

Add `terminal_manager` attribute on `AegisApp` so it satisfies `AppBridge`.

- [ ] **Step 6: Update `tests/test_mcp_server.py` expected tool list to include the 8 new tools.**

- [ ] **Step 7: Run full hermetic test suite.**

```bash
uv run pytest -q -x --ignore=tests/test_opencode_live_*.py
```

- [ ] **Step 8: Commit.**

```bash
git add -A
git commit -m "feat(mcp): expose 8 terminal tools through AppBridge + BRIEFING"
```

---

## Slice 6 — TUI tab type

**Files:**
- Create: `src/aegis/tui/terminal_tab.py` — `TerminalTab` widget
- Modify: `src/aegis/tui/app.py` — wire the new tab type into `Ctrl+T` chooser and tab management
- Modify: `src/aegis/tui/tab_chooser.py` (or wherever the Ctrl+T overlay lives) — add "Terminal…" option

Implementation notes:

- Each command in `ledger.jsonl` renders as a Textual `Static` block: header line (`$ <cmd>  · <writer>  · <time>`), output body (RichLog with stdout/stderr lines, escape-stripped, ANSI color preserved via `Text.from_ansi`), footer chip (`↳ exit N · Ns`).
- Live commands stream into a special "running" block at the bottom — header shows `$ <cmd>` followed by streaming output; footer is replaced with the exit chip when the command finishes.
- Input bar at the bottom: single `Input` widget. Default mode is "run" (Enter → `aegis_term_run` synth). `Ctrl+K` toggles "raw" mode (each keystroke → `aegis_term_keys`).
- Status strip above the input: `<cwd> · pid <N> · <shell> · last exit <N> · subscribers: <N>`.
- Click any command block → copy `cmd` to clipboard (re-use the click-to-copy hook agent tabs already have).
- Tab title format: `term:<name>` with state dot (`●` idle, `⠹` running, `*` sticky-finished after background).

- [ ] **Step 1: Implement `TerminalTab` widget.** Aim for ~250 LoC. Subscribe to TerminalManager events via a queue/callback that the manager exposes for the TUI's benefit (add `add_render_observer(name, callback)` to TerminalManager if it isn't already there; the callback fires on every reader-loop chunk and on every command finalization).

- [ ] **Step 2: Hook `Ctrl+T` chooser** to add a "Terminal…" entry that prompts for `name` and `cwd`, then calls `terminal_manager.spawn(...)` and opens a `TerminalTab`.

- [ ] **Step 3: Manual visual check.**

```bash
uv run aegis --clean
```

Then in the TUI:
1. `Ctrl+T` → Terminal… → name `smoke` → Enter.
2. Verify a new `term:smoke` tab is open and the bash prompt is visible.
3. Type `echo hello` Enter → verify the line renders as a block with exit 0.
4. Type `false` Enter → verify red exit-1 chip.
5. Run `sleep 30` → press `Ctrl+K` → press `Ctrl+C` → verify interrupt + ledger record with exit 130.
6. `Ctrl+Q`.

No automated test for this slice — visual integration only. The next slice covers automated coverage.

- [ ] **Step 4: Commit.**

```bash
git add -A
git commit -m "feat(tui): TerminalTab widget — command-session log + raw-key mode"
```

---

## Slice 7 — Persistence (`--resume` / `--clean`)

**Files:**
- Modify: `src/aegis/core/persistence.py` (or wherever SessionManager save/load lives) — add `terminals` section to the saved workspace snapshot
- Modify: `src/aegis/core/manager.py` — `SessionManager.save_workspace` snapshots live terminals; `SessionManager.restore_workspace` re-spawns them
- Modify: `src/aegis/terminal/manager.py` — `spawn` sweeps existing `ledger.jsonl` for stale in-flight records and flags them `killed_by_restart`
- Create: `tests/test_terminal_persistence.py`

- [ ] **Step 1: Write failing tests.**

```python
# tests/test_terminal_persistence.py
import asyncio
import json
import pytest
from pathlib import Path
from aegis.terminal.manager import TerminalManager, CommandRecord


@pytest.mark.asyncio
async def test_stale_in_flight_marked_killed_by_restart(tmp_path):
    state_dir = tmp_path / "s"
    term_dir = state_dir / "build"
    term_dir.mkdir(parents=True)
    (term_dir / "meta.json").write_text(json.dumps({
        "name": "build", "shell": "/bin/bash", "cwd": str(tmp_path),
        "started_at": "2026-05-21T00:00:00Z", "version": 1,
    }))
    # Simulate a prior session that died with an in-flight record:
    stale = {
        "seq": 0, "cmd": "sleep 100", "writer": "agent:a",
        "started_at": "2026-05-21T00:00:01Z", "finished_at": None,
        "duration_s": None, "exit": None,
        "stdout": "", "stderr": "", "killed_by_restart": False,
        "timed_out": False,
    }
    (term_dir / "ledger.jsonl").write_text(json.dumps(stale) + "\n")
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="build", shell="/bin/bash")
    recs = mgr.read("build", last_n=10)
    assert recs[0].killed_by_restart is True
    assert recs[0].exit is None
    await mgr.close("build")
```

- [ ] **Step 2: Implement the sweep in `spawn`.**

After `_TerminalState` is created and `next_seq` is computed from existing ledger length:

```python
# Mark any unfinished record as killed_by_restart and rewrite the ledger
existing = _read_ledger(state.ledger_path)
mutated = False
for r in existing:
    if r.finished_at is None and not r.killed_by_restart:
        r.killed_by_restart = True
        mutated = True
if mutated:
    state.ledger_path.write_text(
        "\n".join(json.dumps(asdict(r)) for r in existing) + "\n"
    )
state.next_seq = len(existing)
```

- [ ] **Step 3: Wire SessionManager save/restore.**

Add to the workspace snapshot: `terminals: [{name, shell, cwd, env_keys}, ...]`. On restore (when `--clean` is **false**), iterate the saved list and call `terminal_manager.spawn` for each. The TUI then opens a `TerminalTab` per restored terminal.

For `--clean`, skip the `terminals` block entirely.

The "show prior session ledger with reduced opacity" rendering happens in `TerminalTab` initialization: when the tab is created over an existing-ledger terminal, render existing records dimmed with a `--- end of previous session ---` separator before the new live content.

- [ ] **Step 4: Tests pass; commit.**

```bash
uv run pytest tests/test_terminal_persistence.py -q
uv run pytest -q -x --ignore=tests/test_opencode_live_*.py
git add -A
git commit -m "feat(terminal): persistence — save/restore on --resume, killed_by_restart sweep"
```

---

## Slice 8 — Docs + smoke

**Files:**
- Create: `docs/terminals.md`
- Modify: `mkdocs.yml` — add `Terminals: terminals.md` to the Concepts nav (after Canvas)
- Modify: `README.md` — add `▦` → `▷` style entry for terminals (or replace one — match the four-primitive count in landing; this makes it five)
- Modify: `docs/index.md` — add a fifth `.aegis-card` to the primitives grid (mini terminal mockup showing `aegis_term_spawn` + subscriber wake)
- Modify: `CHANGELOG.md` — add `Added: live terminals` under `[Unreleased]`

- [ ] **Step 1: Write `docs/terminals.md`.** Mirror `docs/canvas.md` shape: model, MCP tools table, notification payload, worked example, state-on-disk, limitations (v1). Reference the spec at the end.

- [ ] **Step 2: Update `mkdocs.yml`.** Append `- Terminals: terminals.md` to the Concepts list (after Canvas, before Workflows).

- [ ] **Step 3: Update `README.md` and `docs/index.md`** to add the fifth primitive. The landing-page primitives grid currently has 4 cards in `repeat(2, minmax(0, 1fr))` — change to `repeat(2, minmax(0, 1fr))` rows (so 2x3 with the last cell empty or 2x2+1 — pick the cleaner layout; centering the orphaned 5th card with `grid-column: 1 / -1` is reasonable). Update the README's "Four primitives" → "Five primitives" wording.

- [ ] **Step 4: Update `CHANGELOG.md`.**

```markdown
### Added
- Live terminals — fifth coordination primitive. Real shared PTY with
  OSC 133 shell integration; spawn/list/run/keys/read/subscribe/
  unsubscribe/close MCP tools; new TUI tab type; session-scoped
  persistence via `aegis --resume`.
```

- [ ] **Step 5: Manual smoke on bash and zsh.**

```bash
# Bash:
SHELL=/bin/bash uv run aegis --clean
# Inside TUI: Ctrl+T → Terminal… → "build" → cwd default → spawn.
# Type: pytest tests/test_terminal_parser.py -q
# Open a second tab (Ctrl+T → agent), in that agent call:
#   aegis_term_subscribe(name="build", from_handle="<my-handle>")
# Back in the build tab, run another command — verify ✉ block appears
# in the agent's transcript.
# Ctrl+K → \x03 to test Ctrl-C interrupt of a `sleep 30`.
# Ctrl+Q.

# Zsh:
SHELL=/bin/zsh uv run aegis --clean
# Repeat above.

# Resume:
uv run aegis  # (no --clean) — verify the build tab is restored as
              # a fresh shell with the prior ledger rendered dimmed.
```

- [ ] **Step 6: Final commit.**

```bash
git add docs/terminals.md mkdocs.yml README.md docs/index.md CHANGELOG.md
git commit -m "docs(terminals): add Terminals concept page + landing/README/changelog"
git push origin main
```

---

## Done definition

- All 8 slices committed on `main`.
- `uv run pytest -q --ignore=tests/test_opencode_live_*.py` is fully green.
- The 8 MCP tools are registered and discoverable.
- The TUI tab type works visually on bash and zsh.
- `aegis --resume` restores terminals; `aegis --clean` does not.
- Documentation reflects the fifth primitive across spec, docs page, mkdocs nav, README, landing page, and changelog.
- Final push to `origin/main` is clean.

Ping Alex on completion with: `✅ live terminals v1 landed on main`. If any slice fails repeatedly (>2 attempts on the same red test), stop and ping with the failing test name + last error.
