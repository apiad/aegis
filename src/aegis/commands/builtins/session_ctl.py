"""Session-control slash commands: /rename, /close, and (Tasks 9-10)
/themes, /clear. Thin calls over the bridge; /themes and /clear additionally
carry a CommandResult.effect the frontend seam applies."""
from __future__ import annotations

from aegis.commands import (
    CommandContext, CommandResult, SlashCommand, register,
)
from aegis.commands.args import Arg, ArgSpec


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
):
    register(_cmd)
