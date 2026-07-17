"""/terminals — thin calls over the shared TerminalManager. `run` blocks
until the command finishes (matching aegis_term_run) and returns its output."""
from __future__ import annotations

from aegis.commands import (
    CommandContext, CommandResult, SlashCommand, register,
)
from aegis.commands.args import Arg, ArgSpec


async def _terminals(ctx: CommandContext, args) -> CommandResult:
    tm = ctx.bridge.terminal_manager
    sub = args.get("subverb")
    if sub in (None, "list"):
        infos = tm.list()
        if not infos:
            return CommandResult(True, "no terminals")
        lines = [f"  {i.name} · pid {i.pid} · {i.shell}" for i in infos]
        return CommandResult(True, f"{len(infos)} terminal"
                             f"{'' if len(infos) == 1 else 's'}",
                             "\n".join(lines))
    name = args.get("name")
    if not name:
        return CommandResult(False,
                             "usage: /terminals new|run|close <name> …")
    if sub == "new":
        info = await tm.spawn(name=name)
        return CommandResult(True, f"terminal {name} started",
                             f"pid {info.pid} · {info.shell}")
    if sub == "close":
        await tm.close(name)
        return CommandResult(True, f"terminal {name} closed")
    if sub == "run":
        cmd = args.get("cmd")
        if not cmd:
            return CommandResult(False, "usage: /terminals run <name> <cmd>")
        rec = await tm.run(name, cmd, writer=ctx.handle)
        head = f"{name}$ {rec.cmd} · exit {rec.exit}"
        return CommandResult(rec.exit == 0, head, rec.stdout.rstrip())
    return CommandResult(False, "usage: /terminals [new|run|close <name> …]")


for _cmd in (
    SlashCommand("terminals", "list terminals, or new/run/close one",
                 "/terminals [new <name> | run <name> <cmd> | close <name>]",
                 _terminals,
                 spec=ArgSpec(positionals=(
                     Arg("subverb", required=False,
                         completer=("list", "new", "run", "close")),
                     Arg("name", required=False,
                         completer=lambda b: [i.name
                                              for i in b.terminal_manager.list()]),
                     Arg("cmd", required=False, greedy=True)))),
):
    register(_cmd)
