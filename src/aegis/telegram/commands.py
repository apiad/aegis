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


# ── existing verbs migrated into the registry ──────────────────


async def _cmd_new(ctx: CmdContext, args: list[str]) -> None:
    slug = args[0] if args else None
    try:
        core = ctx.manager._sync_spawn(slug)
    except KeyError:
        agent_list = ", ".join(ctx.manager.list_agents())
        await ctx.reply(f"unknown agent. agents: {agent_list}")
        return
    ctx.frontend._active = core.handle
    await ctx.reply(f"▸ spawned {core.handle} ({core.agent_slug})")


register(Command(
    name="new",
    summary="/new [slug] — spawn a new agent session",
    detail=(
        "/new [agent-slug]\n\n"
        "Spawn a new agent session. With no arg, uses the default "
        "agent profile. The new session becomes the active session "
        "for bare-text routing. Use /agents to list available profiles."
    ),
    handler=_cmd_new,
))


async def _cmd_close(ctx: CmdContext, args: list[str]) -> None:
    fe = ctx.frontend
    if fe._active is None:
        await ctx.reply("no active agent")
        return
    closed = fe._active
    await ctx.manager.close(closed)
    rest_sessions = ctx.manager.list_sessions()
    fe._active = rest_sessions[0].handle if rest_sessions else None
    tail = f"active: {fe._active}" if fe._active else "no active agent"
    await ctx.reply(f"▸ closed {closed} · {tail}")


register(Command(
    name="close",
    summary="/close — close the active session",
    detail=(
        "/close\n\n"
        "Close the currently-active agent session. If other sessions "
        "exist, the first one becomes active. Otherwise the active "
        "pointer clears."
    ),
    handler=_cmd_close,
))


async def _cmd_interrupt(ctx: CmdContext, args: list[str]) -> None:
    fe = ctx.frontend
    if fe._active is not None:
        await ctx.manager.interrupt(fe._active)
        await ctx.reply(f"▸ interrupted {fe._active}")


register(Command(
    name="interrupt",
    summary="/interrupt — interrupt the active session's current turn",
    detail=(
        "/interrupt\n\n"
        "Stop the active session's in-progress turn. Equivalent to "
        "pressing Escape in the TUI. The session stays open; you can "
        "send another message immediately."
    ),
    handler=_cmd_interrupt,
))


async def _cmd_agents(ctx: CmdContext, args: list[str]) -> None:
    agent_list = ", ".join(ctx.manager.list_agents())
    await ctx.reply(f"agents: {agent_list}")


register(Command(
    name="agents",
    summary="/agents — list available agent profiles",
    detail=(
        "/agents\n\n"
        "List the agent profiles declared in .aegis.py. Use one of "
        "these names as the slug argument to /new."
    ),
    handler=_cmd_agents,
))


async def _cmd_sessions(ctx: CmdContext, args: list[str]) -> None:
    sessions = ctx.manager.list_sessions()
    if not sessions:
        await ctx.reply("no sessions")
        return
    # One per line; /underscore_alias is tappable in Telegram (which
    # only auto-links [A-Za-z0-9_]+) and routes back via the _ -> -
    # normalization in _legacy_handle_alias.
    lines = [
        f"{'●' if s.state == 'working' else '○'} "
        f"/{s.handle.replace('-', '_')} {s.state}"
        for s in sessions
    ]
    await ctx.reply("\n".join(lines))


register(Command(
    name="sessions",
    summary="/sessions — list active sessions",
    detail=(
        "/sessions\n\n"
        "List all active agent sessions with their state (working / "
        "ready). Each handle is rendered as /handle_with_underscores "
        "so Telegram makes it tappable; the dispatcher normalizes back "
        "to the real hyphenated handle."
    ),
    handler=_cmd_sessions,
))


async def _cmd_help(ctx: CmdContext, args: list[str]) -> None:
    if not args:
        # Bare /help: group by resource (first whitespace token).
        groups: dict[str, list[Command]] = {}
        for cmd in COMMANDS.values():
            resource = cmd.name.split(" ", 1)[0]
            groups.setdefault(resource, []).append(cmd)
        lines = ["Aegis Telegram commands (/help <name> for detail):", ""]
        for resource in sorted(groups):
            cmds = sorted(groups[resource], key=lambda c: c.name)
            for cmd in cmds:
                lines.append(f"  /{cmd.name} — {cmd.summary}")
            lines.append("")
        # Drop trailing blank
        if lines and lines[-1] == "":
            lines.pop()
        await ctx.reply("\n".join(lines))
        return

    # /help <name> — try exact match first, then prefix match.
    needle = " ".join(args)
    if needle in COMMANDS:
        cmd = COMMANDS[needle]
        await ctx.reply(f"/{cmd.name}\n\n{cmd.detail}")
        return

    matching = [c for c in COMMANDS.values()
                if c.name == needle or c.name.startswith(needle + " ")]
    if matching:
        lines = [f"commands matching {needle!r}:", ""]
        for cmd in sorted(matching, key=lambda c: c.name):
            lines.append(f"  /{cmd.name} — {cmd.summary}")
        await ctx.reply("\n".join(lines))
        return

    await ctx.reply(f"no such command {needle!r}; /help to list all")


register(Command(
    name="help",
    summary="/help [name] — list commands, or show detail for one",
    detail=(
        "/help [name]\n\n"
        "With no argument, lists every registered command grouped by "
        "resource. With a command name (`/help new`), prints the "
        "command's full detail. With a resource prefix "
        "(`/help queue`), lists every subcommand under that resource."
    ),
    handler=_cmd_help,
))
