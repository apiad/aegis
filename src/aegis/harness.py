"""A harness is the agent CLI a session runs: Claude Code, OpenCode or Codex.

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

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .claude.control import Catalog
    from .session import SpawnSpec


def _no_error(text: str, idle: bool, line: str | None) -> None:
    return None


# Above the 0 the server and unrelated user services run at, so in a host OOM
# the kernel kills a session's tree before the server that holds every session
# (#360). An unprivileged process may raise a score, never lower it.
OOM_SCORE_ADJ = 500


def raise_oom_score(pid: int) -> None:
    """Mark a harness child as the OOM killer's first choice.

    The server writes ``/proc/<pid>/oom_score_adj`` as soon as the spawn
    returns, rather than a ``preexec_fn`` in the child, because a
    ``preexec_fn`` is not safe in a process with threads. By then the child
    has exec'd and is still starting up, so everything it spawns later (tools,
    MCP servers, test runs) inherits the score. Off Linux there is no such
    file, and nothing happens.
    """
    try:
        Path(f"/proc/{pid}/oom_score_adj").write_text(f"{OOM_SCORE_ADJ}\n")
    except OSError:
        pass


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
    # Folders outside the cwd the agent may read: its inbox (attachments.py).
    read_dirs: tuple[Path, ...] = ()
    # The session's cgroup the child joins before it execs (cgroups.py), or
    # None when the server's unit is not delegated.
    cgroup: Path | None = None
    # The session's log id, exported to the child as AEGIS_SESSION so park.strays
    # can find its descendants after they leave its process tree.
    log_id: str | None = None
    # Harness tools the agent keeps although aegis replaces them
    # (claude/process.py REPLACED_TOOLS); other harnesses ignore it.
    keep_harness_tools: tuple[str, ...] = ()
    # The Linux user the process runs as, in multi-user mode (launch.py).
    user: str | None = None
    # The owner's knowledge setting is native: the harness starts without its
    # own skills and memory, which aegis's knowledge tools replace.
    knowledge_native: bool = False
    # The linked server this process reaches the internet through (exit.py).
    via: str | None = None


SESSION_VAR = "AEGIS_SESSION"
URL_VAR = "AEGIS_MCP_URL"
# The MCP header that carries a session's token (mcp.py, call.py).
HEADER = "X-Aegis-Session"
# Sent by ``aegis call`` alone: its output is a program's input, so no notice
# rides on it (notices.py).
SHELL_HEADER = "X-Aegis-Shell"
TOKEN_VAR = "AEGIS_SESSION_TOKEN"


def session_env(
    launch: "Launch", base: Mapping[str, str] | None = None
) -> dict[str, str] | None:
    """The child's environment: ``base`` (the server's own when None) with
    AEGIS_SESSION naming the session. Without a log id, ``base`` unchanged, and
    None (inherit) when there is no base either."""
    if launch.log_id is None:
        return None if base is None else dict(base)
    env = {**(os.environ if base is None else base), SESSION_VAR: launch.log_id}
    if launch.mcp is not None:
        # `aegis call` reads these: the same endpoint and token as the MCP tools.
        env[URL_VAR], env[TOKEN_VAR] = launch.mcp
    return env


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
    # Reads a message sent mid-turn inside that turn (Session.deliver's urgent).
    steers_mid_turn: bool
    # Takes a pending notice that way at each tool call, instead of on the
    # result of an aegis tool. Each one needs a run with a real model first: a
    # harness that hands a steer on as its own message may have it read as a
    # new request.
    steers_notices: bool
    bin: str

    def process(self, launch: Launch) -> Process: ...
    async def probe(self, spec: SpawnSpec, stderr_path: Path) -> Catalog: ...


def harness_for(
    name: str,
    claude_bin: str,
    opencode_bin: str,
    codex_bin: str = "codex",
    cursor_bin: str = "cursor-agent",
) -> Harness:
    if name == "claude-code":
        from .claude.harness import ClaudeCode

        return ClaudeCode(claude_bin)
    if name == "opencode":
        from .opencode.harness import OpenCode

        return OpenCode(opencode_bin)
    if name == "codex":
        from .codex.harness import Codex

        return Codex(codex_bin)
    if name == "cursor":
        from .cursor.harness import Cursor

        return Cursor(cursor_bin)
    raise ValueError(f"no harness named {name!r}")
