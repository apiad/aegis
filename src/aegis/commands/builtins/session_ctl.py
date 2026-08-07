"""Session-control slash commands: /rename, /close, and (Tasks 9-10)
/themes, /clear. Thin calls over the bridge; /themes and /clear additionally
carry a CommandResult.effect the frontend seam applies."""
from __future__ import annotations

from aegis.commands import (
    CommandContext, CommandResult, SlashCommand, register,
)
from aegis.commands.args import Arg, ArgSpec, Flag
from aegis.theme_names import THEME_NAMES


def _normalize_theme(name: str) -> str | None:
    if name in THEME_NAMES:
        return name
    prefixed = f"aegis-{name}"
    return prefixed if prefixed in THEME_NAMES else None


async def _themes(ctx: CommandContext, args) -> CommandResult:
    name = args.get("name")
    if name is None or name == "list":
        return CommandResult(True, "themes",
                             "\n".join(f"  {t}" for t in THEME_NAMES))
    full = _normalize_theme(name)
    if full is None:
        return CommandResult(False, f"unknown theme: {name}",
                             "available: " + ", ".join(THEME_NAMES))
    return CommandResult(True, f"theme → {full}",
                         effect={"kind": "theme", "name": full})


async def _clear(ctx: CommandContext, args) -> CommandResult:
    return CommandResult(True, "transcript cleared",
                         effect={"kind": "clear"})


async def _tasks(ctx: CommandContext, args) -> CommandResult:
    """Toggle the task dock. Carried as an effect rather than done here so
    the TUI and the web client both get it through the one dispatch seam
    (the TUI also binds F3)."""
    return CommandResult(True, "task dock toggled",
                         effect={"kind": "tasks"})


async def _rename(ctx: CommandContext, args) -> CommandResult:
    new = args["new"]
    res = await ctx.bridge.rename_handle(ctx.handle, new)
    if isinstance(res, dict) and res.get("error"):
        return CommandResult(False, "rename rejected", res["error"])
    return CommandResult(True, f"renamed {ctx.handle} → {new}")


async def _title(ctx: CommandContext, args) -> CommandResult:
    """Set the session's display label, beside (never instead of) its handle.

    Bare ``/title`` *regenerates* from where the conversation is now —
    which is the point of regenerating rather than re-running the opening
    summary. ``--clear`` drops the title instead, which is also how you
    hand a hand-written one back to auto-titling.
    """
    if args.flags.get("clear"):
        res = await ctx.bridge.set_title(ctx.handle, "", source="human")
        if isinstance(res, dict) and res.get("error"):
            return CommandResult(False, "title rejected", res["error"])
        return CommandResult(True, "title cleared")

    text = args.get("text") or ""
    if not text:
        regen = getattr(ctx.bridge, "regenerate_title", None)
        if regen is None:
            return CommandResult(
                False, "cannot regenerate a title in this frontend",
                "use `/title <text>`, or `/title --clear`")
        res = await regen(ctx.handle)
        if isinstance(res, dict) and res.get("error"):
            return CommandResult(False, "could not regenerate the title",
                                 res["error"])
        return CommandResult(True, f"title → {res.get('title', '')}")

    res = await ctx.bridge.set_title(ctx.handle, text, source="human")
    if isinstance(res, dict) and res.get("error"):
        return CommandResult(False, "title rejected", res["error"])
    stored = res.get("title") if isinstance(res, dict) else text
    return (CommandResult(True, f"title → {stored}") if stored
            else CommandResult(True, "title cleared"))


async def _close(ctx: CommandContext, args) -> CommandResult:
    target = args.get("handle") or ctx.handle
    await ctx.bridge.close(target)
    return CommandResult(True, f"closed {target}")


async def _reconnect(ctx: CommandContext, args) -> CommandResult:
    """Rebuild a dropped remote session's harness, in place."""
    target = args.get("handle") or ctx.handle
    reconnect = getattr(ctx.bridge, "reconnect", None)
    if reconnect is None:
        return CommandResult(
            False, "reconnect is not available in this frontend")
    try:
        return CommandResult(True, await reconnect(target))
    except ValueError as e:
        return CommandResult(False, f"cannot reconnect: {e}")


for _cmd in (
    SlashCommand("rename", "rename the current session",
                 "/rename <new>", _rename,
                 spec=ArgSpec(positionals=(Arg("new"),))),
    SlashCommand("title",
                 "set the session's title (bare: regenerate it)",
                 "/title [text] [--clear]", _title,
                 spec=ArgSpec(positionals=(
                     Arg("text", required=False, greedy=True),),
                     flags=(Flag("clear", takes_value=False),))),
    SlashCommand("close", "close the current or a named session",
                 "/close [handle]", _close,
                 spec=ArgSpec(positionals=(
                     Arg("handle", required=False,
                         completer=lambda b: [
                             (s.handle, f"{s.agent_slug} · {s.state}")
                             for s in b.list_sessions()]),))),
    SlashCommand("reconnect",
                 "rebuild a dropped remote session in place",
                 "/reconnect [handle]", _reconnect,
                 spec=ArgSpec(positionals=(
                     Arg("handle", required=False,
                         completer=lambda b: [
                             (s.handle, f"{s.agent_slug} · @{s.host}")
                             for s in b.list_sessions()
                             if getattr(s, "host", "local") != "local"]),))),
    SlashCommand("themes", "list themes, or switch to one",
                 "/themes [name]", _themes,
                 spec=ArgSpec(positionals=(
                     Arg("name", required=False, completer=THEME_NAMES),))),
    SlashCommand("clear", "clear the visible transcript (cosmetic)",
                 "/clear", _clear),
    SlashCommand("tasks", "show or hide the task dock (also F3)",
                 "/tasks", _tasks),
):
    register(_cmd)
