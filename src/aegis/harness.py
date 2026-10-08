"""A harness is the agent CLI a session runs: Claude Code or OpenCode.

``Session`` keeps the store, the fold, the status, the inbox and the card.
Everything it asks of the agent's process goes through ``Process``, and the
fold reads every stored line through the parser its ``src`` tag names
(``transcript.entries.PARSERS``). Adding a harness adds a module behind these
two, never a branch in ``Session``.

The host decides what a process starts with (the MCP URL and this session's
token, the system prompt) and hands it over as a ``Launch``; the harness only
uses it, because how a token reaches the CLI is the harness's business
(``--mcp-config`` for Claude, ``OPENCODE_CONFIG_CONTENT`` for OpenCode).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .claude.control import Catalog
    from .session import SpawnSpec


def _no_error(text: str, idle: bool, line: str | None) -> None:
    return None


@dataclass(frozen=True)
class Launch:
    cwd: Path
    model: str
    effort: str
    permission: str
    resume_id: str | None
    # The aegis MCP endpoint and this session's token; None runs without aegis.
    mcp: tuple[str, str] | None
    system_prompt: str | None
    stderr_path: Path
    on_line: Callable[[str], None]
    on_exit: Callable[[int, list[str]], None]
    # A request that failed outside the event stream: its text, whether it
    # means no turn is running, and the line the person typed, if any.
    on_error: Callable[[str, bool, str | None], None] = _no_error


class Process(Protocol):
    @property
    def pid(self) -> int | None: ...
    @property
    def running(self) -> bool: ...
    @property
    def session_id(self) -> str | None:
        """The harness's id for the conversation, once the process knows it."""
        ...

    async def start(self) -> None: ...
    async def send(self, text: str) -> None: ...
    async def interrupt(self) -> None: ...
    async def set(self, kind: str, value: str) -> None:
        """Change ``model``, ``effort`` or ``permission``; raises
        ``claude.process.ControlError`` when the harness refuses."""
        ...

    async def catalog(self) -> Catalog: ...
    async def terminate(self) -> None: ...


class Harness(Protocol):
    name: str
    src: str
    label: str
    tool_prefix: str
    bin: str

    def process(self, launch: Launch) -> Process: ...
    async def probe(self, spec: SpawnSpec, stderr_path: Path) -> Catalog: ...


def harness_for(name: str, claude_bin: str, opencode_bin: str) -> Harness:
    if name == "claude-code":
        from .claude.harness import ClaudeCode

        return ClaudeCode(claude_bin)
    raise ValueError(f"no harness named {name!r}")
