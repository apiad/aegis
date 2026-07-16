"""Phase 1 builtin slash commands: /help, /sessions, /spawn, /queue, /enqueue.

Each is a thin call into the ``AppBridge`` (``ctx.bridge``) — the same
surface agents drive through MCP.
"""
from __future__ import annotations

from aegis.commands import (
    REGISTRY, CommandContext, CommandResult, SlashCommand, register,
)


async def _help(ctx: CommandContext, argstr: str) -> CommandResult:
    lines = [f"{c.usage} — {c.summary}"
             for _, c in sorted(REGISTRY.items())]
    return CommandResult(True, "commands", "\n".join(lines))


async def _sessions(ctx: CommandContext, argstr: str) -> CommandResult:
    sessions = list(ctx.bridge.list_sessions())
    if not sessions:
        return CommandResult(True, "no live sessions")
    lines = [f"{'*' if s.active else ' '} {s.handle} · {s.agent_slug} · "
             f"{s.state}" for s in sessions]
    plural = "" if len(sessions) == 1 else "s"
    return CommandResult(True, f"{len(sessions)} session{plural}",
                         "\n".join(lines))


async def _spawn(ctx: CommandContext, argstr: str) -> CommandResult:
    parts = argstr.split(None, 1)
    if not parts:
        return CommandResult(False, "usage: /spawn <agent> [prompt]")
    agent = parts[0]
    prompt = parts[1] if len(parts) > 1 else None
    agents = ctx.bridge.list_agents()
    if agent not in agents:
        return CommandResult(False, f"unknown agent: {agent}",
                             "available: " + ", ".join(agents))
    handle = await ctx.bridge.spawn(agent, opening_prompt=prompt,
                                    spawned_by=ctx.handle)
    detail = f"agent {agent}" + (f" · prompt: {prompt}" if prompt else "")
    return CommandResult(True, f"spawned {handle}", detail)


async def _queue(ctx: CommandContext, argstr: str) -> CommandResult:
    parts = argstr.split()
    if len(parts) < 2 or parts[0] != "new":
        return CommandResult(False, "usage: /queue new <name> [agent]")
    name = parts[1]
    agents = ctx.bridge.list_agents()
    agent = parts[2] if len(parts) > 2 else (agents[0] if agents else "")
    if not agent:
        return CommandResult(False, "no agent available for the queue")
    if agent not in agents:
        return CommandResult(False, f"unknown agent: {agent}",
                             "available: " + ", ".join(agents))
    from aegis.queue import Queue
    q = Queue(name=name, agent_profile=agent, max_parallel=1)
    try:
        ctx.bridge.register_queue(q)
    except ValueError as e:
        return CommandResult(False, f"queue rejected: {e}")
    return CommandResult(True, f"queue {name} created",
                         f"agent {agent} · max_parallel 1")


async def _enqueue(ctx: CommandContext, argstr: str) -> CommandResult:
    parts = argstr.split(None, 1)
    if len(parts) < 2:
        return CommandResult(False, "usage: /enqueue <queue> <payload>")
    queue, payload = parts[0], parts[1]
    from aegis.queue import sender_user
    try:
        result = ctx.bridge.queue_manager.enqueue(
            queue, payload, enqueued_by=sender_user(), callback=False)
    except KeyError as e:
        return CommandResult(False, f"unknown queue: {e.args[0]!r}")
    if isinstance(result, dict):
        return CommandResult(False, "enqueue failed", str(result))
    tid, pos = result
    return CommandResult(True, f"queued task {tid}",
                         f"queue {queue} · position {pos}")


for _cmd in (
    SlashCommand("help", "list slash commands", "/help", _help),
    SlashCommand("sessions", "list live agent sessions", "/sessions",
                 _sessions),
    SlashCommand("spawn", "start a new top-level agent",
                 "/spawn <agent> [prompt]", _spawn),
    SlashCommand("queue", "create a queue", "/queue new <name> [agent]",
                 _queue),
    SlashCommand("enqueue", "drop a task on a queue",
                 "/enqueue <queue> <payload>", _enqueue),
):
    register(_cmd)
