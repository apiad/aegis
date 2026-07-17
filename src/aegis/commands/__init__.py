"""Slash commands — control commands aegis executes itself, typed into the
input box, never passed to the underlying harness.

A control command is a thin, human-facing front-end over the same
``AppBridge`` surface aegis exposes to agents through MCP. This package is
harness-agnostic (no Textual import) so the web client can reuse ``dispatch``
verbatim; the TUI wires it in ``ConversationPane`` and renders the result.

See ``docs/superpowers/specs/2026-07-16-aegis-slash-commands-design.md``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from aegis.commands.args import Args, ArgError, ArgSpec, parse


@dataclass(frozen=True)
class CommandResult:
    ok: bool          # False → rendered as an error block
    title: str        # one-line headline, e.g. "spawned researcher-1"
    body: str = ""    # optional multi-line detail


@dataclass
class CommandContext:
    """What a command handler is given: the shared capability surface
    (the AegisApp, which implements AppBridge) and the calling pane's
    session handle (recorded as ``spawned_by`` etc.)."""
    bridge: object
    handle: str


Handler = Callable[[CommandContext, Args], Awaitable[CommandResult]]


class CommandCollision(ValueError):
    """A non-builtin command tried to shadow a protected builtin name."""


@dataclass(frozen=True)
class SlashCommand:
    name: str
    summary: str          # one line, shown by /help
    usage: str            # e.g. "/spawn <agent> [prompt]"
    run: Handler
    source: str = "builtin"          # builtin | user | plugin
    spec: ArgSpec = field(default_factory=ArgSpec)


REGISTRY: dict[str, SlashCommand] = {}


def register(cmd: SlashCommand) -> None:
    """Add a command to the registry. Builtins are protected: a non-builtin
    command whose name already exists as a builtin is rejected."""
    existing = REGISTRY.get(cmd.name)
    if (existing is not None and existing.source == "builtin"
            and cmd.source != "builtin"):
        raise CommandCollision(
            f"/{cmd.name} is a builtin and cannot be overridden by a "
            f"{cmd.source} command")
    REGISTRY[cmd.name] = cmd


async def dispatch(text: str, ctx: CommandContext) -> CommandResult:
    """Parse ``/verb rest-of-line``, parse its typed args, run the command.
    Pure (no UI).

    A bare ``/`` is treated as ``/help``. An unknown verb, an ``ArgError`` from
    parsing, or any exception raised by a handler comes back as an error
    ``CommandResult`` — a bad command never crashes the turn loop.
    """
    body = text[1:] if text.startswith("/") else text
    parts = body.split(None, 1)
    verb = parts[0].lower() if parts and parts[0] else "help"
    argstr = parts[1] if len(parts) > 1 else ""
    cmd = REGISTRY.get(verb)
    if cmd is None:
        return CommandResult(False, f"unknown command: /{verb}", "try /help")
    try:
        args = parse(cmd.spec, argstr)
    except ArgError as e:
        return CommandResult(False, f"usage: {cmd.usage}", str(e))
    try:
        return await cmd.run(ctx, args)
    except Exception as e:  # noqa: BLE001 — a bad command must not kill the turn
        return CommandResult(False, f"/{verb} failed", f"{type(e).__name__}: {e}")


# Register the builtins. Import at the bottom so the types above exist when
# builtins.py imports them (avoids a circular import).
from aegis.commands import builtins as _builtins  # noqa: E402,F401
