"""The server's state and its operations, independent of any transport.

Slice 1 holds at most one session. Its operations: ``profiles.list``,
``session.spawn``, ``session.send``, ``session.interrupt``,
``session.close``. Its channels: ``session`` (the live session or null) and
``transcript:<log_id>``.

The ``session`` channel's patch ops are ``{"set": {...}}``, a shallow merge of
changed fields, and ``{"replace": value}``, used when a session appears or
goes away.
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .channels import Channels
from .claude.process import build_argv
from .ops import OpError, Registry
from .profiles import ProfileError, default_profile, load_profiles
from .roots import Roots
from .session import Session, SpawnSpec
from .transcript.store import Store

Effort = Literal["low", "medium", "high", "max"]
Permission = Literal["read", "write", "full", "auto"]


class SpawnParams(BaseModel):
    model_config = {"extra": "forbid"}
    profile: str
    cwd: str | None = None
    model: str | None = None
    effort: Effort | None = None
    permission: Permission | None = None


class SendParams(BaseModel):
    model_config = {"extra": "forbid"}
    log_id: str
    text: str = Field(min_length=1)


class LogParams(BaseModel):
    model_config = {"extra": "forbid"}
    log_id: str


def mint_log_id() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


class App:
    def __init__(
        self, roots: Roots, claude_bin: str = "claude", interrupt_timeout: float = 10.0
    ) -> None:
        self.roots = roots
        self.claude_bin = claude_bin
        self.interrupt_timeout = interrupt_timeout
        self.session: Session | None = None
        self.registry = Registry()
        self.channels = Channels(self._resolve)
        self._register()

    # -- channels ----------------------------------------------------------
    def _resolve(self, name: str):
        if name == "session":
            return lambda: self.session.meta() if self.session else None
        if name.startswith("transcript:"):
            s = self.session
            if s is not None and name == s.channel:
                return s.entries
        return None

    def publish(self, channel: str, ops: list[dict]) -> None:
        self.channels.publish(channel, ops)

    # -- operations --------------------------------------------------------
    def _live(self, log_id: str) -> Session:
        s = self.session
        if s is None or s.log_id != log_id:
            raise OpError("no_session", f"no live session {log_id!r}")
        return s

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
            if self.session is not None:
                raise OpError("session_live", "close the live session first")
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
            log_id = mint_log_id()
            state = self.roots.state_root
            session = Session(
                log_id=log_id,
                spec=spec,
                store=Store(state / "transcripts" / f"{log_id}.jsonl"),
                argv=build_argv(
                    self.claude_bin, spec.model, spec.effort, spec.permission
                ),
                stderr_path=state / "stderr" / f"{log_id}.log",
                publish=self.publish,
                interrupt_timeout=self.interrupt_timeout,
            )
            self.session = session
            self.publish("session", [{"replace": session.meta()}])
            try:
                await session.start()
            except FileNotFoundError as e:
                session.store.close()
                self.session = None
                self.publish("session", [{"replace": None}])
                raise OpError(
                    "claude_not_found", f"cannot run {self.claude_bin!r}: {e}"
                ) from e
            return {"log_id": log_id}

        @r.op("session.send", SendParams)
        async def send(p: SendParams):
            s = self._live(p.log_id)
            try:
                await s.send(p.text)
            except (BrokenPipeError, ConnectionResetError) as e:
                raise OpError(
                    "session_dead", "claude is not running; close this session"
                ) from e

        @r.op("session.interrupt", LogParams)
        async def interrupt(p: LogParams):
            s = self._live(p.log_id)
            try:
                await s.interrupt()
            except (BrokenPipeError, ConnectionResetError) as e:
                raise OpError(
                    "session_dead", "claude is not running; close this session"
                ) from e

        @r.op("session.close", LogParams)
        async def close(p: LogParams):
            s = self._live(p.log_id)
            await s.close()
            self.session = None
            self.publish("session", [{"replace": None}])

    async def shutdown(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
