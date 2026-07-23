"""`/loop` — arm a looping instruction on this pane's session.

The instruction is re-delivered at every turn boundary where the session
would otherwise settle idle, until the agent reaps it with aegis_loop_stop,
the iteration cap is reached, or the operator stops it.
"""
from __future__ import annotations

from aegis.commands import (
    CommandContext, CommandResult, SlashCommand, register,
)
from aegis.commands.args import Arg, ArgSpec, Flag
from aegis.core.loop import DEFAULT_MAX_ITERATIONS


def _describe(status: dict) -> str:
    return (f"{status['text']}\n"
            f"  iteration {status['iteration']}/{status['max_iterations']}")


async def _loop(ctx: CommandContext, args) -> CommandResult:
    svc = getattr(ctx.bridge, "loop_service", None)
    if svc is None:
        return CommandResult(False, "loops not available here")

    text = (args.get("text") or "").strip()

    # Status.
    if not text:
        res = svc.status(from_handle=ctx.handle)
        if "error" in res:
            return CommandResult(False, "/loop failed", res["error"])
        status = res["loop"]
        if status is None:
            return CommandResult(True, "no loop armed")
        return CommandResult(True, "loop armed", _describe(status))

    # Reap. Exact match only, so `/loop stop the dev server` still arms.
    if text == "stop":
        res = svc.stop(from_handle=ctx.handle, reason="stopped by the operator")
        if "error" in res:
            return CommandResult(False, "/loop failed", res["error"])
        if not res["stopped"]:
            return CommandResult(False, "no loop armed")
        return CommandResult(True, "loop stopped")

    # Arm.
    had = svc.status(from_handle=ctx.handle).get("loop")
    res = svc.arm(from_handle=ctx.handle, text=text,
                  max_iterations=args.get("max") or DEFAULT_MAX_ITERATIONS)
    if "error" in res:
        return CommandResult(False, "/loop failed", res["error"])
    verb = "loop replaced" if had else "loop armed"
    return CommandResult(
        True, f"{verb} — max {res['max_iterations']} iterations", res["text"])


register(SlashCommand(
    "loop",
    "repeat an instruction until the agent says it's done",
    "/loop [--max N] <instruction> | /loop | /loop stop",
    _loop,
    spec=ArgSpec(
        positionals=(Arg("text", required=False, greedy=True),),
        flags=(Flag("max"),))))
