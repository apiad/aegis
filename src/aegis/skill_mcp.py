"""The MCP servers skills bring, one per session that loaded the skill.

A skill whose SKILL.md declares ``aegis.mcp.command`` brings an MCP server.
When a session loads the skill, aegis starts that server for that session
alone and reaches its tools through one generic tool, ``skill_tool``, rather
than adding them to aegis's own tool list: aegis's endpoint is stateless (one
POST per call, no stream to push on), and of the harnesses only Claude Code
and OpenCode re-read a tool list after ``notifications/tools/list_changed``;
Codex 0.164 does so only behind a feature flag that is off by default. One
proxy tool works the same in every harness. ``skill_read`` returns the
server's tool list, so the agent knows what to call.

Each server is one process started through the launcher as the session's
Linux user, speaking MCP's stdio transport (one JSON-RPC message per line).
The store is out of a member's reach, so the server sends the skill's folder
as a tar, length first, on the process's stdin before the MCP stream starts:
a shell unpacks it into a fresh ``mktemp -d`` directory of the user's, runs
the command there and removes the directory when it ends. A server stops
when its session closes, and every one stops with aegis. One that died is
started again by the next call.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field

from . import launch

log = logging.getLogger("aegis.skill_mcp")

START_S = 30.0
STOP_GRACE_S = 2.0
STDERR_CHARS = 2000
# The longest line a server may send; a longer one, or any read error,
# fails what is pending and marks the server dead, so the next call starts
# it again.
READ_LIMIT = 16 * 2**20
# Read exactly the tar's bytes from fd 0, leaving the MCP stream behind them:
# os.read never reads ahead, where a buffered reader would.
UNPACK_PY = (
    "import io,os,sys,tarfile\n"
    "n,b=int(sys.argv[1]),b''\n"
    "while len(b)<n:\n"
    " c=os.read(0,n-len(b))\n"
    " if not c: sys.exit('the skill folder was cut short')\n"
    " b+=c\n"
    "t=tarfile.open(fileobj=io.BytesIO(b))\n"
    "t.extractall(sys.argv[2],**({'filter':'data'} if hasattr(tarfile,'data_filter') else {}))\n"
)
# The command runs in the unpacked folder; a TERM to the group lets the
# EXIT trap remove the folder.
SERVE_SH = (
    "d=$(mktemp -d) || exit 125; trap 'rm -rf -- \"$d\"' EXIT; "
    "trap 'exit 143' TERM; "
    'python3 -c "$0" "$1" "$d" || exit 125; shift; cd -- "$d" && "$@"'
)


async def _signal_group(pgid: int, sig: signal.Signals, user: str | None) -> None:
    """Signal group ``pgid``. In the multi-user mode a member's group runs as
    another Linux user, which the server may not signal itself, so a refused
    signal is sent again by ``kill`` started through the launcher as that
    user. Never raises: a failure is logged."""
    try:
        await launch.signal_process_async(pgid, sig, user=user, group=True)
    except Exception as e:  # noqa: BLE001 - a stop must finish
        log.warning(
            "skill mcp: could not %s group %s as %s: %s", sig.name, pgid, user, e
        )


async def stop_group(proc: asyncio.subprocess.Process, user: str | None) -> None:
    """TERM the process's group, so the shell's EXIT trap removes its folder,
    then KILL it if it is still there after ``STOP_GRACE_S``. Never raises."""
    for sig, wait in ((signal.SIGTERM, STOP_GRACE_S), (signal.SIGKILL, 5.0)):
        await _signal_group(proc.pid, sig, user)
        try:
            await asyncio.wait_for(proc.wait(), wait)
            return
        except TimeoutError:
            continue
    log.warning("skill mcp: group %s outlived SIGKILL", proc.pid)


class SkillToolError(Exception):
    """A server that cannot start or answer; the message says why."""


@dataclass
class Server:
    proc: asyncio.subprocess.Process
    user: str | None = None
    tools: list[dict] = field(default_factory=list)
    pending: dict[int, asyncio.Future] = field(default_factory=dict)
    stderr: str = ""
    next_id: int = 1
    reader: asyncio.Task | None = None
    errors: asyncio.Task | None = None
    dead: bool = False
    version: int = 0  # the skill's store version it started from

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None and not self.dead

    async def request(self, method: str, params: dict, timeout: float) -> dict:
        n = self.next_id
        self.next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[n] = fut
        try:
            self._write({"jsonrpc": "2.0", "id": n, "method": method, "params": params})
            msg = await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            raise SkillToolError(f"{method} got no answer in {timeout:g}s") from None
        finally:
            self.pending.pop(n, None)
        if "error" in msg:
            err = msg["error"]
            raise SkillToolError(f"{method}: {err.get('message', err)}")
        return msg.get("result") or {}

    def notify(self, method: str) -> None:
        self._write({"jsonrpc": "2.0", "method": method})

    def _write(self, msg: dict) -> None:
        if not self.alive or self.proc.stdin is None:
            raise SkillToolError(f"the server ended: {self.stderr[-500:]}")
        self.proc.stdin.write(json.dumps(msg).encode() + b"\n")

    async def read(self) -> None:
        """Hand each answer to its request; a line that is not JSON-RPC is
        logged and skipped. When the stream ends or cannot be read, the
        server is dead and every pending request fails."""
        assert self.proc.stdout is not None
        why = "the server ended"
        try:
            while line := await self.proc.stdout.readline():
                try:
                    msg = json.loads(line)
                except ValueError:
                    log.warning("skill mcp: a line that is not JSON: %r", line[:200])
                    continue
                if isinstance(msg, dict) and "method" in msg:
                    self._answer_server(msg)
                    continue
                fut = self.pending.get(msg.get("id")) if isinstance(msg, dict) else None
                if fut is not None and not fut.done():
                    fut.set_result(msg)
        except Exception as e:  # noqa: BLE001 - LimitOverrun, a reset pipe...
            why = f"its output could not be read ({e})"
            log.warning("skill mcp: %s", why)
        self.dead = True
        why = f"{why}: {self.stderr[-500:]}"
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(SkillToolError(why))

    def _answer_server(self, msg: dict) -> None:
        """A request or notification from the server, never an answer: a
        ping gets an empty result, any other request -32601, and a
        notification nothing."""
        if "id" not in msg:
            return
        if msg["method"] == "ping":
            reply: dict = {"result": {}}
        else:
            reply = {"error": {"code": -32601, "message": "aegis does not offer it"}}
        with contextlib.suppress(SkillToolError, OSError):
            self._write({"jsonrpc": "2.0", "id": msg["id"], **reply})

    async def drain_stderr(self) -> None:
        assert self.proc.stderr is not None
        while chunk := await self.proc.stderr.read(4096):
            self.stderr = (self.stderr + chunk.decode("utf-8", "replace"))[
                -STDERR_CHARS:
            ]

    async def stop(self) -> None:
        if self.proc.returncode is None:
            await stop_group(self.proc, self.user)
        for t in (self.reader, self.errors):
            if t is not None:
                t.cancel()


class SkillServers:
    """Each session's running skill servers, by (log id, skill name), and its
    running ``skill_run`` scripts, which stop with it too."""

    def __init__(self) -> None:
        self._servers: dict[tuple[str, str], Server] = {}
        self._starting: dict[tuple[str, str], asyncio.Lock] = {}
        self._runs: dict[
            str, dict[int, tuple[asyncio.subprocess.Process, str | None]]
        ] = {}
        # Moves each time a session's processes are stopped, so a start that
        # began before the stop stops what it made instead of keeping it.
        self._closed: dict[str, int] = {}

    def runs(self, log_id: str) -> list[int]:
        """The process groups of the session's running scripts."""
        return sorted(self._runs.get(log_id, {}))

    @contextlib.contextmanager
    def tracking(
        self, log_id: str, proc: asyncio.subprocess.Process, user: str | None
    ) -> Iterator[None]:
        """Hold a script's process while it runs, so the session's close and
        aegis's shutdown stop it."""
        self._runs.setdefault(log_id, {})[proc.pid] = (proc, user)
        try:
            yield
        finally:
            held = self._runs.get(log_id, {})
            held.pop(proc.pid, None)
            if not held:
                self._runs.pop(log_id, None)

    def running(self, log_id: str) -> list[str]:
        return sorted(
            k[1] for k, s in self._servers.items() if k[0] == log_id and s.alive
        )

    def current(self, log_id: str, skill: str, version: int) -> Server | None:
        """The session's server for ``skill`` when it runs on ``version``."""
        held = self._servers.get((log_id, skill))
        if held is not None and held.alive and held.version == version:
            return held
        return None

    async def ensure(
        self,
        log_id: str,
        skill: str,
        command: list[str],
        tar: Callable[[], Awaitable[bytes]],
        user: str | None,
        version: int,
    ) -> Server:
        """The session's server for ``skill``, started if it is not running
        or the skill changed since it started (``version``, the skill's
        store version). ``tar`` packs the skill's folder, only for a start.
        A start the session's close overtook stops what it made."""
        key = (log_id, skill)
        closed = self._closed.get(log_id, 0)
        async with self._starting.setdefault(key, asyncio.Lock()):
            held = self._servers.get(key)
            if held is not None and held.alive and held.version == version:
                return held
            if held is not None:
                self._servers.pop(key, None)
                await held.stop()
            server = await self._start(command, await tar(), user)
            server.version = version
            if self._closed.get(log_id, 0) != closed:
                await server.stop()
                raise SkillToolError("the session is closing")
            self._servers[key] = server
            return server

    async def _start(self, command: list[str], tar: bytes, user: str | None) -> Server:
        try:
            proc = await launch.child(
                ["bash", "-c", SERVE_SH, UNPACK_PY, str(len(tar)), *command],
                user=user,
                via=launch.resolve_via(),
                cwd="/",
                stdin=launch.PIPE,
                stdout=launch.PIPE,
                stderr=launch.PIPE,
                new_session=True,
                limit=READ_LIMIT,
            )
        except OSError as e:
            raise SkillToolError(f"the server could not start: {e}") from e
        s = Server(proc, user)
        s.reader = asyncio.create_task(s.read())
        s.errors = asyncio.create_task(s.drain_stderr())
        try:
            assert proc.stdin is not None
            proc.stdin.write(tar)
            await s.request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "aegis", "version": "1"},
                },
                START_S,
            )
            s.notify("notifications/initialized")
            listed = await s.request("tools/list", {}, START_S)
        except (SkillToolError, OSError) as e:
            await s.stop()
            raise SkillToolError(f"{e}; its stderr: {s.stderr[-500:]}") from e
        s.tools = [
            {
                "name": t.get("name"),
                "description": t.get("description") or "",
                "input_schema": t.get("inputSchema") or {},
            }
            for t in listed.get("tools") or []
            if isinstance(t, dict)
        ]
        return s

    async def call(
        self, log_id: str, skill: str, tool: str, args: dict, timeout: float
    ) -> dict:
        s = self._servers.get((log_id, skill))
        if s is None or not s.alive:
            raise SkillToolError("the server is not running")
        return await s.request("tools/call", {"name": tool, "arguments": args}, timeout)

    async def stop_session(self, log_id: str) -> None:
        self._closed[log_id] = self._closed.get(log_id, 0) + 1
        for key in [k for k in self._servers if k[0] == log_id]:
            await self._servers.pop(key).stop()
            self._starting.pop(key, None)
        for proc, user in list(self._runs.get(log_id, {}).values()):
            await stop_group(proc, user)

    async def shutdown(self) -> None:
        for log_id in {k[0] for k in self._servers} | set(self._runs):
            await self.stop_session(log_id)
