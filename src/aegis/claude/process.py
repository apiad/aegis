"""The ``claude -p`` child: spawn it, write to it, read it, end it.

stdout is read line by line and handed to ``on_line``; stderr goes to a file
and the last lines are kept for the exit report. A single stdout line can
carry a whole file (a large Read result), hence the generous line limit.

A control request that wants its answer goes through ``request``; the answer
is routed to it and never reaches ``on_line``, so it is not stored: it is
protocol, not transcript, and ``initialize``'s alone is about 60 KB.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from .. import launch


LINE_LIMIT = 64 * 1024 * 1024
STDERR_TAIL = 20
TERM_GRACE_S = 5.0
# A bad model took 4.7 s to be refused (#97).
CONTROL_TIMEOUT_S = 15.0


class ControlError(Exception):
    """claude answered a control request with an error."""


# aegis's permission vocabulary, mapped to Claude Code's --permission-mode.
PERMISSION_MODE = {
    "read": "plan",
    "write": "acceptEdits",
    "full": "bypassPermissions",
    "auto": "auto",
}


NO_MCP = '{"mcpServers":{}}'
# Claude Code's own tools that an aegis tool replaces, each with its
# replacement (#461). The primer already says to use aegis's, but every schema
# still ships on every request: measured on 2.1.296, these cost about 5.9k input
# tokens per session. A session with aegis's tools runs without them, unless its
# agent keeps some (``keep_harness_tools`` in .aegis.yaml). A name Claude Code
# does not know is ignored, so a tool renamed upstream costs tokens, not a launch.
# The init line lists Agent by its older name, Task; a deny of either removes it.
REPLACED_TOOLS = {
    "Agent": "queue_enqueue, session_spawn",
    "ListAgents": "session_list",
    "SendMessage": "peer_handoff",
    "Workflow": "workflow_arm",
    "ScheduleWakeup": "schedule_create",
    "CronCreate": "schedule_create",
    "CronDelete": "schedule_delete",
    "CronList": "schedule_list",
    "RemoteTrigger": "schedule_fire",
    "Monitor": "monitor_start",
    "TaskStop": "monitor_cancel",
    "PushNotification": "toast",
    "TodoWrite": "plan_update",
    "TaskCreate": "plan_update",
    "TaskGet": "plan_update",
    "TaskList": "plan_update",
    "TaskUpdate": "plan_update",
}
# Under the owner's ``knowledge: native`` (setup.py), Claude Code's own skill
# loader is off too, and aegis's knowledge tools replace it. Its auto-memory
# is turned off by the variable, which on 2.1.306 wins over every setting but
# a managed policy (it reads CLAUDE_CODE_DISABLE_AUTO_MEMORY before
# autoMemoryEnabled); the canary pins both (tests/canary).
NATIVE_TOOLS = {"Skill": "skill_read, skill_search"}
NO_AUTO_MEMORY = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
# aegis decides which of its own tools a session may call (ops.Registry.call),
# so Claude Code lets each one through in every mode. Measured on 2.1.291:
# plan mode refuses an MCP call even when --allowedTools or a settings rule
# allows it, acceptEdits and auto on Haiku ask, which -p answers no, and a
# PreToolUse hook's allow passes all three, while plan mode still refuses
# writes (#305). From 2.1.301 plan mode refuses the hook's allow too, unless
# the tool is annotated readOnlyHint, which every aegis tool is (mcp.py, #442).
ALLOW_AEGIS = json.dumps(
    {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "mcp__aegis__.*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "echo '"
                            + json.dumps(
                                {
                                    "hookSpecificOutput": {
                                        "hookEventName": "PreToolUse",
                                        "permissionDecision": "allow",
                                    }
                                }
                            )
                            + "'",
                        }
                    ],
                }
            ]
        }
    }
)


def build_argv(
    claude_bin: str,
    model: str,
    effort: str,
    permission: str,
    resume: str | None = None,
    mcp_config: str = NO_MCP,
    system_prompt: str | None = None,
    add_dirs: tuple[Path, ...] = (),
    disallowed: tuple[str, ...] = (),
) -> list[str]:
    argv = [
        claude_bin,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--replay-user-messages",
        # Text and thinking stream as deltas (stream.Parser), never stored.
        "--include-partial-messages",
        "--verbose",
        "--permission-mode",
        PERMISSION_MODE.get(permission, permission),
        "--strict-mcp-config",
        "--mcp-config",
        mcp_config,
        "--settings",
        ALLOW_AEGIS,
    ]
    for folder in add_dirs:
        # Read access outside the cwd under every permission mode: the inbox.
        argv += ["--add-dir", str(folder)]
    if disallowed:
        # One comma-joined value: the flag is variadic and would eat a
        # following positional.
        argv += ["--disallowedTools", ",".join(disallowed)]
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
        env: dict[str, str] | None = None,
        cgroup: Path | None = None,
        user: str | None = None,
        via: str | None = None,
    ) -> None:
        self.argv = argv
        self.user = user
        self.via = via
        self.cwd = cwd
        self.cgroup = cgroup
        self.stderr_path = stderr_path
        self._on_line = on_line
        self._on_exit = on_exit
        self.env = env
        self._proc: asyncio.subprocess.Process | None = None
        self._tasks: list[asyncio.Task] = []
        self._stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL)
        self._waiting: dict[str, asyncio.Future[dict]] = {}

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        """Raises FileNotFoundError when the binary is missing."""
        self.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self._proc = await launch.child(
            self.argv,
            cwd=str(self.cwd),
            env=self.env,
            stdin=launch.PIPE,
            stdout=launch.PIPE,
            stderr=launch.PIPE,
            limit=LINE_LIMIT,
            cgroup=self.cgroup,
            oom=True,
            user=self.user,
            via=self.via,
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
            if line.strip() and not self._answer(line):
                self._on_line(line)
        code = await proc.wait()
        for fut in self._waiting.values():
            if not fut.done():
                fut.set_exception(BrokenPipeError("claude exited"))
        await stderr_done
        self._on_exit(code, list(self._stderr_tail))

    async def _pump_stderr(self) -> None:
        proc = self._proc
        assert proc and proc.stderr
        async for raw in proc.stderr:
            self._stderr_tail.append(raw.decode("utf-8", "replace").rstrip())
            try:
                with self.stderr_path.open("ab") as f:
                    f.write(raw)
            except OSError:
                # The tail keeps it for the exit report. Raising would end the
                # drain, fill the pipe and block the child (#395).
                pass

    async def write(self, obj: dict) -> None:
        proc = self._proc
        if not (proc and proc.stdin) or proc.returncode is not None:
            raise BrokenPipeError("claude is not running")
        proc.stdin.write((json.dumps(obj) + "\n").encode())
        await proc.stdin.drain()

    async def request(
        self, subtype: str, timeout: float = CONTROL_TIMEOUT_S, **fields: object
    ) -> dict:
        """Send a control request and return its answer's body. Raises
        ControlError on an error answer, TimeoutError after ``timeout``, and
        BrokenPipeError when claude is not running or exits first."""
        rid = f"aegis_{subtype}_{time.monotonic_ns()}"
        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._waiting[rid] = fut
        try:
            await self.write(
                {
                    "type": "control_request",
                    "request_id": rid,
                    "request": {"subtype": subtype, **fields},
                }
            )
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._waiting.pop(rid, None)

    def _answer(self, line: str) -> bool:
        """Hand a control response to the request waiting for it; True when
        one was waiting."""
        if not self._waiting or '"control_response"' not in line[:40]:
            return False
        try:
            obj = json.loads(line)
        except ValueError:
            return False
        resp = obj.get("response") if isinstance(obj, dict) else None
        if not isinstance(resp, dict):
            return False
        fut = self._waiting.get(str(resp.get("request_id")))
        if fut is None:
            return False
        if not fut.done():
            if resp.get("subtype") == "error":
                fut.set_exception(ControlError(str(resp.get("error") or "error")))
            else:
                body = resp.get("response")
                fut.set_result(body if isinstance(body, dict) else {})
        return True

    async def terminate(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.returncode is None:
            await launch.signal_process_async(proc.pid, signal.SIGTERM, user=self.user)
            try:
                await asyncio.wait_for(proc.wait(), TERM_GRACE_S)
            except TimeoutError:
                await launch.signal_process_async(
                    proc.pid, signal.SIGKILL, user=self.user
                )
                await proc.wait()
        for t in self._tasks:
            if not t.done():
                try:
                    await asyncio.wait_for(t, 1)
                except (TimeoutError, asyncio.CancelledError):
                    t.cancel()
