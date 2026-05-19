from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from aegis.config import Agent, Effort, Permission
from aegis.events import Event, Result, parse
from aegis.drivers.base import HarnessDriver, HarnessSession
from aegis.mcp import PRIMING, mcp_config_json

# claude stream-json emits one JSON object per line; a single line carries
# the full payload of a tool_result (e.g. a large file Read). asyncio's
# StreamReader default line limit is 64 KiB — far too small (reading SOUL.md
# alone exceeds it). Give the reader generous headroom.
_STREAM_LIMIT = 16 * 1024 * 1024  # 16 MiB

_PERMISSION_MODE = {
    Permission.read: "plan",
    Permission.write: "acceptEdits",
    Permission.full: "bypassPermissions",
    Permission.auto: "auto",
}

_EFFORT = {
    Effort.low: "low",
    Effort.medium: "medium",
    Effort.high: "high",
    Effort.max: "max",
}


class ClaudeSession(HarnessSession):
    def __init__(self, argv: list[str], cwd: str) -> None:
        self._argv = argv
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._reader: asyncio.Task | None = None

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._argv,
            cwd=self._cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LIMIT,
        )
        self._reader = asyncio.create_task(self._pump_stdout())

    async def _pump_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            async for raw in self._proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    await self._queue.put(parse(line))
        except Exception:
            # A stream/parse failure must not silently kill the turn: the
            # finally below still delivers the sentinel so events() ends
            # the turn instead of deadlocking on an empty queue forever.
            pass
        finally:
            await self._queue.put(None)  # always signal stream end

    async def send(self, text: str) -> None:
        assert self._proc and self._proc.stdin
        msg = {"type": "user",
               "message": {"role": "user", "content": text}}
        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self._proc.stdin.drain()

    async def events(self) -> AsyncIterator[Event]:
        """Yield events until this turn's Result (or stream close)."""
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev
            if isinstance(ev, Result):
                return

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()


class ClaudeDriver(HarnessDriver):
    def build_argv(self, agent: Agent, cwd: str,
                   mcp_url: str, handle: str) -> list[str]:
        return [
            "claude", "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--replay-user-messages",
            "--verbose",  # required by claude with -p + stream-json output
            "--model", agent.model,
            "--effort", _EFFORT[agent.effort],
            "--permission-mode", _PERMISSION_MODE[agent.permission],
            "--mcp-config", mcp_config_json(mcp_url),
            "--strict-mcp-config",
            # The aegis plane is aegis's own server, trusted by
            # construction. Allowlist it so its tools never hit an
            # interactive permission prompt — there is none under
            # `claude -p` stream-json, so an un-allowed call just fails.
            "--allowedTools", "mcp__aegis",
            "--append-system-prompt", PRIMING.format(handle=handle),
        ]

    def session(self, agent: Agent, cwd: str,
                mcp_url: str, handle: str) -> ClaudeSession:
        return ClaudeSession(
            self.build_argv(agent, cwd, mcp_url, handle), cwd)
