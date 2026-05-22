"""TerminalManager — owns live PTY terminals and their ledgers."""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from aegis.terminal.pty import AsyncPty
from aegis.terminal.parser import CommandEnd, CommandStart, OSC133Parser, PromptStart


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


@dataclass
class _PendingCommand:
    cmd: str
    writer: str
    started_at: str
    started_monotonic: float
    stdout: bytearray = field(default_factory=bytearray)
    waiter: asyncio.Future | None = None


@dataclass
class _TerminalState:
    info: TerminalInfo
    pty: AsyncPty
    state_dir: Path
    ledger_path: Path
    raw_log_path: Path
    raw_log_fh: IO[bytes] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    parser: OSC133Parser = field(default_factory=OSC133Parser)
    subscribers: set[str] = field(default_factory=set)
    reader_task: asyncio.Task | None = None
    pending: _PendingCommand | None = None
    next_seq: int = 0
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    osc133_ok: bool = True
    # Render observers: callables fired from the reader loop on
    # streaming chunks and command finalization. Signature is
    # (kind: str, payload: dict) — kind ∈ {"chunk", "command_end"}.
    render_observers: list = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class TerminalManager:
    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._terminals: dict[str, _TerminalState] = {}
        self._spawn_lock = asyncio.Lock()
        self._notifier = None

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

    def subscribers(self, name: str) -> list[str]:
        state = self._terminals.get(name)
        if state is None:
            raise TerminalNotFound(name)
        return sorted(state.subscribers)

    def add_render_observer(self, name: str, callback) -> None:
        """Register a callback fired on every reader-loop chunk and on
        every command finalization. Callback signature:
            (kind: str, payload: dict) -> None
        where kind ∈ {"chunk", "command_end"}. Used by the TUI tab to
        stream live output; callback runs in the asyncio loop thread."""
        state = self._terminals.get(name)
        if state is None:
            raise TerminalNotFound(name)
        state.render_observers.append(callback)

    def remove_render_observer(self, name: str, callback) -> None:
        state = self._terminals.get(name)
        if state is None:
            return
        try:
            state.render_observers.remove(callback)
        except ValueError:
            pass

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
            ledger_path = term_dir / "ledger.jsonl"
            raw_log_path = term_dir / "raw.log"
            state = _TerminalState(
                info=info,
                pty=pty,
                state_dir=term_dir,
                ledger_path=ledger_path,
                raw_log_path=raw_log_path,
            )
            state.raw_log_fh = open(raw_log_path, "ab")
            existing = _read_ledger(ledger_path)
            # Sweep stale in-flight records from a prior process that
            # died with a command still running. Mark them as killed by
            # the restart so the ledger stays consistent.
            mutated = False
            for r in existing:
                if r.finished_at is None and not r.killed_by_restart:
                    r.killed_by_restart = True
                    mutated = True
            if mutated:
                _rewrite_ledger(ledger_path, existing)
            state.next_seq = len(existing)
            self._terminals[name] = state
            _write_meta(term_dir, info, shell)
            state.reader_task = asyncio.create_task(self._reader_loop(state))
            # Wait for the shell to print its first prompt so the initial
            # PROMPT_COMMAND emission is consumed before any run() races.
            try:
                await asyncio.wait_for(state.ready.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                state.osc133_ok = False
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
            try:
                await state.reader_task
            except (asyncio.CancelledError, Exception):
                pass
        state.pty.close()
        await asyncio.sleep(0)
        state.pty.close(force=True)
        if state.raw_log_fh is not None:
            try:
                state.raw_log_fh.close()
            except Exception:
                pass
        if purge:
            import shutil
            shutil.rmtree(state.state_dir, ignore_errors=True)

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
                    started_at=pending.started_at if pending else _now_iso(),
                    finished_at=_now_iso(),
                    duration_s=loop.time() - (pending.started_monotonic if pending else loop.time()),
                    exit=None,
                    stdout=pending.stdout.decode("utf-8", errors="replace") if pending else "",
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

    async def _reader_loop(self, state: _TerminalState) -> None:
        try:
            while True:
                chunk = await state.pty.read(4096)
                if not chunk:
                    self._finalize_pending_on_eof(state)
                    break
                if state.raw_log_fh is not None:
                    state.raw_log_fh.write(chunk)
                    state.raw_log_fh.flush()
                stripped, events = state.parser.feed(chunk)
                if state.pending is not None and stripped:
                    state.pending.stdout.extend(stripped)
                if stripped:
                    self._fire_observers(state, "chunk", {"data": stripped})
                for ev in events:
                    self._handle_event(state, ev)
        except asyncio.CancelledError:
            return
        except Exception:
            import traceback
            traceback.print_exc()

    def _handle_event(self, state: _TerminalState, ev) -> None:
        if isinstance(ev, PromptStart):
            state.ready.set()
            return
        if isinstance(ev, CommandStart):
            return
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
            self._fire_observers(state, "command_end", {"record": record})
            if self._notifier is not None:
                asyncio.create_task(
                    self._notifier(state.info.name, record, sorted(state.subscribers))
                )

    def _fire_observers(self, state: _TerminalState, kind: str, payload: dict) -> None:
        for cb in list(state.render_observers):
            try:
                cb(kind, payload)
            except Exception:
                import traceback
                traceback.print_exc()

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
    return [shell, "-i"]


def _build_env(shell: str, term_dir: Path, base: dict[str, str]) -> dict[str, str]:
    env = dict(base)
    if Path(shell).name == "zsh":
        env["ZDOTDIR"] = str(term_dir / ".zdotdir")
    env["AEGIS_TERM"] = "1"
    return env


def _write_bash_init(path: Path) -> None:
    # OSC 133 shell integration. Notes:
    # - `__aegis_precmd` runs as PROMPT_COMMAND. It captures $? on its
    #   FIRST line so the user command's exit code is preserved.
    # - DEBUG trap is filtered (case BASH_COMMAND in __aegis_*) so it
    #   doesn't fire for our own helper functions, which would otherwise
    #   reset $? to 0 before precmd reads it.
    # - We skip the initial D emission (before any user command has run)
    #   using the __aegis_in_cmd flag.
    path.write_text(
        '# Sourced by aegis-spawned bash for OSC 133 shell integration.\n'
        '[ -f /etc/bashrc ] && . /etc/bashrc\n'
        '[ -f ~/.bashrc ] && . ~/.bashrc\n'
        '__aegis_precmd() {\n'
        '  local ec=$?\n'
        '  if [ -n "${__aegis_in_cmd:-}" ]; then\n'
        '    printf "\\033]133;D;%d\\007" "$ec"\n'
        '  fi\n'
        '  __aegis_in_cmd=\n'
        '  printf "\\033]133;A\\007"\n'
        '}\n'
        '__aegis_preexec() {\n'
        '  case "$BASH_COMMAND" in\n'
        '    __aegis_*) return ;;\n'
        '  esac\n'
        '  if [ -z "${__aegis_in_cmd:-}" ]; then\n'
        '    __aegis_in_cmd=1\n'
        '    printf "\\033]133;B\\007"\n'
        '  fi\n'
        '}\n'
        'PROMPT_COMMAND=\'__aegis_precmd\'\n'
        "trap '__aegis_preexec' DEBUG\n"
    )


def _write_zsh_init(path: Path) -> None:
    path.write_text(
        '# Sourced by aegis-spawned zsh for OSC 133 shell integration.\n'
        '[ -f ~/.zshrc ] && . ~/.zshrc\n'
        '__aegis_in_cmd=\n'
        'precmd() {\n'
        '  local ec=$?\n'
        '  if [ -n "$__aegis_in_cmd" ]; then\n'
        '    print -n "\\033]133;D;$ec\\007"\n'
        '  fi\n'
        '  __aegis_in_cmd=\n'
        '  print -n "\\033]133;A\\007"\n'
        '}\n'
        'preexec() {\n'
        '  __aegis_in_cmd=1\n'
        '  print -n "\\033]133;B\\007"\n'
        '}\n'
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


def _decode_capped(buf: bytearray, cap: int = 64 * 1024) -> str:
    if len(buf) <= cap:
        return buf.decode("utf-8", errors="replace")
    head = buf[: cap // 2]
    tail = buf[-cap // 2:]
    omitted = len(buf) - cap
    return (
        head.decode("utf-8", errors="replace")
        + f"\n[… {omitted} bytes truncated …]\n"
        + tail.decode("utf-8", errors="replace")
    )


def _append_ledger(path: Path, rec: CommandRecord) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(asdict(rec)) + "\n")


def _rewrite_ledger(path: Path, records: list[CommandRecord]) -> None:
    payload = "".join(json.dumps(asdict(r)) + "\n" for r in records)
    path.write_text(payload)


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
