"""The server's state and its operations, independent of any transport.

Operations: ``agents.list``, ``session.spawn``, ``session.send`` (which
resolves a ``/`` line first, ``commands.py``), ``session.configure``,
``commands.list``, ``session.interrupt``, ``session.stop``, ``session.close``,
``session.reopen``,
``session.rename``, ``archive.list``, ``server.version``, ``file.open``,
``quota.read``. Channels: ``sessions`` (every open session's meta; patches
``upsert`` and ``remove``), ``transcript:<log_id>`` (any session, archived
included), ``quota`` (each provider's windows; patches ``set``) and ``host``
(CPU, RAM and disk while someone watches; patches ``set``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import commands, files
from .agent_ops import register_agent_ops
from .agents import (
    EFFORTS,
    HARNESSES,
    PERMISSION_ORDER,
    SUPPORTED_HARNESSES,
    ConfigError,
    default_agent,
    load_agents,
    model_suggestions,
    resolve,
)
from .channels import Channels, Throttle
from .claude.process import PERMISSION_MODE, ControlError
from .host import HostSampler
from .mcp import PATH as MCP_PATH, Tokens, build_mcp
from .monitors import Monitors
from .queues import Queues
from .quota import Quota
from .ops import OpError, Registry as Ops
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
    text: str = Field(min_length=1)


class ConfigureParams(_Strict):
    log_id: str
    model: str | None = None
    effort: Effort | None = None
    permission: Permission | None = None


class LogParams(_Strict):
    log_id: str


class RenameParams(_Strict):
    log_id: str | None = None
    handle: str | None = None
    title: str | None = None


class FileRef(_Strict):
    file_id: str
    name: str


class ArchiveParams(_Strict):
    query: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    before: float | None = None


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
    ) -> None:
        self.roots = roots
        self.claude_bin = claude_bin
        self.opencode_bin = opencode_bin
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
        )
        self.tokens = Tokens()
        self.monitors = Monitors(self.sessions, roots.state_root / "monitors.json")
        self.queues = Queues(
            self.sessions, self.monitors, roots.state_root / "tasks.jsonl"
        )
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
        self.registry = Ops()
        self._register()
        register_agent_ops(self)
        self.mcp_server, self.mcp_app = build_mcp(self.registry, self.tokens)

    def _bin(self, harness: str) -> str:
        return self.opencode_bin if harness == "opencode" else self.claude_bin

    async def boot(self) -> None:
        self.sessions.boot()
        self.monitors.boot()
        self.monitors.arm_all()
        await self.queues.resume_after_boot(self.queues.boot())
        self.quota.start()
        self.host.start()

    async def shutdown(self) -> None:
        await self.quota.stop()
        await self.host.stop()
        await self.monitors.shutdown()
        await self.sessions.shutdown()

    def publish(self, channel: str, ops: list[dict]) -> None:
        if channel == "sessions":
            self._sessions_out.add(ops)
        else:
            self.channels.publish(channel, ops)

    def _sessions_key(self, op: dict) -> tuple[str, bool]:
        """A session added or removed, or a change to its state, name or model,
        goes out at once: the page acts on them (Esc interrupts only a working
        session, a spawn shows the new tab). The rest of a card can wait."""
        if "remove" in op:
            self._on_wire.pop(op["remove"], None)
            return op["remove"], True
        m = op["upsert"]
        seen = (m["state"], m["title"], m["handle"], m["model"])
        urgent = self._on_wire.get(m["log_id"]) != seen
        self._on_wire[m["log_id"]] = seen
        return m["log_id"], urgent

    def _resolve(self, name: str):
        if name == "sessions":
            return lambda: [s.wire() for s in self.sessions.open_sessions()]
        if name.startswith("transcript:"):
            return self.sessions.transcript(name.removeprefix("transcript:"))
        if name == "quota":
            return self.quota.snapshot
        if name == "host":
            return self.host.snapshot
        return None

    def _agents(self):
        try:
            root = self.roots.config_root
            return load_agents(root), default_agent(root)
        except ConfigError as e:
            raise OpError("bad_config", str(e)) from e

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

    async def _command(self, s, name: str, arg: str):
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
        await reg.close(s.log_id)  # close
        return None

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
            }

        @r.op("session.spawn", SpawnParams, agent=True)
        async def spawn(p: SpawnParams, caller):
            """Start a new session from an agent, overriding its harness, model,
            effort or permission if you need to, and send it `prompt` as its
            first message. Its permission can be at most yours. Returns its log
            id and handle. It does not report back: read it with peer_read,
            message it with peer_handoff."""
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
                name, arg = cmd
                if name in commands.AEGIS:
                    return await self._command(s, name, arg)
                cat = await self.catalogs.get(s)
                if cat is not None and cat.commands and not cat.has(name):
                    raise OpError(
                        "unknown_command",
                        f"no command /{name} in this session; "
                        "start the line with // to send it as text",
                    )
            try:
                await s.send(commands.escape(p.text))
            except FileNotFoundError as e:
                raise OpError(
                    "harness_not_found",
                    f"cannot run {self._bin(s.spec.harness)!r}: {e}",
                ) from e
            except (BrokenPipeError, ConnectionResetError) as e:
                raise _dead(e) from e

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

        @r.op("session.interrupt", LogParams)
        async def interrupt(p: LogParams, caller):
            try:
                await reg.open(p.log_id).interrupt()
            except (BrokenPipeError, ConnectionResetError) as e:
                raise _dead(e) from e

        @r.op("session.stop", LogParams)
        async def stop(p: LogParams, caller):
            await reg.open(p.log_id).stop()

        @r.op("session.close", LogParams)
        async def close(p: LogParams, caller):
            await reg.close(p.log_id)

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
            return reg.archive(p.query, p.limit, p.before)

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
