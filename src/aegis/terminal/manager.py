"""TerminalManager — owns live PTY terminals and their ledgers."""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
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
        await asyncio.sleep(0)
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
