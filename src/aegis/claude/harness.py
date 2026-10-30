"""Claude Code behind the harness interface: ``claude -p`` in stream-json.

A prompt and a slash command are both a user message: Claude runs a line as a
command when its first character is ``/``. Model, effort and permission change
live through control requests (``control.py``).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import os

from ..harness import Launch, session_env
from . import login
from . import control
from .control import Catalog
from .process import (
    NATIVE_TOOLS,
    NO_AUTO_MEMORY,
    REPLACED_TOOLS,
    ClaudeProcess,
    build_argv,
)

if TYPE_CHECKING:
    from ..session import SpawnSpec

_SETTERS = {
    "model": control.set_model,
    "effort": control.set_effort,
    "permission": control.set_permission,
}


class ClaudeSession:
    def __init__(self, bin: str, launch: Launch) -> None:
        mcp_config = None
        disallowed: tuple[str, ...] = ()
        if launch.mcp is not None:
            from ..mcp import mcp_config as build

            mcp_config = build(launch.mcp[0])
            # Without aegis's tools there is nothing to replace them with.
            keep = set(launch.keep_harness_tools)
            disallowed = tuple(t for t in REPLACED_TOOLS if t not in keep)
            if launch.knowledge_native:
                disallowed += tuple(NATIVE_TOOLS)
        env = session_env(launch)
        if launch.knowledge_native:
            env = {**(os.environ if env is None else env), **NO_AUTO_MEMORY}
        argv = build_argv(
            bin,
            launch.model,
            launch.effort,
            launch.permission,
            launch.resume_id,
            **({"mcp_config": mcp_config} if mcp_config else {}),
            system_prompt=launch.system_prompt,
            add_dirs=launch.read_dirs,
            disallowed=disallowed,
        )
        self._proc = ClaudeProcess(
            argv,
            launch.cwd,
            launch.stderr_path,
            launch.on_line,
            launch.on_exit,
            env=login.with_token(env, launch.user),
            cgroup=launch.cgroup,
            user=launch.user,
            via=launch.via,
        )

    @property
    def pid(self) -> int | None:
        return self._proc.pid

    @property
    def running(self) -> bool:
        return self._proc.running

    @property
    def session_id(self) -> str | None:
        return None  # Claude names it in its first init line

    async def start(self) -> None:
        await self._proc.start()

    async def send(self, text: str) -> None:
        await self._proc.write(
            {"type": "user", "message": {"role": "user", "content": text}}
        )

    async def interrupt(self) -> None:
        await self._proc.write(
            {
                "type": "control_request",
                "request_id": f"aegis_interrupt_{time.monotonic_ns()}",
                "request": {"subtype": "interrupt"},
            }
        )

    async def set(self, kind: str, value: str) -> None:
        await _SETTERS[kind](self._proc, value)

    async def catalog(self) -> Catalog:
        return await control.catalog(self._proc)

    async def terminate(self) -> None:
        await self._proc.terminate()


class ClaudeCode:
    name = "claude-code"
    src = "claude"
    label = "Claude Code"
    steers_mid_turn = True  # a user message read at the next tool boundary
    steers_notices = True
    tool_prefix = "mcp__aegis__"

    def __init__(self, bin: str) -> None:
        self.bin = bin

    def process(self, launch: Launch) -> ClaudeSession:
        return ClaudeSession(self.bin, launch)

    async def probe(self, spec: SpawnSpec, stderr_path: Path) -> Catalog:
        return await control.probe(
            self.bin, spec.model, spec.effort, spec.permission, spec.cwd, stderr_path
        )
