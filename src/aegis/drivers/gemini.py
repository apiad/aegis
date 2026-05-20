"""Gemini CLI driver — launches ``gemini -p <prompt> --output-format
stream-json`` and parses its line-by-line JSON events.

V1 semantics: one-shot. Each ``send()`` call spawns a fresh ``gemini``
process with the given prompt (Gemini's headless mode doesn't multiplex
turns over a single subprocess like Claude's stream-json INPUT does).
The first ``send()`` after ``start()`` runs to completion; subsequent
``send()`` calls within the same ``GeminiSession`` raise ``RuntimeError``.

This suits the queue-worker pattern (one task, one worker, exit) and
the workflow ``engine.send`` + ``engine.drain`` pattern (each
workflow-driven message can spawn a fresh worker via the queue if
multi-turn-with-memory is needed). Multi-turn drive of a single Gemini
session is a v2 concern; if you need conversation history, pass full
context in each prompt.

MCP integration: Gemini's MCP config is global (``gemini mcp add``),
not per-invocation. v1 launches gemini WITHOUT injecting aegis-mcp —
spawned Gemini workers can do their task but cannot call aegis tools.
For inter-provider task passing this is fine (the substrate captures
the worker's final assistant text as the result and returns it to the
producer through the inbox). Per-session MCP injection is a follow-up.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from aegis.config import Agent, Permission
from aegis.drivers.base import HarnessDriver, HarnessSession
from aegis.drivers.gemini_parse import parse
from aegis.events import Event, Result

_STREAM_LIMIT = 16 * 1024 * 1024  # match the Claude session — large
# tool_result payloads can exceed asyncio's 64 KiB default.

# Gemini's approval-mode maps to aegis Permission.
_APPROVAL_MODE = {
    Permission.read: "plan",
    Permission.write: "auto_edit",
    Permission.full: "yolo",
    Permission.auto: "default",
}


class GeminiSession(HarnessSession):
    """One-shot session over ``gemini -p``. ``send`` spawns the subprocess;
    ``events`` yields parsed events until the subprocess exits. ``close``
    terminates the subprocess if still running."""

    def __init__(self, base_argv: list[str], cwd: str) -> None:
        self._base_argv = base_argv      # everything except the -p prompt
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._reader: asyncio.Task | None = None
        self._sent = False

    async def start(self) -> None:
        # No-op: gemini is one-shot — the subprocess is spawned in `send`.
        return

    async def send(self, text: str) -> None:
        if self._sent:
            raise RuntimeError(
                "GeminiSession is one-shot: spawn a new session for each "
                "turn. (V1 limitation; track if multi-turn is needed.)")
        self._sent = True
        argv = list(self._base_argv) + ["-p", text]
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
                    # Gemini prefixes its JSON stream with banner /
                    # warning lines (e.g. "[WARN] Skipping..."). Skip
                    # anything that isn't a JSON object.
                    continue
                ev = parse(line)
                await self._queue.put(ev)
                if isinstance(ev, Result):
                    saw_result = True
        except Exception:  # noqa: BLE001 — never silently kill a turn
            pass
        finally:
            if not saw_result:
                # Subprocess ended without emitting a result event —
                # synthesize one so events() terminates cleanly.
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


class GeminiDriver(HarnessDriver):
    def build_argv(self, agent: Agent, cwd: str,
                   mcp_url: str, handle: str) -> list[str]:
        """Build the ``gemini`` argv WITHOUT the -p prompt (which is
        injected per-send by GeminiSession). ``mcp_url`` and ``handle``
        are accepted for protocol compatibility with Claude but Gemini
        v1 does not consume them (per-invocation MCP config is a v2
        item — see module docstring).
        """
        argv = [
            "gemini",
            "--output-format", "stream-json",
            "-m", agent.model,
            "--approval-mode", _APPROVAL_MODE[agent.permission],
        ]
        return argv

    def session(self, agent: Agent, cwd: str,
                mcp_url: str, handle: str) -> GeminiSession:
        return GeminiSession(
            self.build_argv(agent, cwd, mcp_url, handle), cwd)
