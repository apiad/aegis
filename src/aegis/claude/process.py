"""The ``claude -p`` child: spawn it, write to it, read it, end it.

stdout is read line by line and handed to ``on_line``; stderr goes to a file
and the last lines are kept for the exit report. A single stdout line can
carry a whole file (a large Read result), hence the generous line limit.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Callable
from pathlib import Path

from ..profiles import PERMISSION_MODE

LINE_LIMIT = 64 * 1024 * 1024
STDERR_TAIL = 20
TERM_GRACE_S = 5.0


NO_MCP = '{"mcpServers":{}}'


def build_argv(
    claude_bin: str,
    model: str,
    effort: str,
    permission: str,
    resume: str | None = None,
    mcp_config: str = NO_MCP,
    system_prompt: str | None = None,
) -> list[str]:
    argv = [
        claude_bin,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--replay-user-messages",
        "--verbose",
        "--permission-mode",
        PERMISSION_MODE.get(permission, permission),
        "--strict-mcp-config",
        "--mcp-config",
        mcp_config,
    ]
    if system_prompt:
        argv += ["--append-system-prompt", system_prompt]
    if model:
        argv += ["--model", model]
    if effort:
        argv += ["--effort", effort]
    if resume:
        argv += ["--resume", resume]
    return argv


class ClaudeProcess:
    def __init__(
        self,
        argv: list[str],
        cwd: Path,
        stderr_path: Path,
        on_line: Callable[[str], None],
        on_exit: Callable[[int, list[str]], None],
    ) -> None:
        self.argv = argv
        self.cwd = cwd
        self.stderr_path = stderr_path
        self._on_line = on_line
        self._on_exit = on_exit
        self._proc: asyncio.subprocess.Process | None = None
        self._tasks: list[asyncio.Task] = []
        self._stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL)

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        """Raises FileNotFoundError when the binary is missing."""
        self.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self._proc = await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=str(self.cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=LINE_LIMIT,
        )
        err = asyncio.create_task(self._pump_stderr())
        self._tasks = [err, asyncio.create_task(self._pump_stdout(err))]

    async def _pump_stdout(self, stderr_done: asyncio.Task) -> None:
        proc = self._proc
        assert proc and proc.stdout
        while True:
            try:
                raw = await proc.stdout.readline()
            except ValueError:
                # Over LINE_LIMIT: the reader dropped it. Say so and go on.
                self._on_line(
                    f"(a stdout line over {LINE_LIMIT // (1024 * 1024)} MiB was dropped)"
                )
                continue
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.strip():
                self._on_line(line)
        code = await proc.wait()
        await stderr_done
        self._on_exit(code, list(self._stderr_tail))

    async def _pump_stderr(self) -> None:
        proc = self._proc
        assert proc and proc.stderr
        with self.stderr_path.open("ab") as f:
            async for raw in proc.stderr:
                f.write(raw)
                f.flush()
                self._stderr_tail.append(raw.decode("utf-8", "replace").rstrip())

    async def write(self, obj: dict) -> None:
        proc = self._proc
        if not (proc and proc.stdin) or proc.returncode is not None:
            raise BrokenPipeError("claude is not running")
        proc.stdin.write((json.dumps(obj) + "\n").encode())
        await proc.stdin.drain()

    async def terminate(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), TERM_GRACE_S)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        for t in self._tasks:
            if not t.done():
                try:
                    await asyncio.wait_for(t, 1)
                except (TimeoutError, asyncio.CancelledError):
                    t.cancel()
