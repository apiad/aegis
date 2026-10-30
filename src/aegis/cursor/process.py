"""One ``cursor-agent acp`` child per session, driven over ACP on stdio.

Measured on cursor-agent 2026.10.14 (spec
``2026-10-19-aegis-2-cursor-harness-design.md``; fixture
``tests/fixtures/cursor/acp-turn.jsonl``):

- ``initialize`` answers in about eight seconds, and its answer carries no
  version and no token counts, so the transcript's first line has the label
  and the model alone and a card for this harness shows neither cost nor
  context.
- ``session/prompt`` answers only when the whole turn ends, so a send starts
  it as a task and returns. A second prompt mid-turn would cancel the one
  running (its ``stopReason`` is ``cancelled``), so prompts typed during a
  turn wait in ``_queued`` and are read one after another.
- Cursor asks before a command outside its allowlist
  (``session/request_permission``, measured: ``Not in allowlist: cat``); a
  request nobody answers leaves the tool pending forever, so aegis answers
  at once, by level (config.allows), and never picks ``allow_always``, which
  would outlive the session in Cursor's own config.
- Model and permission are live RPCs (``session/set_model``,
  ``session/set_mode``); nothing needs a restart.
- There is no cancel: ``session/cancel`` is method-not-found and a
  ``session/load`` mid-turn lets the turn run on (measured). An interrupt
  therefore closes the turn with its own ``aegis/turn_done`` line and kills
  the child quietly: its exit is not reported, since a red "exited" row
  after a deliberate stop would read as a crash, and the next send starts a
  fresh child that ``session/load``s the conversation.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from collections import deque
from pathlib import Path
from typing import Any

from .. import launch
from ..claude.control import Catalog
from ..claude.process import ControlError
from ..harness import Launch, session_env
from .config import (
    argv,
    allows,
    catalog_from,
    command_list,
    effort_of,
    mcp_servers,
    mode_for,
    models_from,
)

# initialize measured at about 8 s; a first start on a cold network may drag.
START_S = 60.0
# Answers to everything but a turn: session/new, set_model, set_mode.
REQUEST_S = 30.0
TERM_GRACE_S = 5.0
STDERR_TAIL = 64
# A diff or a whole command output arrives on one JSON-RPC line, far past
# asyncio's 64 KB ``readline`` default.
LINE_LIMIT = 64 * 1024 * 1024
# How often an exit is looked for (see codex/process.py): asyncio resolves a
# Process only once every pipe has closed.
EXIT_POLL_S = 0.1


class RpcError(Exception):
    """Cursor answered a request with an error."""


class Exited(ConnectionResetError):
    """The child exited while a request waited for its answer. Its exit is
    reported through ``on_exit``, so a send it cut does not raise as well."""


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _aegis_version() -> str:
    try:
        from importlib.metadata import version

        return version("aegis-harness")
    except Exception:  # noqa: BLE001 — an unknown version must not block a start
        return "0"


async def _exited(proc: asyncio.subprocess.Process) -> int:
    while proc.returncode is None:
        await asyncio.sleep(EXIT_POLL_S)
    return proc.returncode or 0


class CursorProcess:
    def __init__(self, bin: str, launch: Launch) -> None:
        self._bin = bin
        self._launch = launch
        self.model, self.effort, self.permission = (
            launch.model,
            launch.effort,
            launch.permission,
        )
        self._session: str | None = None
        # A resume re-announces the conversation before ``session/load``
        # answers (measured: one user, thought and message chunk, then the
        # answer). The store already holds those lines, so they are caught in
        # _deliver and not folded again.
        self._replaying = False
        # The ACP ids the session reports, which set_model and set_mode take.
        self._current_model: str | None = None
        self._mode: str | None = None
        self._models: list = []
        self._commands: list[dict] = []
        self._proc: asyncio.subprocess.Process | None = None
        self._next = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()
        self._wait_task: asyncio.Task | None = None
        self._tail: deque[str] = deque(maxlen=STDERR_TAIL)
        self._catalog: Catalog | None = None
        self._busy = False
        self._queued: deque[str] = deque()
        self._turn_task: asyncio.Task | None = None
        self._quiet = False
        # terminate() was called: nothing may start a child after it.
        self._closed = False

    # -- what the session reads --------------------------------------
    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def session_id(self) -> str | None:
        return self._session

    # -- lifecycle ----------------------------------------------------
    async def start(self) -> None:
        await self._spawn()
        try:
            await self._open(self._launch.resume_id)
        except (RpcError, OSError, TimeoutError, KeyError, TypeError) as e:
            await self._end_child()
            raise ConnectionError(
                f"cursor-agent acp did not open a session: {e}"
            ) from e

    async def _spawn(self) -> None:
        self._quiet = False
        self._launch.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self._proc = await launch.child(
            argv(self._bin),
            cwd=self._launch.cwd,
            # The whole environment, not just AEGIS_*: cursor-agent needs the
            # person's HOME and PATH to find its login and its tools.
            env=session_env(self._launch),
            stdin=launch.PIPE,
            stdout=launch.PIPE,
            stderr=launch.PIPE,
            limit=LINE_LIMIT,
            new_session=True,
            cgroup=self._launch.cgroup,
            oom=True,
            user=self._launch.user,
            via=self._launch.via,
        )
        self._keep(self._drain(self._proc.stderr))
        self._keep(self._read())
        self._wait_task = asyncio.create_task(self._wait())
        try:
            # ``terminal`` must be a boolean (measured: an object is refused),
            # and a false one means Cursor does its own file and shell work
            # and asks us for nothing but permission.
            await self._call(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {"terminal": False},
                    "clientInfo": {"name": "aegis", "version": _aegis_version()},
                },
                START_S,
            )
        except (RpcError, OSError, TimeoutError) as e:
            await self._end_child()
            tail = "; ".join(self._tail) or "no output"
            raise ConnectionError(f"cursor-agent acp did not start: {e}: {tail}") from e
        self._write({"method": "notifications/initialized"})

    async def _open(self, resume: str | None) -> None:
        """The session, resumed or new. Both doors carry the aegis MCP server
        (measured: ``session/load`` requires ``mcpServers`` as ``session/new``
        does), and an answered error on load falls back to a new session."""
        params: dict[str, Any] = {
            "cwd": str(self._launch.cwd),
            "mcpServers": mcp_servers(self._launch.mcp),
        }
        result: dict | None = None
        if resume:
            self._replaying = True
            try:
                result = await self._call(
                    "session/load", {**params, "sessionId": resume}, START_S
                )
            except RpcError:
                result = None  # Cursor knows no such session; open a fresh one
            finally:
                self._replaying = False
        if result is None:
            result = await self._call("session/new", params, START_S)
            self._session = str(result.get("sessionId") or "") or None
            if self._session is None:
                raise RpcError(
                    "session/new answered no sessionId: " + json.dumps(result)[:600]
                )
        else:
            # ``session/load`` answers no sessionId (measured: the same
            # keys as session/new minus it) — the conversation keeps the id
            # it was asked for.
            self._session = resume
        models = _dict(result.get("models"))
        self._current_model = str(models.get("currentModelId") or "") or None
        self._mode = str(_dict(result.get("modes")).get("currentModeId") or "") or None
        available = models.get("availableModels")
        self._models = available if isinstance(available, list) else []
        self._try_catalog()
        want = mode_for(self.permission)
        if want != self._mode:
            await self._call(
                "session/set_mode",
                {"sessionId": self._session, "modeId": want},
            )
            self._mode = want
        if self.model:
            found = self._catalog.model(self.model) if self._catalog else None
            model_id = found.resolved if found else self.model
            if model_id != self._current_model:
                await self._call(
                    "session/set_model",
                    {"sessionId": self._session, "modelId": model_id},
                )
                self._current_model = model_id
        self._emit(
            "aegis/session",
            {
                "session_id": self._session,
                "model": self._display(),
                "cwd": str(self._launch.cwd),
            },
        )

    def _keep(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    def _note(self, line: str) -> None:
        if not line:
            return
        self._tail.append(line)
        try:
            with self._launch.stderr_path.open("a") as f:
                f.write(line + "\n")
        except OSError:
            pass  # The tail keeps it; raising would end the drain (#395).

    async def _drain(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while raw := await stream.readline():
            self._note(raw.decode(errors="replace").rstrip())

    async def _wait(self) -> None:
        assert self._proc is not None
        proc = self._proc
        code = await _exited(proc)
        await self._signal(proc, signal.SIGKILL)  # whatever it left kept the pipes
        self._fail_pending(Exited("cursor-agent acp exited"))
        if self._quiet:
            return
        self._quiet = True
        await asyncio.sleep(0.05)  # let the stderr drain catch up
        self._launch.on_exit(code, list(self._tail))

    async def terminate(self) -> None:
        self._closed = True
        self._queued.clear()
        await self._end_child()

    async def _end_child(self) -> None:
        self._quiet = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            if proc.stdin is not None:
                proc.stdin.close()  # acp ends when its stdin ends
            try:
                await asyncio.wait_for(_exited(proc), TERM_GRACE_S)
            except TimeoutError:
                await self._signal(proc, signal.SIGTERM)
                try:
                    await asyncio.wait_for(_exited(proc), TERM_GRACE_S)
                except TimeoutError:
                    await self._signal(proc, signal.SIGKILL)
                    await _exited(proc)
        if proc is not None:
            await self._signal(proc, signal.SIGKILL)
        for t in list(self._tasks):
            t.cancel()
        if self._wait_task is not None:
            self._wait_task.cancel()
        self._fail_pending(ConnectionResetError("cursor-agent acp was stopped"))

    async def _signal(self, proc: asyncio.subprocess.Process, sig: int) -> None:
        await launch.signal_process_async(
            proc.pid, sig, user=self._launch.user, group=True
        )

    def _fail_pending(self, err: Exception) -> None:
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(err)

    # -- the wire -----------------------------------------------------
    async def _read(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stdout = self._proc.stdout
        while raw := await stdout.readline():
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            try:
                msg: Any = json.loads(line)
            except ValueError:
                self._note(f"not JSON on stdout: {line[:200]}")
                continue
            if not isinstance(msg, dict):
                continue
            if "id" in msg and "method" in msg:
                if msg.get("method") == "session/request_permission":
                    self._permission(msg)
                else:
                    self._refuse(msg)
            elif "id" in msg:
                fut = self._pending.pop(msg["id"], None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
            else:
                self._deliver(
                    str(msg.get("method") or ""), _dict(msg.get("params")), line
                )

    def _permission(self, msg: dict) -> None:
        """Answer Cursor's one kind of request. Its options name the
        decisions (``allow_once``, ``allow_always``, ``reject_once``,
        ``reject_always``); aegis picks the once-only kind by level."""
        p = _dict(msg.get("params"))
        call = _dict(p.get("toolCall"))
        kind = str(call.get("kind") or "")
        allow = allows(self.permission, kind)
        want = "allow_once" if allow else "reject_once"
        options = p.get("options")
        if not isinstance(options, list):
            options = []
        pick = next(
            (
                str(o["optionId"])
                for o in options
                if isinstance(o, dict) and o.get("kind") == want
            ),
            None,
        )
        self._note(f"permission {want if pick else 'cancelled'}: {call.get('title')}")
        outcome = (
            {"outcome": "selected", "optionId": pick}
            if pick
            else {"outcome": "cancelled"}
        )
        self._write({"id": msg["id"], "result": {"outcome": outcome}})

    def _refuse(self, msg: dict) -> None:
        """aegis answers no other request: an unanswered one would hang the
        turn, and Cursor does its own file and shell work (measured: with
        ``terminal: false`` it asks for nothing but permission)."""
        self._note(f"refused a request from cursor: {msg.get('method')}")
        self._write(
            {
                "id": msg["id"],
                "error": {"code": -32601, "message": "aegis answers no requests"},
            }
        )

    def _deliver(self, method: str, p: dict, line: str) -> None:
        if method == "session/update":
            update = _dict(p.get("update"))
            if update.get("sessionUpdate") == "available_commands_update":
                self._commands = command_list(update.get("availableCommands") or [])
                self._catalog = None  # new commands the person can call
            if self._replaying:
                return  # a resume replays history; the store already has it
        self._launch.on_line(line)

    def _emit(self, method: str, params: dict) -> None:
        self._launch.on_line(json.dumps({"method": method, "params": params}))

    def _write(self, obj: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise ConnectionResetError("cursor-agent acp is not running")
        try:
            self._proc.stdin.write(
                json.dumps({"jsonrpc": "2.0", **obj}).encode() + b"\n"
            )
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as e:
            raise ConnectionResetError(str(e)) from e

    def _begin(self, method: str, params: dict) -> tuple[int, asyncio.Future]:
        """A request written to the child: its id and its future. Registered
        before the write, popped if the write fails, so a prompt that never
        left leaves no future behind."""
        if not self.running:
            raise ConnectionResetError("cursor-agent acp is not running")
        self._next += 1
        rid = self._next
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            self._write({"id": rid, "method": method, "params": params})
        except BaseException:
            self._pending.pop(rid, None)
            raise
        return rid, fut

    async def _await(
        self, rid: int, fut: asyncio.Future, timeout: float | None
    ) -> dict:
        try:
            msg = await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)
        if "error" in msg:
            err = msg["error"]
            data = _dict(err).get("data")
            detail = _dict(data).get("message") if isinstance(data, dict) else None
            raise RpcError(str(detail or _dict(err).get("message") or err))
        result = msg.get("result")
        return result if isinstance(result, dict) else {}

    async def _call(
        self, method: str, params: dict, timeout: float | None = REQUEST_S
    ) -> dict:
        rid, fut = self._begin(method, params)
        return await self._await(rid, fut, timeout)

    # -- turns ---------------------------------------------------------
    async def send(self, text: str) -> None:
        if self._session is None:
            raise ConnectionResetError("cursor-agent acp has no session")
        if self._busy:
            # A second prompt cancels the one running (measured), so a
            # message typed mid-turn waits here and is read when it ends.
            self._queued.append(text)
            return
        self._busy = True
        self._turn_task = self._keep(self._loop(text))

    async def _loop(self, text: str) -> None:
        try:
            while True:
                if not self.running:
                    break  # the child died: its exit record owns the session now
                await self._one_turn(text)
                if not self._queued or self._closed:
                    break
                text = self._queued.popleft()
        finally:
            self._busy = False

    async def _one_turn(self, text: str) -> None:
        started = time.monotonic()
        # The request is written before the echo: a prompt that never left
        # (a child that just died) must not be read as a running turn.
        rid, fut = self._begin(
            "session/prompt",
            {
                "sessionId": self._session,
                "prompt": [{"type": "text", "text": text}],
            },
        )
        self._emit("aegis/echo", {"text": text})
        try:
            msg = await self._await(rid, fut, None)  # it answers at the end
        except (Exited, ConnectionResetError):
            return  # the child died: its exit record closes the turn
        except RpcError as e:
            self._note(f"the prompt was refused: {e}")
            stop = "error"
        else:
            stop = str(_dict(msg.get("result")).get("stopReason") or "end_turn")
        self._emit(
            "aegis/turn_done",
            {
                "stop_reason": stop,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )

    async def interrupt(self) -> None:
        """The person stopped the turn. There is no cancel to send (module
        docstring), so the turn ends here and the child is killed without
        reporting the exit; the next send starts a child that loads the
        conversation."""
        if not self._busy:
            return
        self._emit("aegis/turn_done", {"stop_reason": "interrupted"})
        self._queued.clear()
        if self._turn_task is not None:
            self._turn_task.cancel()
        await self._end_child()

    # -- settings ------------------------------------------------------
    async def set(self, kind: str, value: str) -> None:
        """Model and permission are live RPCs; effort is not a knob of its
        own (config.py), so it confirms what the model carries or refuses."""
        if kind == "model":
            await self._set_model(value)
        elif kind == "effort":
            own = effort_of(self._current_model or "")
            if value and value == own:
                self.effort = value
                return
            raise ControlError(
                "cursor carries the effort inside its model id"
                " and refuses the same id rewritten (measured:"
                " `-32602 Invalid model value`), so pick a model with it"
            )
        elif kind == "permission":
            await self._set_permission(value)

    async def _set_model(self, value: str) -> None:
        model = (await self.catalog()).model(value)
        model_id = model.resolved if model else value
        if model_id == self._current_model:
            self.model = value
            return
        if self._session is None:
            raise ConnectionResetError("cursor-agent acp has no session")
        try:
            await self._call(
                "session/set_model",
                {"sessionId": self._session, "modelId": model_id},
            )
        except RpcError as e:
            raise ControlError(str(e)) from e
        self.model = value
        self._current_model = model_id
        self._emit("aegis/turn", {"model": self._display()})

    async def _set_permission(self, value: str) -> None:
        if value == self.permission:
            return
        mode = mode_for(value)
        before = self.permission
        self.permission = value
        try:
            if self._session is not None and mode != self._mode:
                await self._call(
                    "session/set_mode",
                    {"sessionId": self._session, "modeId": mode},
                )
                self._mode = mode
        except BaseException:
            self.permission = before
            raise

    def _display(self) -> str:
        """The model name the transcript and the card show: what the person
        configured, else the catalog name of what Cursor runs."""
        if self.model:
            return self.model
        if self._catalog is not None and self._current_model:
            found = next(
                (m for m in self._catalog.models if m.resolved == self._current_model),
                None,
            )
            return found.value if found else self._current_model
        return self._current_model or ""

    # -- catalog --------------------------------------------------------
    def _try_catalog(self) -> None:
        if self._catalog is None and self._models:
            self._catalog = catalog_from(
                list(models_from(self._models, self._current_model or "")),
                self._commands,
            )

    async def catalog(self) -> Catalog:
        self._try_catalog()
        if self._catalog is None:
            raise ConnectionError("cursor-agent acp gave no catalog")
        return self._catalog


async def probe(bin: str, cwd: Path, stderr_path: Path, model: str = "") -> Catalog:
    """A catalog for a cwd with no live process: start ``cursor-agent acp``,
    open a throwaway session (its ``session/new`` answer carries the models),
    end it. About ten seconds, since ``initialize`` alone is about eight, and
    no tokens: no prompt is ever sent. ``cursor-agent models`` is not used on
    purpose: its 265 ids are not the ones ``session/set_model`` accepts
    (measured: a plain name or a CLI id is `-32602 Invalid model value`)."""
    p = CursorProcess(
        bin,
        Launch(
            cwd=cwd, model=model, effort="", permission="read", resume_id=None,
            mcp=None, system_prompt=None, stderr_path=stderr_path,
            on_line=lambda line: None, on_exit=lambda code, tail: None,
        ),
    )  # fmt: skip
    await p._spawn()
    try:
        await p._open(None)
        return await p.catalog()
    except (RpcError, OSError, TimeoutError) as e:
        raise ConnectionError(f"cursor-agent acp gave no catalog: {e}") from e
    finally:
        await p.terminate()
