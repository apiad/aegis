"""One ``opencode serve`` child per session, driven over HTTP.

Measured on OpenCode 1.18.31 (spec ``2026-10-08-aegis-2-opencode-harness-design.md``):
the v1 routes run a turn and the v2 ``/api/session`` routes do not;
``--port 0`` takes 4096 when it is free and another free port otherwise, so the
port is read from the line the server prints; ``POST /session/{id}/command``
blocks until the turn ends, so it runs on its own task.

The child reads its config once, at start, from ``OPENCODE_CONFIG_CONTENT``:
the aegis MCP server with this session's token, and the permission rules. A
permission change therefore restarts the child before the next prompt that
comes after the turn, and the new child resumes the same OpenCode session. A
random password guards the port, because any process or page on the machine
can reach a localhost port.

Events reach ``on_line`` as they arrive, only this session's and its child
sessions', and only the types the fold reads (``stream.STORED``) plus deltas.
If the event stream closes while the child runs, the child is ended and its
exit reported: a session that can no longer hear its harness must not look
alive.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import secrets
from collections import deque
from pathlib import Path
from typing import Any

import httpx

from ..claude.control import Catalog
from ..harness import Launch
from .config import catalog_from, child_config, split_model
from .stream import DELTA, STORED, _dict, session_of

START_S = 15.0
REQUEST_S = 30.0
TERM_GRACE_S = 5.0
STDERR_TAIL = 20
_LISTENING = re.compile(r"listening on (http://\S+)")


class OpenCodeProcess:
    def __init__(self, bin: str, launch: Launch) -> None:
        self._bin = bin
        self._launch = launch
        self.model, self.effort, self.permission = (
            launch.model,
            launch.effort,
            launch.permission,
        )
        self._session_id: str | None = None
        self._children: set[str] = set()
        self._proc: asyncio.subprocess.Process | None = None
        self._client: httpx.AsyncClient | None = None
        self._tasks: set[asyncio.Task] = set()
        self._pump_task: asyncio.Task | None = None
        self._wait_task: asyncio.Task | None = None
        self._connected = asyncio.Event()
        self._early: list[str] = []
        self._catalog: Catalog | None = None
        # Sent something since the last idle: a turn is running or about to.
        self._busy = False
        # OpenCode says a turn is running (its status events).
        self._turn_running = False
        # Set when OpenCode has taken the last command (its user message).
        self._admitted = asyncio.Event()
        self._admitted.set()
        self._restart = False
        self._quiet = False  # ending the child on purpose: report nothing
        self._tail: deque[str] = deque(maxlen=STDERR_TAIL)

    # -- what the session reads --------------------------------------
    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    # -- lifecycle ----------------------------------------------------
    async def start(self) -> None:
        await self._spawn()
        try:
            resume = self._launch.resume_id
            if resume and await self._exists(resume):
                self._session_id = resume
            else:
                created = await self._call("POST", "/session", json={})
                self._session_id = created["id"]
        except (httpx.HTTPError, OSError, TimeoutError, KeyError, TypeError) as e:
            await self._end_child()
            raise ConnectionError(f"opencode serve did not start a session: {e}") from e
        early, self._early = self._early, []
        for raw in early:
            self._deliver(raw)
        await self._try_catalog()

    async def _try_catalog(self) -> None:
        """The catalog is optional (a slow MCP server can hold up /command):
        without it a prompt carries no variant until a later send finds it."""
        try:
            self._catalog = await self._read_catalog()
        except (httpx.HTTPError, OSError, TimeoutError):
            self._catalog = None

    async def _spawn(self) -> None:
        self._quiet = False
        password = secrets.token_hex(16)
        env = {
            **os.environ,
            "OPENCODE_CONFIG_CONTENT": json.dumps(
                child_config(self._launch.mcp, self.permission)
            ),
            "OPENCODE_SERVER_PASSWORD": password,
        }
        self._launch.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self._proc = await asyncio.create_subprocess_exec(
            self._bin, "serve", "--hostname", "127.0.0.1", "--port", "0",
            cwd=self._launch.cwd, env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        self._keep(self._drain(self._proc.stderr))
        try:
            url = await asyncio.wait_for(self._listening(), START_S)
        except (TimeoutError, OSError):
            await self._end_child()
            raise
        self._keep(self._drain(self._proc.stdout))
        self._client = httpx.AsyncClient(
            base_url=url,
            auth=("opencode", password),
            params={"directory": str(self._launch.cwd)},
            timeout=httpx.Timeout(REQUEST_S, read=None),
        )
        self._connected = asyncio.Event()
        self._pump_task = asyncio.create_task(self._pump())
        try:
            await asyncio.wait_for(self._connected.wait(), START_S)
        except TimeoutError:
            await self._end_child()
            raise
        self._wait_task = asyncio.create_task(self._wait())

    async def _listening(self) -> str:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:
                await self._proc.wait()
                await asyncio.sleep(0.05)  # let the stderr drain catch up
                tail = "; ".join(self._tail) or "no output"
                raise ConnectionError(
                    f"opencode serve exited with code {self._proc.returncode} "
                    f"before listening: {tail}"
                )
            line = raw.decode(errors="replace").strip()
            if m := _LISTENING.search(line):
                return m.group(1)
            self._note(line)

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

    def _keep(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def _wait(self) -> None:
        assert self._proc is not None
        code = await self._proc.wait()
        if self._quiet:
            return
        self._quiet = True
        await self._close()
        self._launch.on_exit(code, list(self._tail))

    async def terminate(self) -> None:
        await self._end_child()

    async def _end_child(self) -> None:
        self._quiet = True
        await self._close()
        proc = self._proc
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), TERM_GRACE_S)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        if self._wait_task is not None:
            self._wait_task.cancel()

    async def _close(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
        for t in list(self._tasks):
            t.cancel()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- events -------------------------------------------------------
    async def _pump(self) -> None:
        assert self._client is not None
        try:
            async with self._client.stream("GET", "/event") as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        self._deliver(line[5:].strip())
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as e:
            self._note(f"the event stream failed: {e}")
        if not self._quiet and self.running:
            self._note("the event stream closed")
            assert self._proc is not None
            self._proc.terminate()  # _wait reports the exit

    def _deliver(self, raw: str) -> None:
        try:
            obj: Any = json.loads(raw)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            kind = obj.get("type")
            if kind == "server.connected":
                self._connected.set()
                return
            if kind not in STORED and kind != DELTA:
                return
            props: dict = _dict(obj.get("properties"))
            if self._session_id is None:
                self._early.append(raw)
                return
            info: dict = _dict(props.get("info"))
            if kind == "session.created" and info.get("parentID") in (
                {self._session_id} | self._children
            ):
                self._children.add(str(info.get("id")))
            sid = session_of(props)
            if sid != self._session_id and sid not in self._children:
                return
            if kind == "session.status" and sid == self._session_id:
                self._turn_running = (props.get("status") or {}).get("type") == "busy"
            if kind == "session.idle" and sid == self._session_id:
                self._busy = self._turn_running = False
            if (
                kind == "message.updated"
                and sid == self._session_id
                and info.get("role") == "user"
            ):
                self._admitted.set()
        self._launch.on_line(raw)

    # -- requests -----------------------------------------------------
    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        timeout: float | None = REQUEST_S,
    ) -> Any:
        if self._client is None:
            raise ConnectionResetError("opencode serve is not running")
        req = self._client.request(method, path, json=json)
        try:
            r = await (asyncio.wait_for(req, timeout) if timeout else req)
        except httpx.TransportError as e:
            raise ConnectionResetError(str(e)) from e
        r.raise_for_status()
        return r.json() if r.content else None

    async def _exists(self, sid: str) -> bool:
        try:
            await self._call("GET", f"/session/{sid}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return False
            raise
        return True

    async def _read_catalog(self) -> Catalog:
        commands = await self._call("GET", "/command")
        providers = await self._call("GET", "/config/providers")
        return catalog_from(commands or [], providers or {})

    def _variant(self) -> str | None:
        m = self._catalog.model(self.model) if self._catalog else None
        return self.effort if m is not None and self.effort in m.efforts else None

    async def _maybe_restart(self) -> None:
        if not self._restart or self._busy:
            return
        self._restart = False
        await self._end_child()
        self._launch = dataclasses.replace(self._launch, resume_id=self._session_id)
        try:
            await self._spawn()
        except (OSError, TimeoutError) as e:
            # _end_child silenced the old child's exit, so this one says it.
            self._launch.on_exit(-1, [*self._tail, f"the restart failed: {e}"])
            raise ConnectionResetError(f"opencode serve did not restart: {e}") from e

    async def send(self, text: str) -> None:
        await self._maybe_restart()
        # Busy from the send on, not from OpenCode's busy event: a prompt sent
        # right after must not restart the child under the turn it started.
        self._busy = True
        if self._catalog is None:
            await self._try_catalog()
        if text.startswith("/"):
            name, _, args = text[1:].partition(" ")
            # The command's request lasts the whole turn, so it runs on its own
            # task; what is sent next waits until OpenCode has taken it, or a
            # prompt could overtake it.
            self._admitted.clear()
            self._keep(self._command(name, args.strip(), text))
            try:
                await asyncio.wait_for(self._admitted.wait(), REQUEST_S)
            except TimeoutError:
                pass
            return
        body: dict[str, Any] = {
            "parts": [{"type": "text", "text": text}],
            "model": split_model(self.model),
        }
        if variant := self._variant():
            body["variant"] = variant
        if self._launch.system_prompt:
            body["system"] = self._launch.system_prompt
        try:
            await self._call(
                "POST", f"/session/{self._session_id}/prompt_async", json=body
            )
        except (httpx.HTTPStatusError, OSError, TimeoutError):
            if not self._turn_running:
                self._busy = False  # this send started nothing
            raise

    async def _command(self, name: str, args: str, line: str) -> None:
        body: dict[str, Any] = {"command": name, "arguments": args, "model": self.model}
        if variant := self._variant():
            body["variant"] = variant
        try:
            await self._call(
                "POST", f"/session/{self._session_id}/command", json=body, timeout=None
            )
        except httpx.HTTPStatusError as e:
            r = e.response
            if not self._turn_running:
                self._busy = False  # the command started nothing
            self._launch.on_error(
                f"/{name} failed: {r.status_code} {r.text[:200]}",
                not self._turn_running,
                line,
            )
        except ConnectionResetError:
            pass  # the child is gone; its exit is reported
        finally:
            self._admitted.set()

    async def interrupt(self) -> None:
        await self._call("POST", f"/session/{self._session_id}/abort", json={})

    async def set(self, kind: str, value: str) -> None:
        if kind == "model":
            self.model = value
        elif kind == "effort":
            self.effort = value
        elif kind == "permission" and value != self.permission:
            self.permission, self._restart = value, True

    async def catalog(self) -> Catalog:
        if self._catalog is None:
            self._catalog = await self._read_catalog()
        return self._catalog


async def probe(bin: str, cwd: Path, stderr_path: Path) -> Catalog:
    """A catalog for a cwd with no live process: start ``opencode serve``,
    ask, end it. About 3 s and no tokens."""
    p = OpenCodeProcess(
        bin,
        Launch(
            cwd=cwd, model="", effort="", permission="full", resume_id=None, mcp=None,
            system_prompt=None, stderr_path=stderr_path,
            on_line=lambda line: None, on_exit=lambda code, tail: None,
        ),
    )  # fmt: skip
    await p._spawn()
    try:
        return await p._read_catalog()
    except httpx.HTTPError as e:
        raise ConnectionError(f"opencode serve gave no catalog: {e}") from e
    finally:
        await p.terminate()
