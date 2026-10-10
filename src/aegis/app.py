"""The server's state and its operations, independent of any transport.

Operations: ``agents.list``, ``session.spawn``, ``session.send`` (which
resolves a ``/`` line first, ``commands.py``), ``session.read``,
``recap.request``, ``dictation.prepare``, ``session.configure``,
``commands.list``, ``session.interrupt``, ``session.stop``, ``session.reopen``,
``session.rename``, ``archive.list``, ``server.version``, ``file.open``, ``file.peek``,
``attachment.begin``, ``attachment.put``, ``attachment.drop``,
``quota.read``, ``transcript.detail``, ``transcript.search``, ``transcript.output``, and ``config.read``, ``config.write``,
``config.detect``, ``config.doctor`` and ``config.propose`` (``config_ops.py``),
and ``artifact.create``, ``artifact.send``, ``artifact.read``,
``artifact.update``, ``artifact.close``, ``artifact.state``, ``artifact.emit``,
``artifact.submit``, ``artifact.error`` and ``artifact.probed``
(``artifact_ops.py``).
Channels: ``sessions`` (every open session's meta; patches ``upsert`` and
``remove``), ``transcript:<log_id>`` (any session, archived included; a
subscribe ``since`` a revision gets what changed after it), ``quota`` (each
provider's windows; patches ``set``), ``host`` (CPU, RAM and disk while someone
watches; patches ``set``) and ``config`` (``.aegis.yaml`` as aegis holds it;
patches ``set``).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import getpass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from . import archive, attachments as att, commands, dictation, files
from .agent_ops import register_agent_ops, spawn_opening
from .artifact_ops import register_artifact_ops
from .agents import (
    EFFORTS,
    HARNESSES,
    PERMISSION_ORDER,
    SUPPORTED_HARNESSES,
    model_suggestions,
    resolve,
)
from .channels import Channels, Throttle
from .claude.process import PERMISSION_MODE, ControlError
from .config import Config, Snapshot
from .config_ops import register_config_ops
from .confirm import Confirmations
from .host import HostSampler
from .links import LinkError, Links, probe
from .mcp import PATH as MCP_PATH, Tokens, build_mcp
from .monitors import Monitors
from .queues import Queues
from .quota import Quota
from .recaps import Recaps
from .ops import Caller, NoParams, OpError, Registry as Ops
from .registry import Registry
from .roots import Roots
from .session import PUBLISH_EVERY_S
from .version import Versions

Effort = Literal["low", "medium", "high", "xhigh", "max"]
Permission = Literal["read", "write", "full", "auto"]


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class SpawnParams(_Strict):
    agent: str | None = Field(
        None,
        description="The agent to start from (agents_list shows them); "
        "omitted means default_agent.",
    )
    harness: str | None = Field(None, description="Overrides the agent's harness.")
    model: str | None = Field(None, description="Overrides the agent's model.")
    effort: Effort | None = Field(None, description="Overrides the agent's effort.")
    permission: Permission | None = Field(
        None, description="Overrides the agent's permission."
    )
    cwd: str | None = Field(
        None,
        description="Working directory inside the server's root; a relative one "
        "resolves against yours.",
    )
    prompt: str | None = Field(
        None, min_length=1, description="The new session's first message."
    )


class SendParams(_Strict):
    log_id: str
    text: str = ""
    # Upload ids from attachment.begin, every one complete (attachments.py).
    attachments: list[str] = Field(default_factory=list, max_length=att.MAX_FILES)

    @model_validator(mode="after")
    def _says_something(self) -> "SendParams":
        if not self.text and not self.attachments:
            raise ValueError("a message needs text or attachments")
        return self


class AttachBegin(_Strict):
    log_id: str
    name: str = Field(min_length=1, max_length=1000)
    size: int = Field(ge=0, le=files.MAX_BYTES)


class AttachPut(_Strict):
    log_id: str
    upload_id: str
    offset: int = Field(ge=0)
    # Base64 of at most CHUNK_BYTES.
    data: str = Field(max_length=4 * ((att.CHUNK_BYTES + 2) // 3))


class AttachDrop(_Strict):
    log_id: str
    upload_id: str


class ReadParams(_Strict):
    log_id: str
    ids: list[str] = Field(max_length=500)


class RecapParams(_Strict):
    log_id: str
    force: bool = False


class ConfigureParams(_Strict):
    log_id: str
    model: str | None = None
    effort: Effort | None = None
    permission: Permission | None = None


class LogParams(_Strict):
    log_id: str


class DetailParams(_Strict):
    log_id: str
    ids: list[str] = Field(min_length=1, max_length=100)


class OutputParams(_Strict):
    log_id: str
    id: str


class SearchParams(_Strict):
    log_id: str
    q: str = Field(min_length=1, max_length=200)


class RenameParams(_Strict):
    log_id: str | None = None
    handle: str | None = None
    title: str | None = None


class FileRef(_Strict):
    file_id: str
    name: str


class PeekParams(_Strict):
    log_id: str
    entry_id: str


class LinkAdd(_Strict):
    url: str = Field(description="The far server's page URL, e.g. https://dev.example")
    token: str = Field(min_length=1)
    name: str | None = Field(
        None, description="What it must call itself; omitted: whatever it does."
    )


class LinkName(_Strict):
    name: str


class ArchiveParams(_Strict):
    query: str | None = None
    server: str | None = Field(None, description="One server only; omitted: all.")
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(
        None, description="The cursor the last page returned; omitted: the first."
    )


def _dead(e: Exception) -> OpError:
    return OpError(
        "session_dead",
        f"the agent stopped while being written to: {e}; send again to resume",
    )


class App:
    def __init__(
        self,
        roots: Roots,
        claude_bin: str = "claude",
        interrupt_timeout: float = 10.0,
        base_url: str | None = None,
        server_name: str = "aegis",
        opencode_bin: str = "opencode",
        codex_bin: str = "codex",
        dictation_dir: Path | None = None,
        user: str | None = None,
    ) -> None:
        self.roots = roots
        self.server_name = server_name
        self.claude_bin = claude_bin
        self.opencode_bin = opencode_bin
        self.codex_bin = codex_bin
        self.dictation = dictation.Store(dictation_dir or dictation.default_dir())
        self.channels = Channels(self._resolve)
        # Every session's card changes go out together, a few times a second at
        # most, however many sessions are working (#158).
        self._sessions_out = Throttle(
            lambda ops: self.channels.publish("sessions", ops),
            self._sessions_key,
            PUBLISH_EVERY_S,
        )
        self._on_wire: dict[str, tuple] = {}  # log_id -> its fields a person acts on
        self.sessions = Registry(
            roots,
            self.publish,
            claude_bin,
            interrupt_timeout,
            opencode_bin=opencode_bin,
            codex_bin=codex_bin,
        )
        self.tokens = Tokens()
        self.monitors = Monitors(self.sessions, roots.state_root / "monitors.json")
        self.config = Config(roots.config_root, self._on_config)
        self._config_task: asyncio.Task | None = None
        self._background: set[asyncio.Task] = set()
        self.queues = Queues(
            self.sessions, self.monitors, roots.state_root / "tasks.jsonl", self.config
        )
        self.confirmations = Confirmations()
        self.quota = Quota(self.publish)
        self.host = HostSampler(
            self.publish, self.channels.subscribers, roots.config_root
        )
        reg = self.sessions
        reg.tokens, reg.monitors, reg.queues, reg.server_name = (
            self.tokens,
            self.monitors,
            self.queues,
            server_name,
        )
        reg.quota = self.quota
        self.catalogs = commands.Catalogs(
            roots.state_root / "stderr" / "catalog-probe.log"
        )
        reg.catalogs = self.catalogs
        reg.mcp_url = f"{base_url.rstrip('/')}{MCP_PATH}" if base_url else None
        self.versions = Versions()
        self.recaps = Recaps(self)
        # Who the people on this server are, as a link tells the far side. One
        # user per server for now: whoever started it (links.py).
        self.user = user or getpass.getuser()
        self.links = Links(roots.state_root, server_name, self.user, self.publish)
        self.registry = Ops()
        self._register()
        register_agent_ops(self)
        register_config_ops(self)
        register_artifact_ops(self)
        self.mcp_server, self.mcp_app = build_mcp(self.registry, self.tokens)

    def _bin(self, harness: str) -> str:
        return {"opencode": self.opencode_bin, "codex": self.codex_bin}.get(
            harness, self.claude_bin
        )

    async def boot(self) -> None:
        self._config_task = asyncio.create_task(self.config.watch())
        self.sessions.boot()
        # No browser's upload survives a restart (attachments.py).
        att.clear_staged(self.roots.state_root)
        self.monitors.boot()
        self.monitors.arm_all()
        await self.queues.resume_after_boot(self.queues.boot())
        self.quota.start()
        self.host.start()
        self.links.boot()

    async def shutdown(self) -> None:
        if self._config_task is not None:
            self._config_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._config_task
        await self.links.shutdown()
        await self.quota.stop()
        await self.host.stop()
        await self.monitors.shutdown()
        await self.recaps.shutdown()
        await self.sessions.shutdown()

    def _on_config(self, snap: Snapshot) -> None:
        """Every change to .aegis.yaml, however it was made: the page and the
        composer follow it, and the queues start what it now allows."""
        self.channels.publish("config", [{"set": snap.wire()}])
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        t = loop.create_task(self.queues.dispatch())
        self._background.add(t)
        t.add_done_callback(self._background.discard)

    def publish(self, channel: str, ops: list[dict]) -> None:
        if channel == "sessions":
            self._sessions_out.add(ops)
        else:
            self.channels.publish(channel, ops)

    def _sessions_key(self, op: dict) -> tuple[str, bool]:
        """A session added or removed, or a change to its state, name, model,
        attention, mark or blink, goes out at once: the page acts on them (Esc
        interrupts only a working session, a spawn shows the new tab, a person
        answers a session that needs them, a mark clearing or starting to blink
        changes what a person acts on). The rest of a card can wait."""
        if "remove" in op:
            self._on_wire.pop(op["remove"], None)
            return op["remove"], True
        m = op["upsert"]
        seen = (
            m["state"],
            m["title"],
            m["handle"],
            m["model"],
            m.get("model_id"),
            m.get("attention"),
            m.get("mark"),
            m.get("blink"),
        )
        urgent = self._on_wire.get(m["log_id"]) != seen
        self._on_wire[m["log_id"]] = seen
        return m["log_id"], urgent

    def _resolve(self, name: str, since: int | None = None):
        if name == "sessions":
            return lambda: [s.wire() for s in self.sessions.open_sessions()]
        if name.startswith("transcript:"):
            return self.sessions.transcript(name.removeprefix("transcript:"), since)
        if name == "quota":
            return self.quota.snapshot
        if name == "host":
            return self.host.snapshot
        if name == "config":
            return lambda: self.config.current().wire()
        if name == "links":
            return self.links.wire
        return None

    def _agents(self):
        snap = self.config.current()
        return list(snap.agents), snap.default_agent

    def _resolve_cwd(self, raw: str | None, base: Path | None = None) -> Path:
        base = base or self.roots.harness_cwd
        p = Path(raw).expanduser() if raw else base
        if not p.is_absolute():
            p = base / p
        p = p.resolve()
        root = self.roots.config_root.resolve()
        if not (p == root or p.is_relative_to(root)):
            raise OpError("bad_cwd", f"{p} is outside {root}")
        if not p.is_dir():
            raise OpError("bad_cwd", f"{p} is not a directory")
        return p

    async def _configure(
        self,
        s,
        model: str | None,
        effort: str | None,
        permission: str | None,
    ) -> dict:
        if not (model or effort or permission):
            raise OpError("bad_params", "nothing to change")
        cat = await self.catalogs.get(s) if model or effort else None
        if cat is not None:  # without one, claude itself refuses a bad value
            if model:
                m = cat.model(model)
                if m is None:
                    raise OpError(
                        "unknown_model", f"no model {model!r}; /model lists them"
                    )
                model = m.value
            if effort:
                cur = cat.model(model or s.spec.model or "default")
                if cur is not None and effort not in cur.efforts:
                    raise OpError(
                        "bad_effort",
                        f"{cur.label} takes no effort level"
                        if not cur.efforts
                        else f"{cur.label} takes {', '.join(cur.efforts)}",
                    )
        try:
            await s.configure(model=model, effort=effort, permission=permission)
        except ControlError as e:
            raise OpError("refused", f"{s.harness.label} refused: {e}") from e
        except TimeoutError as e:
            raise OpError(
                "timeout", f"{s.harness.label} did not answer within 15 s"
            ) from e
        except (BrokenPipeError, ConnectionResetError) as e:
            raise _dead(e) from e
        return s.wire()

    async def _command(self, s, name: str, arg: str, caller: Caller = Caller("user")):
        """Run an aegis command typed in the composer."""
        cmd = commands.AEGIS[name]
        if cmd.hint.startswith("<") and not arg:
            raise OpError("missing_argument", f"usage: /{name} {cmd.hint}")
        reg = self.sessions
        if name == "model":
            return await self._configure(s, arg, None, None)
        if name == "effort":
            if arg not in EFFORTS:
                raise OpError("bad_effort", f"an effort is one of {', '.join(EFFORTS)}")
            return await self._configure(s, None, arg, None)
        if name == "permission":
            if arg not in PERMISSION_MODE:
                raise OpError(
                    "bad_permission",
                    f"a permission is one of {', '.join(PERMISSION_MODE)}",
                )
            return await self._configure(s, None, None, arg)
        if name == "rename":
            return reg.rename(s.log_id, arg, None)
        if name == "title":
            return reg.rename(s.log_id, None, arg)
        if name == "help":
            return None  # the client opens its menu; nothing goes to claude
        if name == "stop":
            await s.stop()
            return s.wire()
        if name == "spawn":
            return await self._spawn_line(commands.parse_spawn(arg), caller, s)
        await reg.close(s.log_id)  # close
        return None

    async def _spawn_line(self, line: commands.SpawnLine, caller: Caller, s) -> dict:
        """``/spawn``: a person starting a session, here or, with ``@server``, on
        a linked server through the link. Nobody's child: no ``spawned_by``, but
        its prompt carries the tail of tab ``s``, where it was typed."""
        far = None if line.server == self.server_name else line.server
        prompt = line.prompt and spawn_opening(s, line.prompt, far and self.server_name)
        params = {
            k: v
            for k, v in (
                ("agent", line.agent),
                ("prompt", prompt),
                ("model", line.model),
                ("effort", line.effort),
                ("permission", line.permission),
                ("cwd", line.cwd),
            )
            if v is not None
        }
        if far is None:
            r = await self.registry.call("session.spawn", params, caller)
            return {**r, "server": self.server_name}
        if caller.link is not None:
            raise OpError("not_relayed", "a link is not relayed to another server")
        link = self.links.get(far)
        if link is None:
            raise OpError("unknown_server", f"this server links no server named {far}")
        if link.state != "linked":
            raise OpError("server_offline", f"{link.describe()}; nothing was started")
        r = await link.call("session.spawn", params)
        return {**r, "server": far}

    async def _archive(self, p: ArchiveParams, caller: Caller) -> dict:
        positions = archive.decode(p.cursor)
        # A link asking reads this server's archive only: links are not relayed.
        only = self.server_name if caller.link is not None else p.server
        pages: dict[str, tuple[list[dict], archive.Position | None]] = {}
        counts: dict[str, int] = {}
        for name, fetch in self._archive_sources(only):
            if name in positions and positions[name] is None:
                continue  # exhausted on an earlier page
            got = await fetch(p.query, p.limit, positions.get(name))
            if got is None:
                continue  # a linked server that is down keeps its place
            items, total, last = got
            pages[name] = ([{**m, "server": name} for m in items], last)
            counts[name] = total
        items, positions = archive.merge(pages, p.limit, positions)
        more = any(v is not None for v in positions.values()) or any(
            name not in positions for name in pages
        )
        # A linked server that is down is named, so its rows are visibly missing.
        offline = [
            link.name
            for link in self.links.links()
            if link.state != "linked"
            and only in (None, link.name)
            and caller.link is None
        ]
        return {
            "items": items,
            "total": sum(counts.values()),
            "counts": counts,
            "offline": offline,
            "cursor": archive.encode(positions) if more else None,
        }

    def _archive_sources(self, only: str | None):
        """Each server an archive listing reads, with how to fetch a page."""

        async def local(query, limit, after):
            return self.sessions.archive(query, limit, after)

        if only in (None, self.server_name):
            yield self.server_name, local
        for link in self.links.up():
            if only not in (None, link.name):
                continue

            async def far(query, limit, after, link=link):
                cursor = archive.encode({link.name: after}) if after else None
                try:
                    r = await link.call(
                        "archive.list",
                        {
                            "query": query,
                            "limit": limit,
                            "server": link.name,
                            "cursor": cursor,
                        },
                    )
                except OpError:
                    return None  # down since the listing started: keep its place
                last = archive.decode(r.get("cursor")).get(link.name)
                return r.get("items", []), r.get("total", 0), last

            yield link.name, far

    def _register(self) -> None:
        r = self.registry
        reg = self.sessions

        @r.op("agents.list", agent=True)
        async def agents_list(_, caller):
            """The agents you can spawn, each a preset of harness, model, effort
            and permission. session_spawn starts one and can override those."""
            agents, default = self._agents()
            return {
                "agents": [a.as_dict() for a in agents],
                "default": default,
                "harnesses": [
                    {"name": h, "supported": h in SUPPORTED_HARNESSES}
                    for h in HARNESSES
                ],
                "models": model_suggestions(agents),
                "cwd": str(self.roots.harness_cwd),
                "config_error": self.config.current().error,
            }

        @r.op("session.spawn", SpawnParams, agent=True)
        async def spawn(p: SpawnParams, caller):
            """Start a new session from an agent, overriding its harness, model,
            effort or permission if you need to, and send it `prompt` as its
            first message. Its permission can be at most yours. Returns its log
            id and handle. It does not report back: read it with peer_read,
            message it with peer_handoff."""
            if p.agent and "@" in p.agent:
                raise OpError(
                    "not_across_links",
                    f"{p.agent}: an agent spawns only on its own server; a person "
                    "spawns on a linked one with /spawn agent@server",
                )
            agents, default = self._agents()
            parent = reg.sessions.get(caller.log_id) if caller.is_agent else None
            spec = resolve(
                agents,
                default,
                p.agent,
                {
                    "harness": p.harness,
                    "model": p.model,
                    "effort": p.effort,
                    "permission": p.permission,
                },
                self._resolve_cwd(p.cwd, parent.spec.cwd if parent else None),
                spawned_by=parent.log_id if parent else None,
            )
            # An agent cannot hand a session more power than it has itself.
            if parent is not None and PERMISSION_ORDER.index(
                spec.permission
            ) > PERMISSION_ORDER.index(parent.spec.permission):
                raise OpError(
                    "not_allowed",
                    f"your session runs with {parent.spec.permission}, so a session "
                    f"you spawn can have at most {parent.spec.permission} "
                    f"(this one would have {spec.permission}); pass permission",
                )
            try:
                s = await reg.spawn(spec)
            except FileNotFoundError as e:
                raise OpError(
                    "harness_not_found", f"cannot run {self._bin(spec.harness)!r}: {e}"
                ) from e
            except (OSError, TimeoutError) as e:
                raise OpError(
                    "harness_failed", f"{self._bin(spec.harness)!r} did not start: {e}"
                ) from e
            if p.prompt:
                try:
                    await s.send(p.prompt)
                except (BrokenPipeError, ConnectionResetError, FileNotFoundError) as e:
                    raise OpError(
                        "send_failed",
                        f"{s.handle} ({s.log_id}) started, but its first message "
                        f"failed: {e}",
                    ) from e
            return {"log_id": s.log_id, "handle": s.handle}

        @r.op("session.send", SendParams)
        async def send(p: SendParams, caller):
            s = reg.open(p.log_id)
            cmd = commands.split(p.text)
            if cmd is not None:
                if p.attachments:
                    raise OpError(
                        "attachments_with_command",
                        "a / line takes no files; send them with a message",
                    )
                name, arg = cmd
                if name in commands.AEGIS:
                    return await self._command(s, name, arg, caller)
                cat = await self.catalogs.get(s)
                if cat is not None and cat.commands and not cat.has(name):
                    raise OpError(
                        "unknown_command",
                        f"no command /{name} in this session; "
                        "start the line with // to send it as text",
                    )
            text = commands.escape(p.text)
            try:
                # The harness first: a send that cannot reach the agent leaves
                # its uploads staged, to send again.
                await s.ensure_running()
                sent: list[dict] = []
                if p.attachments:
                    try:
                        sent = await asyncio.to_thread(
                            att.commit, self.roots.state_root, p.log_id, p.attachments
                        )
                    except files.FileError as e:
                        raise OpError(e.code, e.message) from e
                await s.send(att.message(text, sent), typed=text, attached=sent)
            except FileNotFoundError as e:
                raise OpError(
                    "harness_not_found",
                    f"cannot run {self._bin(s.spec.harness)!r}: {e}",
                ) from e
            except (BrokenPipeError, ConnectionResetError) as e:
                raise _dead(e) from e

        @r.op("attachment.begin", AttachBegin)
        async def attachment_begin(p: AttachBegin, caller):
            """Start uploading a file to a session: its upload id. The file
            waits, staged, until a session.send names it (attachments.py)."""
            reg.open(p.log_id)
            try:
                uid = att.begin(self.roots.state_root, p.log_id, p.name, p.size)
            except files.FileError as e:
                raise OpError(e.code, e.message) from e
            return {"upload_id": uid}

        @r.op("attachment.put", AttachPut)
        async def attachment_put(p: AttachPut, caller):
            """One base64 chunk of an upload at ``offset``: the bytes now
            staged. No await between reading the staged size and appending,
            so two chunks of one file cannot interleave."""
            reg.open(p.log_id)
            try:
                data = base64.b64decode(p.data, validate=True)
            except binascii.Error as e:
                raise OpError("bad_chunk", f"the chunk is not base64: {e}") from e
            if len(data) > att.CHUNK_BYTES:
                raise OpError(
                    "bad_chunk", f"a chunk carries at most {att.CHUNK_BYTES} bytes"
                )
            try:
                size = att.put(
                    self.roots.state_root, p.log_id, p.upload_id, p.offset, data
                )
            except files.FileError as e:
                raise OpError(e.code, e.message) from e
            return {"size": size}

        @r.op("attachment.drop", AttachDrop)
        async def attachment_drop(p: AttachDrop, caller):
            """Forget an upload the person removed before sending."""
            reg.open(p.log_id)
            try:
                att.drop(self.roots.state_root, p.log_id, p.upload_id)
            except files.FileError as e:
                raise OpError(e.code, e.message) from e

        @r.op("session.read", ReadParams)
        async def read(p: ReadParams, caller):
            """A person read these agent messages, on any browser."""
            s = reg.open(p.log_id)
            n = s.read(p.ids)
            return {"read": n, "unread": len(s.unread)}

        @r.op("recap.request", RecapParams)
        async def recap_request(p: RecapParams, caller):
            """A recap of where the session stands, for a person landing on its tab."""
            return await self.recaps.request(reg.open(p.log_id), p.force)

        @r.op("dictation.prepare", NoParams)
        async def dictation_prepare(p: NoParams, caller):
            """The engine and model the browser transcribes with, on disk and
            verified, and the words to bias it toward (dictation.py)."""
            try:
                await self.dictation.ensure()
            except dictation.Unavailable as e:
                raise OpError("dictation_unavailable", str(e)) from e
            snap = self.config.current()
            live = reg.open_sessions()
            return {
                "base": f"/dictation/{self.dictation.id}/",
                "keywords": dictation.keywords(
                    [s.handle for s in live],
                    [s.spec.cwd for s in live],
                    [*(a.name for a in snap.agents), *snap.queues],
                ),
            }

        @r.op("session.configure", ConfigureParams)
        async def configure(p: ConfigureParams, caller):
            return await self._configure(
                reg.open(p.log_id), p.model, p.effort, p.permission
            )

        @r.op("commands.list", LogParams)
        async def commands_list(p: LogParams, caller):
            """What the composer's menu offers this session."""
            cat = await self.catalogs.get(reg.open(p.log_id))
            return {
                "commands": commands.aegis_wire()
                + (cat.wire_commands(shadowed=commands.AEGIS) if cat else []),
                "models": cat.wire_models() if cat else [],
                "permissions": list(PERMISSION_MODE),
                # False: claude gave no command list, so the menu cannot tell
                # an unknown name from one of claude's.
                "complete": bool(cat and cat.commands),
            }

        @r.op("transcript.detail", DetailParams)
        async def detail(p: DetailParams, caller):
            """The whole entries for rows the wire sent without their detail."""
            return reg.detail(p.log_id, p.ids)

        @r.op("transcript.search", SearchParams)
        async def search(p: SearchParams, caller):
            """The entries whose detail the wire left out hold ``q``: the find
            bar searches the rest in the browser (js/find.js)."""
            return {"ids": reg.search(p.log_id, p.q)}

        @r.op("transcript.output", OutputParams)
        async def output(p: OutputParams, caller):
            """A tool row's whole output, for its copy button: the entry holds
            only the tail. A thread, because it parses the whole store."""
            text = await asyncio.to_thread(reg.output, p.log_id, p.id)
            if text is None:
                raise OpError("no_output", f"no output for {p.id!r}")
            return {"text": text}

        @r.op("session.interrupt", LogParams)
        async def interrupt(p: LogParams, caller):
            try:
                await reg.open(p.log_id).interrupt()
            except (BrokenPipeError, ConnectionResetError) as e:
                raise _dead(e) from e

        @r.op("session.stop", LogParams)
        async def stop(p: LogParams, caller):
            await reg.open(p.log_id).stop()

        @r.op("session.reopen", LogParams)
        async def reopen(p: LogParams, caller):
            return reg.reopen(p.log_id).wire()

        @r.op("session.rename", RenameParams, agent=True)
        async def rename(p: RenameParams, caller):
            """Rename your session: a handle (2 or 3 lowercase segments joined by
            hyphens) and/or a title."""
            log_id = p.log_id
            if caller.is_agent:
                if log_id not in (None, caller.log_id):
                    raise OpError(
                        "not_yours", "agents can rename only their own session"
                    )
                log_id = caller.log_id
            if log_id is None:
                raise OpError("bad_params", "log_id is required")
            return reg.rename(log_id, p.handle, p.title)

        @r.op("archive.list", ArchiveParams)
        async def archive_list(p: ArchiveParams, caller):
            """A page of closed sessions, newest first, across this server and
            the servers it links (archive.py). ``total`` and ``counts`` are of
            the servers this page read; the first page reads every one."""
            return await self._archive(p, caller)

        @r.op("file.open", FileRef)
        async def file_open(p: FileRef, caller):
            """Open a sent file in the desktop's app for it. Only for a person
            whose browser runs on the server's desktop; never an agent tool,
            or an agent could launch apps on that desktop."""
            if not caller.desktop:
                raise OpError(
                    "not_local", "only a browser on the server's desktop can do this"
                )
            path = files.find(self.roots.state_root, p.file_id, p.name)
            if path is None:
                raise OpError("no_file", f"no sent file {p.name!r}")
            try:
                files.open_natively(path)
            except (files.FileError, OSError) as e:
                raise OpError("open_failed", str(e)) from e

        @r.op("file.peek", PeekParams)
        async def file_peek(p: PeekParams, caller):
            """Copy the file a Read, Write or Edit row used, as it is now, and
            open it inside that row. The person's way to see a file the agent
            did not send; the agent never sees it."""
            s = reg.open(p.log_id)
            row = s.fold().entry(p.entry_id)
            raw = row["detail"].get("path") if row and row["kind"] == "tool" else None
            if not raw:
                raise OpError("no_path", "that row used no file")
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = s.spec.cwd / path
            try:
                (rec,) = await asyncio.to_thread(
                    files.store_all, self.roots.state_root, [path]
                )
            except files.FileError as e:
                raise OpError(e.code, e.message) from e
            sent = {k: v for k, v in rec.items() if k != "kind"}
            s.record_peek({"kind": "peek", "entry": p.entry_id, "files": [sent]})
            url = files.url(rec["file_id"], rec["name"])
            return [{"url": url, "name": rec["name"], "size": rec["size"]}]

        @r.op("link.list")
        async def link_list(_, caller):
            """The servers this one links, with their state; never their tokens."""
            return self.links.wire()

        @r.op("link.add", LinkAdd)
        async def link_add(p: LinkAdd, caller):
            """Link another aegis server: connect once with its token, take the
            name it gives itself, and keep the link in links.json."""
            try:
                welcome = await probe(p.url, p.token, self.server_name, self.user)
            except LinkError as e:
                raise OpError("link_refused", e.message) from e
            name = str(welcome.get("server"))
            if p.name and p.name != name:
                raise OpError(
                    "bad_link", f"that server calls itself {name}, not {p.name}"
                )
            self.links.add(name, p.url, p.token)
            return {"name": name}

        @r.op("link.remove", LinkName)
        async def link_remove(p: LinkName, caller):
            self.links.remove(p.name)

        @r.op("server.version")
        async def server_version(_, caller):
            return await self.versions.wire()

        @r.op("quota.read", agent=True)
        async def quota_read(_, caller):
            """How much of each subscription window is left: per provider, each
            window's percent used, severity, projected percent at reset and
            reset time (epoch seconds). Reads the last reading; never asks the
            vendor, so calling it costs nothing."""
            return self.quota.snapshot()
