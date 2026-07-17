"""Session-control slash commands: /rename, /close, and (Tasks 9-10)
/themes, /clear. Thin calls over the bridge; /themes and /clear additionally
carry a CommandResult.effect the frontend seam applies."""
from __future__ import annotations

from aegis.commands import (
    CommandContext, CommandResult, SlashCommand, register,
)
from aegis.commands.args import Arg, ArgSpec
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


async def _rename(ctx: CommandContext, args) -> CommandResult:
    new = args["new"]
    res = await ctx.bridge.rename_handle(ctx.handle, new)
    if isinstance(res, dict) and res.get("error"):
        return CommandResult(False, "rename rejected", res["error"])
    return CommandResult(True, f"renamed {ctx.handle} → {new}")


async def _close(ctx: CommandContext, args) -> CommandResult:
    target = args.get("handle") or ctx.handle
    await ctx.bridge.close(target)
    return CommandResult(True, f"closed {target}")


for _cmd in (
    SlashCommand("rename", "rename the current session",
                 "/rename <new>", _rename,
                 spec=ArgSpec(positionals=(Arg("new"),))),
    SlashCommand("close", "close the current or a named session",
                 "/close [handle]", _close,
                 spec=ArgSpec(positionals=(Arg("handle", required=False),))),
    SlashCommand("themes", "list themes, or switch to one",
                 "/themes [name]", _themes,
                 spec=ArgSpec(positionals=(Arg("name", required=False),))),
    SlashCommand("clear", "clear the visible transcript (cosmetic)",
                 "/clear", _clear),
):
    register(_cmd)
