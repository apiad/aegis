"""One ``opencode serve`` child per session, driven over HTTP.

Measured on OpenCode 2.0.28 (spec ``2026-10-13-opencode-2-driver.html``): every
route lives under ``/api`` and any other path serves the web app's HTML;
``--port 0`` takes a free port, read from the line the server prints. A prompt
or a command returns once OpenCode has queued it, and the turn's end arrives as
an event. The model and its variant belong to the session, not the prompt, so a
change is sent before the next prompt; the system prompt is an instructions
entry of the session, set once per child. The command and skill lists fill a
moment after the server first reads the directory, which ``command.updated``
announces.

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

Whatever starts a child ends it, on every path (#620): an OpenCode server holds
about 300 MB doing nothing, and a day of leaked ones took a 32 GB host into the
OOM killer. Ending one signals it before any await, so a cancellation part way
through cannot skip the signal; a start or a probe that fails or is cancelled
ends its child; the child dies with the process that started it (``launch``'s
``die_with_parent``), so a server or a test killed outright leaves none behind;
and it carries its owner (``sweep.OWNER``), so ``aegis serve`` reaps at start
any child whose owner is gone (sweep.py).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import secrets
import signal
from collections import deque
from pathlib import Path
from typing import Any

import httpx

from ..claude.control import Catalog
from .. import launch
from ..harness import Launch, session_env
from .config import catalog_from, child_config, split_model
from .sweep import OWNER, owner_tag
from .stream import DELTAS, STORED, TURN_ENDS, _dict

START_S = 15.0
REQUEST_S = 30.0
CATALOG_S = 10.0
MCP_S = 15.0
MCP_SETTLED = ("connected", "failed", "disabled", "needs_auth")
INSTRUCTIONS = "aegis"  # the key of aegis's instructions entry
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
        # OpenCode says a turn is running (its execution events).
        self._turn_running = False
        self._commands_ready = asyncio.Event()
        # The model and variant the OpenCode session has, as aegis last set them.
        self._applied: tuple[str, str | None] | None = None
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
                body = {"model": split_model(self.model)} if self.model else {}
                created = await self._call("POST", "/api/session", json=body)
                self._session_id = created["id"]
                self._applied = (self.model, None)
            await self._instruct()
            await self._await_mcp()
        except (httpx.HTTPError, OSError, TimeoutError, KeyError, TypeError) as e:
            await self._end_child()
            raise ConnectionError(f"opencode serve did not start a session: {e}") from e
        except BaseException:  # a cancellation too: the child must not outlive it
            await self._end_child()
            raise
        early, self._early = self._early, []
        for raw in early:
            self._deliver(raw)
        await self._try_catalog()

    async def _instruct(self) -> None:
        """The system prompt, as this session's instructions entry: OpenCode
        keeps it with the session, but a child sets it again in case it
        changed. The route is experimental in OpenCode, so a refusal is noted
        and the session runs without it."""
        if not self._launch.system_prompt:
            return
        try:
            await self._call(
                "PUT",
                f"/api/experimental/session/{self._session_id}/instructions/entries/{INSTRUCTIONS}",
                json={"value": self._launch.system_prompt},
            )
        except httpx.HTTPStatusError as e:
            self._note(f"the system prompt was refused: {e.response.status_code}")

    async def _await_mcp(self) -> None:
        """Wait until OpenCode has connected aegis's MCP server. It connects
        when it first reads the directory, about 2.5 s after the start, and a
        prompt sent before that runs without aegis's tools."""
        if self._launch.mcp is None:
            return
        status = "pending"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + MCP_S
        while loop.time() < deadline:
            servers = await self._call("GET", "/api/mcp") or []
            for srv in servers:
                if isinstance(srv, dict) and srv.get("name") == "aegis":
                    st = srv.get("status")
                    status = str(
                        (st.get("status") if isinstance(st, dict) else st) or ""
                    )
                    if status in MCP_SETTLED:
                        if status != "connected":
                            err = st.get("error") if isinstance(st, dict) else None
                            self._note(
                                f"aegis's MCP server is {status}: {err or ''}".rstrip(
                                    ": "
                                )
                            )
                        return
            await asyncio.sleep(0.2)
        self._note(f"aegis's MCP server was still {status} after {MCP_S:g}s")

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
                child_config(
                    self._launch.mcp,
                    self.permission,
                    self._launch.read_dirs,
                    self._launch.knowledge_native,
                )
            ),
            "OPENCODE_SERVER_PASSWORD": password,
            OWNER: owner_tag(),
        }
        self._launch.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self._proc = await launch.child(
            [self._bin, "serve", "--hostname", "127.0.0.1", "--port", "0"],
            cwd=self._launch.cwd, env=session_env(self._launch, env),
            stdin=launch.DEVNULL, stdout=launch.PIPE, stderr=launch.PIPE,
            cgroup=self._launch.cgroup, oom=True, user=self._launch.user,
            via=self._launch.via, die_with_parent=True,
        )  # fmt: skip
        try:
            self._keep(self._drain(self._proc.stderr))
            url = await asyncio.wait_for(self._listening(), START_S)
            self._keep(self._drain(self._proc.stdout))
            self._client = httpx.AsyncClient(
                base_url=url,
                auth=("opencode", password),
                params={"directory": str(self._launch.cwd)},
                timeout=httpx.Timeout(REQUEST_S, read=None),
            )
            self._connected = asyncio.Event()
            self._commands_ready = asyncio.Event()
            self._pump_task = asyncio.create_task(self._pump())
            await asyncio.wait_for(self._connected.wait(), START_S)
        except BaseException:  # a cancellation too: the child must not outlive it
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
        proc = self._proc
        if proc is not None and proc.returncode is None:
            await launch.signal_process_async(
                proc.pid, signal.SIGTERM, user=self._launch.user
            )  # direct signal before the fallback can yield
        else:
            proc = None
        await self._close()
        if proc is not None:
            try:
                await asyncio.wait_for(proc.wait(), TERM_GRACE_S)
            except TimeoutError:
                await launch.signal_process_async(
                    proc.pid, signal.SIGKILL, user=self._launch.user
                )
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
            async with self._client.stream("GET", "/api/event") as r:
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
            await launch.signal_process_async(
                self._proc.pid, signal.SIGTERM, user=self._launch.user
            )  # _wait reports the exit

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
            if kind == "command.updated":
                self._commands_ready.set()
                return
            if kind not in STORED and kind not in DELTAS:
                return
            data: dict = _dict(obj.get("data"))
            if self._session_id is None:
                self._early.append(raw)
                return
            sid = data.get("sessionID")
            if kind == "session.created" and data.get("parentID") in (
                {self._session_id} | self._children
            ):
                self._children.add(str(sid))
            if sid != self._session_id and sid not in self._children:
                return
            if sid == self._session_id:
                if kind == "session.execution.started":
                    self._turn_running = True
                elif kind in TURN_ENDS:
                    self._busy = self._turn_running = False
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
        """A request; the reply's ``data``, which every v2 route wraps it in."""
        if self._client is None:
            raise ConnectionResetError("opencode serve is not running")
        req = self._client.request(method, path, json=json)
        try:
            r = await (asyncio.wait_for(req, timeout) if timeout else req)
        except httpx.TransportError as e:
            raise ConnectionResetError(str(e)) from e
        r.raise_for_status()
        out = r.json() if r.content else None
        return out.get("data") if isinstance(out, dict) and "data" in out else out

    async def _exists(self, sid: str) -> bool:
        try:
            await self._call("GET", f"/api/session/{sid}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return False
            raise
        return True

    async def _read_catalog(self) -> Catalog:
        commands = await self._call("GET", "/api/command")
        if not commands:
            # The first read starts the directory's load; its end is announced.
            try:
                await asyncio.wait_for(self._commands_ready.wait(), CATALOG_S)
            except TimeoutError:
                pass
            commands = await self._call("GET", "/api/command")
        skills = await self._call("GET", "/api/skill")
        models = await self._call("GET", "/api/model")
        return catalog_from(commands or [], skills or [], models or [])

    def _variant(self) -> str | None:
        m = self._catalog.model(self.model) if self._catalog else None
        return self.effort if m is not None and self.effort in m.efforts else None

    async def _apply_model(self) -> None:
        """The session's model and variant, sent only when aegis changed them."""
        want = (self.model, self._variant())
        if not self.model or want == self._applied:
            return
        ref = split_model(self.model)
        if want[1]:
            ref["variant"] = want[1]
        # Claimed before the request, so two sends at once switch it once.
        before, self._applied = self._applied, want
        try:
            await self._call(
                "POST", f"/api/session/{self._session_id}/model", json={"model": ref}
            )
        except BaseException:
            self._applied = before
            raise

    async def _maybe_restart(self) -> None:
        if not self._restart or self._busy:
            return
        self._restart = False
        await self._end_child()
        self._launch = dataclasses.replace(self._launch, resume_id=self._session_id)
        try:
            await self._spawn()
            await self._instruct()
            await self._await_mcp()
        except (OSError, TimeoutError, httpx.HTTPError) as e:
            # _end_child silenced the old child's exit, so this one says it.
            self._launch.on_exit(-1, [*self._tail, f"the restart failed: {e}"])
            raise ConnectionResetError(f"opencode serve did not restart: {e}") from e

    async def send(self, text: str) -> None:
        await self._maybe_restart()
        # Busy from the send on, not from OpenCode's start event: a prompt sent
        # right after must not restart the child under the turn it started.
        self._busy = True
        try:
            if self._catalog is None:
                await self._try_catalog()
            await self._apply_model()
            if text.startswith("/"):
                return await self._command(text)
            await self._call(
                "POST", f"/api/session/{self._session_id}/prompt", json={"text": text}
            )
        except (httpx.HTTPStatusError, OSError, TimeoutError):
            if not self._turn_running:
                self._busy = False  # this send started nothing
            raise

    async def _command(self, line: str) -> None:
        """A ``/name args`` line: a command, or a skill with the arguments as
        its prompt. OpenCode refusing it ends nothing that was running."""
        name, _, args = line[1:].partition(" ")
        args = args.strip()
        sid = self._session_id
        if self._catalog is not None and any(
            c["name"] == name and c["source"] == "skill" for c in self._catalog.commands
        ):
            path, body = f"/api/session/{sid}/prompt", {"text": args or line, "skills": [{"id": name}]}  # fmt: skip
        else:
            path, body = f"/api/session/{sid}/command", {"name": name, "text": args}
        try:
            await self._call("POST", path, json=body)
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

    async def interrupt(self) -> None:
        await self._call("POST", f"/api/session/{self._session_id}/interrupt", json={})

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
    try:
        await p._spawn()
        return await p._read_catalog()
    except httpx.HTTPError as e:
        raise ConnectionError(f"opencode serve gave no catalog: {e}") from e
    finally:
        await p.terminate()
