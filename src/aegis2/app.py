"""The server's state and its operations, independent of any transport.

Operations: ``profiles.list``, ``session.spawn``, ``session.send``,
``session.interrupt``, ``session.stop``, ``session.close``, ``session.reopen``,
``session.rename``, ``archive.list``. Channels: ``sessions`` (every open
session's meta; patches ``upsert`` and ``remove``) and ``transcript:<log_id>``
(any session, archived included).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .channels import Channels
from .ops import OpError, Registry as Ops
from .profiles import ProfileError, default_profile, load_profiles
from .registry import Registry
from .roots import Roots
from .session import SpawnSpec

Effort = Literal["low", "medium", "high", "max"]
Permission = Literal["read", "write", "full", "auto"]


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class SpawnParams(_Strict):
    profile: str
    cwd: str | None = None
    model: str | None = None
    effort: Effort | None = None
    permission: Permission | None = None


class SendParams(_Strict):
    log_id: str
    text: str = Field(min_length=1)


class LogParams(_Strict):
    log_id: str


class RenameParams(_Strict):
    log_id: str
    handle: str | None = None
    title: str | None = None


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
        self, roots: Roots, claude_bin: str = "claude", interrupt_timeout: float = 10.0
    ) -> None:
        self.roots = roots
        self.claude_bin = claude_bin
        self.channels = Channels(self._resolve)
        self.sessions = Registry(roots, self.publish, claude_bin, interrupt_timeout)
        self.registry = Ops()
        self._register()

    def boot(self) -> None:
        self.sessions.boot()

    async def shutdown(self) -> None:
        await self.sessions.shutdown()

    def publish(self, channel: str, ops: list[dict]) -> None:
        self.channels.publish(channel, ops)

    def _resolve(self, name: str):
        if name == "sessions":
            return lambda: [s.wire() for s in self.sessions.open_sessions()]
        if name.startswith("transcript:"):
            return self.sessions.transcript(name.removeprefix("transcript:"))
        return None

    def _resolve_cwd(self, raw: str | None) -> Path:
        base = self.roots.harness_cwd
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

        @r.op("profiles.list")
        async def profiles_list(_):
            try:
                profiles = load_profiles(self.roots.config_root)
                default = default_profile(self.roots.config_root)
            except ProfileError as e:
                raise OpError("bad_config", str(e)) from e
            return {
                "profiles": [p.as_dict() for p in profiles],
                "default": default,
                "cwd": str(self.roots.harness_cwd),
            }

        @r.op("session.spawn", SpawnParams)
        async def spawn(p: SpawnParams):
            try:
                profiles = {x.name: x for x in load_profiles(self.roots.config_root)}
            except ProfileError as e:
                raise OpError("bad_config", str(e)) from e
            prof = profiles.get(p.profile)
            if prof is None:
                raise OpError("unknown_profile", f"no profile named {p.profile!r}")
            if not prof.enabled:
                raise OpError(
                    "harness_unsupported", f"{prof.harness} is not supported yet"
                )
            spec = SpawnSpec(
                profile=prof.name,
                model=p.model or prof.model,
                effort=p.effort or prof.effort,
                permission=p.permission or prof.permission,
                cwd=self._resolve_cwd(p.cwd),
            )
            try:
                s = await reg.spawn(spec)
            except FileNotFoundError as e:
                raise OpError(
                    "claude_not_found", f"cannot run {self.claude_bin!r}: {e}"
                ) from e
            return {"log_id": s.log_id, "handle": s.handle}

        @r.op("session.send", SendParams)
        async def send(p: SendParams):
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
        async def interrupt(p: LogParams):
            try:
                await reg.open(p.log_id).interrupt()
            except (BrokenPipeError, ConnectionResetError) as e:
                raise _dead(e) from e

        @r.op("session.stop", LogParams)
        async def stop(p: LogParams):
            await reg.open(p.log_id).stop()

        @r.op("session.close", LogParams)
        async def close(p: LogParams):
            await reg.close(p.log_id)

        @r.op("session.reopen", LogParams)
        async def reopen(p: LogParams):
            return reg.reopen(p.log_id).wire()

        @r.op("session.rename", RenameParams)
        async def rename(p: RenameParams):
            return reg.rename(p.log_id, p.handle, p.title)

        @r.op("archive.list", ArchiveParams)
        async def archive_list(p: ArchiveParams):
            return reg.archive(p.query, p.limit, p.before)
