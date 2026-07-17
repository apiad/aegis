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
    effect: dict | None = None   # frontend-applied side-effect, or None


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


def classify_input(text: str) -> "tuple[str, str]":
    """Route an input line for the slash family. ``//foo`` is a literal
    message ``/foo`` (one slash stripped); a single leading ``/`` is a
    command; anything else is a plain message. The TUI's ``!`` shell escape
    is handled before this call and is not represented here."""
    if text.startswith("//"):
        return "message", text[1:]
    if text.startswith("/"):
        return "command", text
    return "message", text


# --- palette completion --------------------------------------------------

@dataclass(frozen=True)
class Completion:
    insert: str       # text spliced into the input for this choice
    label: str        # matched text shown (e.g. "/spawn" or "opus")
    detail: str = ""  # dim right-column (summary / agent config / "")


@dataclass(frozen=True)
class Completions:
    items: tuple[Completion, ...] = ()
    hint: str = ""


def _usage_hint(spec, bound: int) -> str:
    parts = []
    for i, p in enumerate(spec.positionals):
        token = f"<{p.name}>" if p.required else f"[{p.name}]"
        parts.append(token if i >= bound else f"·{p.name}")
    return " ▸ ".join(parts)


def _norm_choice(ch) -> "tuple[str, str]":
    if isinstance(ch, tuple):
        return ch[0], (ch[1] if len(ch) > 1 else "")
    return ch, ""


def complete(text: str, bridge: object) -> Completions:
    """Return completion candidates for the current input. Pure; never raises.
    Empty items when ``text`` is not a slash command."""
    from aegis.commands.fuzzy import fuzzy_rank

    if not text.startswith("/"):
        return Completions()
    body = text[1:]
    if " " not in body:
        # still typing the verb
        ranked = fuzzy_rank(body, list(REGISTRY.values()), key=lambda c: c.name)
        ranked.sort(key=lambda c: 0 if c.source == "builtin" else 1)
        items = tuple(
            Completion(insert=f"/{c.name} ", label=f"/{c.name}",
                       detail=c.summary)
            for c in ranked)
        return Completions(items=items)

    parts = body.split(None, 1)
    verb = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    cmd = REGISTRY.get(verb.lower())
    if cmd is None:
        return Completions()

    trailing_space = text.endswith(" ")
    toks = rest.split()
    partial = "" if trailing_space or not toks else toks[-1]
    bound_toks = toks if trailing_space else toks[:-1]
    positional_bound = sum(1 for t in bound_toks if not t.startswith("--"))
    spec = cmd.spec
    hint = _usage_hint(spec, positional_bound)

    # flag completion (checked first — flags may trail any positional)
    if partial.startswith("--"):
        names = [f"--{f.name}" for f in spec.flags]
        ranked = fuzzy_rank(partial[2:], names, key=lambda n: n[2:])
        return Completions(
            items=tuple(Completion(insert=n + " ", label=n) for n in ranked),
            hint=hint)

    # positional value completion
    if positional_bound >= len(spec.positionals):
        return Completions(hint=hint)
    arg = spec.positionals[positional_bound]
    if arg.greedy or arg.completer is None:
        return Completions(hint=hint)
    try:
        raw = (arg.completer if isinstance(arg.completer, tuple)
               else arg.completer(bridge))
        choices = [_norm_choice(ch) for ch in raw]
    except Exception:              # noqa: BLE001 — a bad completer must not break typing
        return Completions(hint=hint)
    ranked = fuzzy_rank(partial, choices, key=lambda vd: vd[0])
    items = tuple(Completion(insert=f"{v} ", label=v, detail=d)
                  for v, d in ranked)
    return Completions(items=items, hint=hint)


# Register the builtins. Import at the bottom so the types above exist when
# builtins.py imports them (avoids a circular import).
from aegis.commands import builtins as _builtins  # noqa: E402,F401
