"""OpenCode driver — launches ``opencode run <message> --format json``
and parses its line-by-line JSON events.

V1 semantics: one-shot per ``send`` (same as Gemini). OpenCode does
support ``--continue`` / ``--session`` for resuming a session, but v1
does not thread that — each ``send`` is independent. Multi-turn-with-
memory is a v2 concern.

MCP integration: like Gemini, OpenCode's MCP config is global
(``opencode mcp``). v1 launches opencode WITHOUT injecting aegis-mcp;
workers do their task but cannot call aegis tools. Sufficient for
queue-worker semantics.

Model strings follow OpenCode's ``provider/model`` format
(e.g. ``opencode/claude-sonnet-4-6``, ``opencode/gemini-3-flash``,
``opencode/gpt-5``). ``opencode models`` lists what's available.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from aegis.config import Agent
from aegis.drivers.base import HarnessDriver, HarnessSession
from aegis.drivers.opencode_parse import parse
from aegis.events import Event, Result

_STREAM_LIMIT = 16 * 1024 * 1024


class OpenCodeSession(HarnessSession):
    """One-shot session over ``opencode run``."""

    def __init__(self, base_argv: list[str], cwd: str) -> None:
        self._base_argv = base_argv
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._reader: asyncio.Task | None = None
        self._sent = False

    async def start(self) -> None:
        return

    async def send(self, text: str) -> None:
        if self._sent:
            raise RuntimeError(
                "OpenCodeSession is one-shot: spawn a new session per "
                "turn. (V1 limitation.)")
        self._sent = True
        argv = list(self._base_argv) + [text]
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self._cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LIMIT,
        )
        self._reader = asyncio.create_task(self._pump_stdout())

    async def _pump_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        saw_result = False
        try:
            async for raw in self._proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("{"):
                    # OpenCode may emit banner / ANSI noise before the
                    # JSON stream begins. Skip anything that isn't JSON.
                    continue
                ev = parse(line)
                await self._queue.put(ev)
                if isinstance(ev, Result):
                    saw_result = True
        except Exception:  # noqa: BLE001
            pass
        finally:
            if not saw_result:
                await self._queue.put(Result(
                    duration_ms=None, is_error=True,
                    input_tokens=None, output_tokens=None))
            await self._queue.put(None)

    async def events(self) -> AsyncIterator[Event]:
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


class OpenCodeDriver(HarnessDriver):
    def build_argv(self, agent: Agent, cwd: str,
                   mcp_url: str, handle: str) -> list[str]:
        """OpenCode argv minus the trailing message (added per-send).

        ``mcp_url`` and ``handle`` are accepted for protocol parity with
        Claude but not consumed in v1 — OpenCode's MCP config is global,
        not per-invocation. Per-session MCP injection is a v2 item.
        """
        argv = [
            "opencode", "run",
            "--format", "json",
            "-m", agent.model,
        ]
        return argv

    def session(self, agent: Agent, cwd: str,
                mcp_url: str, handle: str) -> OpenCodeSession:
        return OpenCodeSession(
            self.build_argv(agent, cwd, mcp_url, handle), cwd)
