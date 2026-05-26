"""Command registry for the Telegram frontend.

Each chat command is a `Command` registered at import time. The
frontend's dispatcher looks up the verb in `COMMANDS` and calls the
handler with a `CmdContext` carrying the bridge, config, session
manager, optional @peer target, and a reply callable.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CmdContext:
    """Context passed to every command handler.

    bridge:   the _PlaneBridge from cli.py (queue_manager, scheduler, ...)
    cfg:      the AegisConfig (cfg.remotes for @peer routing)
    manager:  the SessionManager (live session lookup, spawn, close)
    target:   the @peer name parsed from the user's command, or None
    reply:    async callable to send text back to the chat
    frontend: the TelegramFrontend instance — only used by the five
              migrated verbs (/new, /close, /interrupt, etc.) that
              mutate the active-session pointer. New commands should
              not touch this.
    """
    bridge:   Any
    cfg:      Any
    manager:  Any
    target:   str | None
    reply:    Callable[[str], Awaitable[None]]
    frontend: Any


@dataclass(frozen=True)
class Command:
    """One registered chat command.

    name:    full verb-plus-subcommand string, e.g. "queue list"
             or just "new" for bare-verb commands.
    summary: one-line description shown in `/help`.
    detail:  multi-line description shown in `/help <name>`.
    handler: async function called by the dispatcher.
    """
    name:    str
    summary: str
    detail:  str
    handler: Callable[[CmdContext, list[str]], Awaitable[None]]


COMMANDS: dict[str, Command] = {}


def register(cmd: Command) -> Command:
    """Register a command at import time. Duplicates fail loud."""
    if cmd.name in COMMANDS:
        raise ValueError(f"duplicate Telegram command {cmd.name!r}")
    COMMANDS[cmd.name] = cmd
    return cmd


def resolve_remote(ctx: CmdContext) -> tuple[str, Any] | None:
    """Look up ctx.target in cfg.remotes. Returns (target_name, spec)
    on success, None when ctx.target is None (local execution), and
    raises a custom marker if ctx.target is set but unknown — handler
    should reply with an error.
    """
    if ctx.target is None:
        return None
    remotes = getattr(ctx.cfg, "remotes", {}) or {}
    if ctx.target not in remotes:
        return None
    return ctx.target, remotes[ctx.target]
