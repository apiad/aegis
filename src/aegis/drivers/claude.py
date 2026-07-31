from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

from aegis.config import Agent, Effort, Permission
from aegis.events import Event, ParserState, Result, parse
from aegis.drivers.base import HarnessDriver, HarnessSession
from aegis.hooks import SessionHandle
from aegis.hooks.decorator import _REGISTRY as _HOOK_REG
from aegis.hooks.runner import run_pre_spawn_hooks
from aegis.config.persona import read_persona
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

# Replaces claude's agentic system prompt for one-shot generation. It has
# to say "you have no tools" out loud: given the default prompt the model
# reaches for Read/Grep, finds nothing, and reports failure instead of
# answering from the window it was handed.
_ONESHOT_SYSTEM = (
    "You answer a side question about a conversation, using only the "
    "conversation window you are given. You have no tools and cannot look "
    "anything up. Answer directly and briefly. If the window does not "
    "contain what you would need, say so and set needs_more to true "
    "rather than guessing."
)

_EFFORT = {
    Effort.low: "low",
    Effort.medium: "medium",
    Effort.high: "high",
    Effort.max: "max",
}


class ClaudeSession(HarnessSession):
    # Claude in ``-p`` mode keeps the subprocess alive across turns and
    # can emit events without aegis having sent a prompt — e.g. when a
    # background Monitor or scheduled sub-task fires. AgentSession arms
    # an idle watcher for these.
    supports_idle_events = True

    def __init__(self, argv: list[str], cwd: str, *,
                 handle: str = "", harness: str = "claude-code",
                 agent_profile: str = "") -> None:
        self._argv = argv
        self._cwd = cwd
        self._handle = handle
        self._harness = harness
        self._agent_profile = agent_profile or handle
        self._proc: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._reader: asyncio.Task | None = None
        self._session_id: str | None = None
        self._control_seq: int = 0
        # One ParserState per session — remembers each tool_use.id →
        # kind so the matching tool_result can carry kind too.
        self._parser_state = ParserState()

    def has_pending_event(self) -> bool:
        return not self._queue.empty()

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def _latch_session_id(self, ev: Event) -> None:
        """Latch session_id on first SystemInit with a non-empty session_id."""
        from aegis.events import SystemInit
        if (self._session_id is None
                and isinstance(ev, SystemInit)
                and ev.session_id):
            self._session_id = ev.session_id

    async def start(self) -> None:
        argv, env = await self._apply_pre_spawn_hooks()
        kw: dict = dict(
            cwd=self._cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LIMIT,
        )
        if env is not None:
            kw["env"] = env
        self._proc = await asyncio.create_subprocess_exec(*argv, **kw)
        self._reader = asyncio.create_task(self._pump_stdout())

    async def _apply_pre_spawn_hooks(
        self,
    ) -> tuple[list[str], dict[str, str] | None]:
        """Run registered pre_spawn hooks against this session's argv/env.

        Returns the (possibly-rewritten) argv and env-dict to exec with.
        ``env`` is ``None`` when no hooks fired (preserves parent-env
        inheritance via ``create_subprocess_exec``'s default). Raises
        ``RuntimeError`` if any hook returns a ``block`` reason.
        """
        entries = _HOOK_REG.get("pre_spawn", [])
        if not entries:
            return list(self._argv), None
        composed = await run_pre_spawn_hooks(
            argv=tuple(self._argv),
            env=dict(os.environ),
            session=SessionHandle(
                handle=self._handle,
                agent_profile=self._agent_profile,
                harness=self._harness,
            ),
            cwd=self._cwd,
            entries=entries,
            state_dir=Path(self._cwd) / ".aegis" / "state",
        )
        if composed.block is not None:
            raise RuntimeError(
                f"pre_spawn hook blocked spawn: {composed.block}")
        return list(composed.argv or self._argv), composed.env

    async def _pump_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            async for raw in self._proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    ev = parse(line, state=self._parser_state)
                    self._latch_session_id(ev)
                    await self._queue.put(ev)
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

    async def interrupt(self) -> None:
        """Abort the in-flight turn without killing the subprocess.

        Sends a stream-json ``control_request/interrupt``; ``claude -p``
        honours it by aborting the current turn and emitting a terminal
        error result, keeping the process alive for the next send. Then
        drains those terminal events so they don't bleed into the next
        turn's ``events()`` loop. Bounded so a harness that never emits a
        result can't hang the caller.
        """
        proc = self._proc
        if not (proc and proc.stdin and proc.returncode is None):
            return
        self._control_seq += 1
        msg = {"type": "control_request",
               "request_id": f"aegis_interrupt_{self._control_seq}",
               "request": {"subtype": "interrupt"}}
        try:
            proc.stdin.write((json.dumps(msg) + "\n").encode())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            return
        # Drain the aborted turn's terminal events (the interrupt result,
        # any [Request interrupted] echo) so the next turn starts clean.
        for _ in range(64):
            try:
                ev = await asyncio.wait_for(self._queue.get(), timeout=5)
            except asyncio.TimeoutError:
                return
            if ev is None or isinstance(ev, Result):
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
    supports_resume = True
    supports_fork = True
    supports_oneshot = True

    def build_argv(self, agent: Agent, cwd: str,
                   mcp_url: str, handle: str) -> list[str]:
        argv = [
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
            "--append-system-prompt", PRIMING.format(handle=handle),
        ]
        # Persona composes AFTER the primer so the agent still knows its
        # handle and can call back.
        persona = read_persona(agent, cwd)
        if persona:
            argv += ["--append-system-prompt", persona]
        return argv

    def _oneshot_argv(self, agent: Agent, schema, instructions: list[str],
                      *, system: str = "") -> list[str]:
        """A `claude` invocation that generates rather than agents.

        Every flag here is load-shedding, and the two that matter were
        measured on zion 2026-07-31 (claude 2.1.220, haiku, same window
        and question):

            default `claude -p`            21.9s  $0.0633  53,593 in  refusal
            --system-prompt + --tools ""    8.5s  $0.0044   2,361 in  correct

        The default run does not merely cost more — it goes *agentic*,
        tries to read files that a side note has no business reading, and
        then answers "I cannot verify". ``--tools ""`` is what removes the
        tool schemas (the bulk of those 53k input tokens) and with them the
        urge to use them; ``--system-prompt`` replaces claude's agentic
        default rather than appending to it, which
        ``--append-system-prompt`` cannot do.
        """
        return [
            "claude", "-p", "\n\n".join(instructions),
            "--model", agent.model,
            "--output-format", "json",
            "--json-schema", json.dumps(schema.model_json_schema()),
            "--system-prompt", system or _ONESHOT_SYSTEM,
            "--tools", "",
            "--mcp-config", json.dumps({"mcpServers": {}}),
            "--strict-mcp-config",
        ]

    async def generate_detailed(self, agent: Agent, cwd: str, schema,
                                *instructions: str):
        from aegis.drivers.oneshot import Generation, parse_structured
        argv = self._oneshot_argv(agent, schema, list(instructions))
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd,
                # DEVNULL, not inherited: claude waits 3s for stdin it will
                # never get, and that wait is a third of the whole call.
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=_STREAM_LIMIT)
            out, _ = await proc.communicate()
        except Exception:                                     # noqa: BLE001
            return Generation()
        try:
            envelope = json.loads(out)
        except (ValueError, TypeError):
            return Generation()
        if not isinstance(envelope, dict):
            return Generation()
        return Generation(
            value=parse_structured(str(envelope.get("result") or ""), schema),
            model=agent.model,
            duration_ms=int(envelope.get("duration_ms") or 0),
            cost_usd=float(envelope.get("total_cost_usd") or 0.0),
        )

    def session(self, agent: Agent, cwd: str,
                mcp_url: str, handle: str) -> ClaudeSession:
        return ClaudeSession(
            self.build_argv(agent, cwd, mcp_url, handle), cwd,
            handle=handle, harness=agent.harness or "claude-code")

    def resume(self, agent: Agent, cwd: str,
               mcp_url: str, handle: str,
               session_id: str) -> ClaudeSession:
        """Build a ClaudeSession that resumes an existing conversation."""
        argv = self.build_argv(agent, cwd, mcp_url, handle)
        # Insert --resume <session_id> right after the "claude -p" prefix
        resumed_argv = argv[:2] + ["--resume", session_id] + argv[2:]
        return ClaudeSession(resumed_argv, cwd,
                             handle=handle,
                             harness=agent.harness or "claude-code")

    def fork(self, agent: Agent, cwd: str,
             mcp_url: str, handle: str,
             session_id: str) -> ClaudeSession:
        """Build a ClaudeSession branching from an existing conversation.

        `--fork-session` is what keeps this from being a plain resume:
        "when resuming, create a new session ID". The parent's own id
        stays where it was, so the two conversations never share a log.
        """
        argv = self.build_argv(agent, cwd, mcp_url, handle)
        forked_argv = (argv[:2] + ["--fork-session", "--resume", session_id]
                       + argv[2:])
        return ClaudeSession(forked_argv, cwd,
                             handle=handle,
                             harness=agent.harness or "claude-code")
