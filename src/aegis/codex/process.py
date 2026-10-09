"""One ``codex app-server`` child per session, driven over JSON-RPC on stdio.

Measured on codex-cli 0.162.1 (spec ``2026-10-09-aegis-2-codex-harness-design.md``):

- Model, effort and sandbox travel with every ``turn/start``, so a change
  needs no restart; only a new provider does, because a thread names its
  provider when it opens.
- A prompt sent mid-turn is ``turn/steer``, read at the next model request.
  It names the running turn, and Codex refuses it once that turn has ended,
  so a refused steer starts a new turn instead.
- The child holds a writer lease on its thread. Closing stdin ends it
  cleanly; a child that died left a grandchild (the plugin clone) holding the
  lease, so after any exit the whole process group is killed.
- An ``item/completed`` carries a command's whole output on one line, far
  past asyncio's 64 KB ``readline`` default.
- No line names the Codex version or the model of a turn, so the process
  writes ``aegis/*`` lines for them through ``on_line`` (``stream.OWN``).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import tomllib
from collections import deque
from pathlib import Path
from typing import Any

import httpx

from ..claude.control import Catalog
from ..harness import Launch
from .config import (
    SANDBOX_MODE,
    SANDBOX_POLICY,
    argv,
    catalog_from,
    child_env,
    provider_model,
    split_model,
)
from .stream import DELTAS, STORED

START_S = 15.0
REQUEST_S = 30.0
TERM_GRACE_S = 5.0
STDERR_TAIL = 20
LINE_LIMIT = 64 * 1024 * 1024
LOST = "no rollout found"
# How often an exit is looked for. Not ``Process.wait()``: asyncio resolves it
# only once every pipe has closed, and a grandchild that inherited the pipes
# (the plugin clone) holds them open after the child is gone.
EXIT_POLL_S = 0.1


class RpcError(Exception):
    """app-server answered a request with an error."""


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


async def _exited(proc: asyncio.subprocess.Process) -> int:
    """The child's exit code, once it has exited (see ``EXIT_POLL_S``)."""
    while proc.returncode is None:
        await asyncio.sleep(EXIT_POLL_S)
    return proc.returncode


def _aegis_version() -> str:
    try:
        from importlib.metadata import version

        return version("aegis-harness")
    except Exception:  # noqa: BLE001 — an unknown version must not block a start
        return "0"


class CodexProcess:
    def __init__(self, bin: str, launch: Launch) -> None:
        self._bin = bin
        self._launch = launch
        self.model, self.effort, self.permission = (
            launch.model,
            launch.effort,
            launch.permission,
        )
        self._provider = split_model(launch.model)[0]
        self._thread: str | None = None
        self._turn: str | None = None
        self._ended: deque[str] = deque(maxlen=16)
        self._proc: asyncio.subprocess.Process | None = None
        self._next = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()
        self._wait_task: asyncio.Task | None = None
        self._tail: deque[str] = deque(maxlen=STDERR_TAIL)
        self._catalog: Catalog | None = None
        self._skills: dict[str, str] = {}
        self._restart = False
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
        return self._thread

    # -- lifecycle ----------------------------------------------------
    async def start(self) -> None:
        await self._spawn()
        try:
            await self._open(self._launch.resume_id)
        except (RpcError, OSError, TimeoutError, KeyError, TypeError) as e:
            await self._end_child()
            raise ConnectionError(f"codex app-server did not open a thread: {e}") from e
        await self._try_catalog()

    async def _spawn(self) -> None:
        self._quiet = False
        mcp = self._launch.mcp
        self._launch.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self._proc = await asyncio.create_subprocess_exec(
            *argv(self._bin, mcp[0] if mcp else None, person_excludes()),
            cwd=self._launch.cwd,
            env=child_env(os.environ, mcp),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=LINE_LIMIT,
            start_new_session=True,
        )
        self._keep(self._drain(self._proc.stderr))
        self._keep(self._read())
        self._wait_task = asyncio.create_task(self._wait())
        try:
            init = await self._call(
                "initialize",
                {"clientInfo": {"name": "aegis", "version": _aegis_version()}},
                START_S,
            )
        except (RpcError, OSError, TimeoutError) as e:
            await self._end_child()
            tail = "; ".join(self._tail) or "no output"
            raise ConnectionError(f"codex app-server did not start: {e}: {tail}") from e
        self._write({"method": "initialized"})
        self._emit("aegis/initialize", init)

    async def _open(self, resume: str | None) -> None:
        provider, model_id = split_model(self.model)
        params: dict[str, Any] = {
            "cwd": str(self._launch.cwd),
            "approvalPolicy": "never",
            "sandbox": SANDBOX_MODE[self.permission],
        }
        if model_id:
            params |= {"model": model_id, "modelProvider": provider}
        if self._launch.system_prompt:
            params["developerInstructions"] = self._launch.system_prompt
        result: dict | None = None
        if resume:
            try:
                result = await self._call(
                    "thread/resume", {"threadId": resume, **params}
                )
            except RpcError as e:
                if LOST not in str(e):
                    raise
        if result is None:
            result = await self._call("thread/start", params)
        self._thread = str(_dict(result.get("thread"))["id"])
        self._provider = provider
        self._emit("aegis/thread", result)

    def _keep(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    def _note(self, line: str) -> None:
        if not line:
            return
        self._tail.append(line)
        with self._launch.stderr_path.open("a") as f:
            f.write(line + "\n")

    async def _drain(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while raw := await stream.readline():
            self._note(raw.decode(errors="replace").rstrip())

    async def _wait(self) -> None:
        assert self._proc is not None
        proc = self._proc
        code = await _exited(proc)
        self._signal(proc, signal.SIGKILL)  # whatever it left holds the thread
        self._fail_pending(ConnectionResetError("codex app-server exited"))
        if self._quiet:
            return
        self._quiet = True
        await asyncio.sleep(0.05)  # let the stderr drain catch up
        self._launch.on_exit(code, list(self._tail))

    async def terminate(self) -> None:
        self._closed = True
        await self._end_child()

    async def _end_child(self) -> None:
        self._quiet = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            if proc.stdin is not None:
                proc.stdin.close()  # a clean exit releases the thread's writer lease
            try:
                await asyncio.wait_for(_exited(proc), TERM_GRACE_S)
            except TimeoutError:
                self._signal(proc, signal.SIGTERM)
                try:
                    await asyncio.wait_for(_exited(proc), TERM_GRACE_S)
                except TimeoutError:
                    self._signal(proc, signal.SIGKILL)
                    await _exited(proc)
        if proc is not None:
            self._signal(proc, signal.SIGKILL)
        for t in list(self._tasks):
            t.cancel()
        if self._wait_task is not None:
            self._wait_task.cancel()
        self._fail_pending(ConnectionResetError("codex app-server was stopped"))

    @staticmethod
    def _signal(proc: asyncio.subprocess.Process, sig: int) -> None:
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

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
                self._refuse(msg)
            elif "id" in msg:
                fut = self._pending.pop(msg["id"], None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
            else:
                self._deliver(
                    str(msg.get("method") or ""), _dict(msg.get("params")), line
                )

    def _refuse(self, msg: dict) -> None:
        """aegis has no approval prompt, and a request nobody answers would
        hang the turn, so every request from Codex is refused at once."""
        self._note(f"refused a request from codex: {msg.get('method')}")
        self._write(
            {
                "id": msg["id"],
                "error": {"code": -32601, "message": "aegis answers no requests"},
            }
        )

    def _deliver(self, method: str, p: dict, line: str) -> None:
        if p.get("threadId") == self._thread:
            if method == "turn/started":
                self._turn = str(_dict(p.get("turn")).get("id") or "") or None
            elif method == "turn/completed":
                self._ended.append(str(_dict(p.get("turn")).get("id") or ""))
                self._turn = None
        if method in STORED or method in DELTAS:
            self._launch.on_line(line)

    def _emit(self, method: str, params: dict) -> None:
        self._launch.on_line(json.dumps({"method": method, "params": params}))

    def _write(self, obj: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise ConnectionResetError("codex app-server is not running")
        try:
            self._proc.stdin.write(
                json.dumps({"jsonrpc": "2.0", **obj}).encode() + b"\n"
            )
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as e:
            raise ConnectionResetError(str(e)) from e

    async def _call(
        self, method: str, params: dict, timeout: float = REQUEST_S
    ) -> dict:
        if not self.running:
            raise ConnectionResetError("codex app-server is not running")
        self._next += 1
        rid = self._next
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            self._write({"id": rid, "method": method, "params": params})
            msg = await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)
        if "error" in msg:
            err = msg["error"]
            raise RpcError(str(_dict(err).get("message") or err))
        result = msg.get("result")
        return result if isinstance(result, dict) else {}

    # -- turns --------------------------------------------------------
    async def send(self, text: str) -> None:
        await self._maybe_restart()
        if self._catalog is None:
            await self._try_catalog()
        if text.startswith("/"):
            await self._command(text)
            return
        items = [{"type": "text", "text": text, "text_elements": []}]
        if self._turn is not None:
            try:
                await self._call(
                    "turn/steer",
                    {
                        "threadId": self._thread,
                        "expectedTurnId": self._turn,
                        "input": items,
                    },
                )
                return
            except RpcError:
                pass  # the turn ended between the check and the call
        await self._start_turn(items)

    async def _start_turn(self, items: list[dict]) -> None:
        _, model_id = split_model(self.model)
        self._emit(
            "aegis/turn",
            {"model": self.model, "effort": self.effort, "permission": self.permission},
        )
        params: dict[str, Any] = {
            "threadId": self._thread,
            "input": items,
            "sandboxPolicy": SANDBOX_POLICY[self.permission],
        }
        if model_id:
            params["model"] = model_id
        if effort := self._effort():
            params["effort"] = effort
        result = await self._call("turn/start", params)
        tid = str(_dict(result.get("turn")).get("id") or "")
        if tid and tid not in self._ended:
            self._turn = tid

    def _effort(self) -> str | None:
        m = self._catalog.model(self.model) if self._catalog else None
        return self.effort if m is not None and self.effort in m.efforts else None

    async def _command(self, text: str) -> None:
        name, _, args = text[1:].partition(" ")
        args = args.strip()
        if name == "compact":
            self._emit("aegis/command", {"line": text, "kind": "compact"})
            await self._call("thread/compact/start", {"threadId": self._thread})
            return
        if name == "review":
            target = (
                {"type": "custom", "instructions": args}
                if args
                else {"type": "uncommittedChanges"}
            )
            self._emit("aegis/command", {"line": text, "kind": "review"})
            await self._call(
                "review/start", {"threadId": self._thread, "target": target}
            )
            return
        path = self._skills.get(name)
        if path is None:
            raise RpcError(f"/{name} is not a Codex skill in {self._launch.cwd}")
        self._emit("aegis/command", {"line": text, "kind": "skill"})
        items: list[dict] = [{"type": "skill", "name": name, "path": path}]
        if args:
            items.append({"type": "text", "text": args, "text_elements": []})
        await self._start_turn(items)

    async def interrupt(self) -> None:
        if self._turn is None:
            return
        await self._call(
            "turn/interrupt", {"threadId": self._thread, "turnId": self._turn}
        )

    async def set(self, kind: str, value: str) -> None:
        if kind == "model":
            if split_model(value)[0] != self._provider:
                self._restart = True
            self.model = value
        elif kind == "effort":
            self.effort = value
        elif kind == "permission":
            self.permission = value

    async def _maybe_restart(self) -> None:
        """A new provider: the thread reopens under it, in a new child, once no
        turn runs."""
        if not self._restart or self._turn is not None:
            return
        self._restart = False
        await self._end_child()
        if self._closed:
            raise ConnectionResetError("codex app-server was stopped")
        try:
            await self._spawn()
            if self._closed:  # stopped while the new child started
                raise ConnectionResetError("codex app-server was stopped")
            await self._open(self._thread)
        except (RpcError, OSError, TimeoutError, KeyError, TypeError) as e:
            # A child that started must not outlive a failed restart: it would
            # hold the thread's writer lease and nothing would end it.
            await self._end_child()
            if self._closed:
                raise ConnectionResetError("codex app-server was stopped") from e
            # _end_child silenced the old child's exit, so this one says it.
            self._launch.on_exit(-1, [*self._tail, f"the restart failed: {e}"])
            raise ConnectionResetError(f"codex app-server did not restart: {e}") from e

    # -- the catalog ----------------------------------------------------
    async def _try_catalog(self) -> None:
        try:
            self._catalog = await self._read_catalog()
        except (RpcError, OSError, TimeoutError):
            self._catalog = None

    async def catalog(self) -> Catalog:
        if self._catalog is None:
            self._catalog = await self._read_catalog()
        return self._catalog

    async def _read_catalog(self) -> Catalog:
        models = (await self._call("model/list", {})).get("data") or []
        skills: list[dict] = []
        found = await self._call("skills/list", {"cwds": [str(self._launch.cwd)]})
        for group in found.get("data") or []:
            skills += [
                s for s in _dict(group).get("skills") or [] if isinstance(s, dict)
            ]
        self._skills = {
            str(s["name"]): str(s.get("path") or "") for s in skills if s.get("name")
        }
        provider = split_model(self.model)[0]
        listed = (
            await self._provider_models(provider)
            if provider not in ("", "openai")
            else []
        )
        return catalog_from(models, skills, listed, self.model)

    async def _provider_models(self, provider: str) -> list:
        """An OpenAI-compatible provider's own ``/models``, so ``/model`` can
        offer its models. Nothing on any failure: the catalog still has the
        session's model."""
        try:
            cfg = await self._call("config/read", {})
            providers = _dict(_dict(cfg.get("config")).get("model_providers"))
            base = _dict(providers.get(provider)).get("base_url")
            if not isinstance(base, str):
                return []
            async with httpx.AsyncClient() as client:
                r = await asyncio.wait_for(client.get(base.rstrip("/") + "/models"), 10)
            r.raise_for_status()
            data = r.json().get("data") or []
        except (
            RpcError,
            httpx.HTTPError,
            OSError,
            TimeoutError,
            ValueError,
            AttributeError,
        ):
            return []
        return [
            m
            for d in data
            if isinstance(d, dict) and (m := provider_model(provider, d))
        ]


def person_excludes() -> list[str]:
    """The shell exclude list in the person's Codex config, which aegis's own
    ``-c`` would replace (``config.argv`` keeps it). Nothing on any failure."""
    home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    try:
        with open(Path(home) / "config.toml", "rb") as f:
            policy = tomllib.load(f).get("shell_environment_policy") or {}
    except (OSError, tomllib.TOMLDecodeError):
        return []
    found = policy.get("exclude") if isinstance(policy, dict) else None
    return [str(x) for x in found] if isinstance(found, list) else []


async def probe(bin: str, cwd: Path, stderr_path: Path, model: str = "") -> Catalog:
    """A catalog for a cwd with no live process: start ``codex app-server``,
    ask, end it. About half a second and no tokens."""
    p = CodexProcess(
        bin,
        Launch(
            cwd=cwd, model=model, effort="", permission="read", resume_id=None, mcp=None,
            system_prompt=None, stderr_path=stderr_path,
            on_line=lambda line: None, on_exit=lambda code, tail: None,
        ),
    )  # fmt: skip
    await p._spawn()
    try:
        return await p._read_catalog()
    except RpcError as e:
        raise ConnectionError(f"codex app-server gave no catalog: {e}") from e
    finally:
        await p.terminate()
