"""The server's state and its operations, independent of any transport.

Operations: ``agents.list``, ``session.spawn``, ``session.send``,
``session.interrupt``, ``session.stop``, ``session.close``, ``session.reopen``,
``session.rename``, ``archive.list``, ``server.version``, ``file.open``. Channels: ``sessions`` (every open
session's meta; patches ``upsert`` and ``remove``) and ``transcript:<log_id>``
(any session, archived included).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import files
from .agent_ops import register_agent_ops
from .agents import (
    HARNESSES,
    SUPPORTED_HARNESSES,
    ConfigError,
    default_agent,
    load_agents,
    model_suggestions,
    resolve,
)
from .channels import Channels
from .mcp import PATH as MCP_PATH, Tokens, build_mcp
from .monitors import Monitors
from .queues import Queues
from .ops import OpError, Registry as Ops
from .registry import Registry
from .roots import Roots
from .version import Versions

Effort = Literal["low", "medium", "high", "max"]
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
        f"claude stopped while being written to: {e}; send again to resume",
    )


class App:
    def __init__(
        self,
        roots: Roots,
        claude_bin: str = "claude",
        interrupt_timeout: float = 10.0,
        base_url: str | None = None,
        server_name: str = "aegis",
    ) -> None:
        self.roots = roots
        self.claude_bin = claude_bin
        self.channels = Channels(self._resolve)
        self.sessions = Registry(roots, self.publish, claude_bin, interrupt_timeout)
        self.tokens = Tokens()
        self.monitors = Monitors(self.sessions, roots.state_root / "monitors.json")
        self.queues = Queues(
            self.sessions, self.monitors, roots.state_root / "tasks.jsonl"
        )
        reg = self.sessions
        reg.tokens, reg.monitors, reg.queues, reg.server_name = (
            self.tokens,
            self.monitors,
            self.queues,
            server_name,
        )
        reg.mcp_url = f"{base_url.rstrip('/')}{MCP_PATH}" if base_url else None
        self.versions = Versions()
        self.registry = Ops()
        self._register()
        register_agent_ops(self)
        self.mcp_server, self.mcp_app = build_mcp(self.registry, self.tokens)

    async def boot(self) -> None:
        self.sessions.boot()
        self.monitors.boot()
        self.monitors.arm_all()
        await self.queues.resume_after_boot(self.queues.boot())

    async def shutdown(self) -> None:
        await self.monitors.shutdown()
        await self.sessions.shutdown()

    def publish(self, channel: str, ops: list[dict]) -> None:
        self.channels.publish(channel, ops)

    def _resolve(self, name: str):
        if name == "sessions":
            return lambda: [s.wire() for s in self.sessions.open_sessions()]
        if name.startswith("transcript:"):
            return self.sessions.transcript(name.removeprefix("transcript:"))
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
            first message. Returns its log id and handle. It does not report
            back: read it with peer_read, message it with peer_handoff."""
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
            try:
                s = await reg.spawn(spec)
            except FileNotFoundError as e:
                raise OpError(
                    "claude_not_found", f"cannot run {self.claude_bin!r}: {e}"
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
            try:
                await s.send(p.text)
            except FileNotFoundError as e:
                raise OpError(
                    "claude_not_found", f"cannot run {self.claude_bin!r}: {e}"
                ) from e
            except (BrokenPipeError, ConnectionResetError) as e:
                raise _dead(e) from e

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
