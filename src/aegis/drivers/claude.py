from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

from aegis.config import Agent, Effort, Permission
from aegis.events import (
    AgentPlan, AssistantText, AssistantThinking, Event, ParserState, Result,
    ToolResult, ToolUse, parse,
)
from aegis.drivers.base import HarnessDriver, HarnessSession
from aegis.hooks import SessionHandle
from aegis.hooks.decorator import _REGISTRY as _HOOK_REG
from aegis.hooks.runner import run_pre_spawn_hooks
from aegis.config.persona import read_persona
from aegis.hosts.launcher import LOCAL, Launcher
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

# Events that only ever occur inside a turn, and so promise that a
# terminal ``Result`` is coming. Anything else claude puts on the wire
# between turns — ``system`` notices like ``commands_changed`` (skills
# reloaded), ``init``, ``hook_started``, ``task_updated`` — is out-of-band
# metadata that no ``Result`` ever follows. Promoting one of those to a
# turn parks the session on a read that never returns; see
# ``tests/test_claude_idle_promotion.py``.
#
# Telemetry that merely accompanies a turn (ThinkingTokens, CompactBoundary,
# ContextUpdate) is deliberately absent: it cannot start a turn on its own,
# and the real turn's events arrive right behind it.
_TURN_BEARING = (
    AssistantText, AssistantThinking, ToolUse, ToolResult, AgentPlan, Result,
)


def _promises_result(ev: Event | None) -> bool:
    """Whether this queue entry means a turn is running (or the stream died).

    ``None`` is the end-of-stream sentinel: a dead harness must still wake
    the session so it can settle into ``error`` rather than idle quietly on
    a subprocess that no longer exists.
    """
    return ev is None or isinstance(ev, _TURN_BEARING)


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
                 agent_profile: str = "",
                 launcher: Launcher = LOCAL) -> None:
        self._argv = argv
        self._cwd = cwd
        self._handle = handle
        self._harness = harness
        self._agent_profile = agent_profile or handle
        self._launcher = launcher
        self._proc: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        # How many queued entries promise a Result. Maintained at both ends
        # of the queue so ``has_pending_event`` can stay a pure predicate.
        self._pending_turn = 0
        self._reader: asyncio.Task | None = None
        self._session_id: str | None = None
        self._control_seq: int = 0
        # One ParserState per session — remembers each tool_use.id →
        # kind so the matching tool_result can carry kind too.
        self._parser_state = ParserState()

    def has_pending_event(self) -> bool:
        """Whether a *turn* is waiting to be drained.

        Not the same question as "is the queue non-empty". Out-of-band
        system notices sit in the queue too, and they never end in a
        ``Result`` — waking a turn for one strands the session.
        """
        return self._pending_turn > 0

    def _put(self, ev: Event | None) -> None:
        # put_nowait, not await put: the queue is unbounded, so this cannot
        # block, and staying synchronous keeps the counter and the queue in
        # lockstep with no await point between them to be cancelled at.
        if _promises_result(ev):
            self._pending_turn += 1
        self._queue.put_nowait(ev)

    async def _take(self) -> Event | None:
        ev = await self._queue.get()
        if _promises_result(ev):
            self._pending_turn -= 1
        return ev

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
        # Pre-spawn hooks run against the INNER argv, before any transport
        # wrapping — a hook that rewrites claude's flags has no business
        # knowing whether this session runs here or over ssh.
        argv, env = await self._apply_pre_spawn_hooks()
        self._proc = await self._launcher.spawn(argv, cwd=self._cwd, env=env)
        # This session reads stdout only, so the transport is free to drain
        # stderr — which is both how a dropped link gets diagnosed and what
        # stops a chatty remote from filling the pipe and blocking.
        watch = getattr(self._launcher, "watch_stderr", None)
        if watch is not None:
            watch()
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
                    self._put(ev)
        except Exception:
            # A stream/parse failure must not silently kill the turn: the
            # finally below still delivers the sentinel so events() ends
            # the turn instead of deadlocking on an empty queue forever.
            pass
        finally:
            # A dropped SSH link looks exactly like a clean harness exit
            # from here — stdout just ends. Ask the transport which it was,
            # so the pane can say "link to vps lost" instead of going
            # quietly idle on a session that no longer exists.
            #
            # No new event type: AssistantText puts the diagnostic where a
            # reader will see it, and Result(is_error=True) is the terminal
            # event AgentSession already keys its error state off — so the
            # tab turns red through the path that already exists.
            failure = getattr(self._launcher, "link_failure", lambda: None)()
            if failure is not None:
                self._put(AssistantText(text=str(failure)))
                self._put(Result(duration_ms=None, is_error=True,
                                 stop_reason="link_lost"))
            self._put(None)  # always signal stream end

    async def send(self, text: str) -> None:
        assert self._proc and self._proc.stdin
        msg = {"type": "user",
               "message": {"role": "user", "content": text}}
        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self._proc.stdin.drain()

    async def events(self) -> AsyncIterator[Event]:
        """Yield events until this turn's Result (or stream close)."""
        while True:
            ev = await self._take()
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
                ev = await asyncio.wait_for(self._take(), timeout=5)
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
                   mcp_url: str, handle: str,
                   launcher: Launcher = LOCAL,
                   token: str = "") -> list[str]:
        argv = [
            "claude", "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--replay-user-messages",
            "--verbose",  # required by claude with -p + stream-json output
            "--model", agent.model,
            "--effort", _EFFORT[agent.effort],
            "--permission-mode", _PERMISSION_MODE[agent.permission],
            "--mcp-config", mcp_config_json(mcp_url, token),
            "--strict-mcp-config",
            "--append-system-prompt", PRIMING.format(handle=handle),
        ]
        # Persona composes AFTER the primer so the agent still knows its
        # handle and can call back. It is read from the LOCAL project even
        # when the harness runs remotely — a persona file lives here.
        persona = read_persona(agent, launcher.persona_root(cwd))
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
                mcp_url: str, handle: str,
                launcher: Launcher = LOCAL,
                token: str = "") -> ClaudeSession:
        return ClaudeSession(
            self.build_argv(agent, cwd, mcp_url, handle, launcher, token),
            cwd,
            handle=handle, harness=agent.harness or "claude-code",
            launcher=launcher)

    def resume(self, agent: Agent, cwd: str,
               mcp_url: str, handle: str,
               session_id: str,
               launcher: Launcher = LOCAL,
               token: str = "") -> ClaudeSession:
        """Build a ClaudeSession that resumes an existing conversation."""
        argv = self.build_argv(agent, cwd, mcp_url, handle, launcher, token)
        # Insert --resume <session_id> right after the "claude -p" prefix
        resumed_argv = argv[:2] + ["--resume", session_id] + argv[2:]
        return ClaudeSession(resumed_argv, cwd,
                             handle=handle,
                             harness=agent.harness or "claude-code",
                             launcher=launcher)

    def fork(self, agent: Agent, cwd: str,
             mcp_url: str, handle: str,
             session_id: str,
             launcher: Launcher = LOCAL,
             token: str = "") -> ClaudeSession:
        """Build a ClaudeSession branching from an existing conversation.

        `--fork-session` is what keeps this from being a plain resume:
        "when resuming, create a new session ID". The parent's own id
        stays where it was, so the two conversations never share a log.
        """
        argv = self.build_argv(agent, cwd, mcp_url, handle, launcher, token)
        forked_argv = (argv[:2] + ["--fork-session", "--resume", session_id]
                       + argv[2:])
        return ClaudeSession(forked_argv, cwd,
                             handle=handle,
                             harness=agent.harness or "claude-code",
                             launcher=launcher)
