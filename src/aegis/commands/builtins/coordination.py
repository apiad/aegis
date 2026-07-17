"""Coordination slash commands: /groups, /schedules — thin calls over the
groups bridge and the scheduler-push helpers."""
from __future__ import annotations

from aegis.commands import (
    CommandContext, CommandResult, SlashCommand, register,
)
from aegis.commands.args import Arg, ArgSpec


async def _groups(ctx: CommandContext, args) -> CommandResult:
    g = ctx.bridge.groups
    sub = args.get("subverb")
    if sub in (None, "list"):
        rows = g.list_groups()
        if not rows:
            return CommandResult(True, "no live groups")
        lines = [f"  {r['name']} · {r['members']} member"
                 f"{'' if r['members'] == 1 else 's'}" for r in rows]
        return CommandResult(True, f"{len(rows)} group"
                             f"{'' if len(rows) == 1 else 's'}",
                             "\n".join(lines))
    name = args.get("name")
    if not name:
        return CommandResult(False, "usage: /groups status|dissolve <name>")
    if sub == "status":
        st = await g.status(name)
        members = ", ".join(f"{m['handle']}({m['profile']})"
                            for m in st.get("members", [])) or "none"
        return CommandResult(True, f"group {name}", f"members: {members}")
    if sub == "dissolve":
        await g.dissolve(name)
        return CommandResult(True, f"group {name} dissolved")
    return CommandResult(False, "usage: /groups [status|dissolve <name>]")


for _cmd in (
    SlashCommand("groups", "list groups, or status/dissolve one",
                 "/groups [status|dissolve <name>]", _groups,
                 spec=ArgSpec(positionals=(Arg("subverb", required=False),
                                           Arg("name", required=False)))),
):
    register(_cmd)
