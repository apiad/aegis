"""Phase 1 builtin slash commands: /help, /sessions, /agents, /spawn, /queue,
/enqueue.

Each is a thin call into the ``AppBridge`` (``ctx.bridge``) — the same
surface agents drive through MCP. Handlers receive a validated ``Args``
(parsed from the command's declared ``ArgSpec`` by ``dispatch``).
"""
from __future__ import annotations

from aegis.commands import (
    REGISTRY, CommandContext, CommandResult, SlashCommand, register,
)
from aegis.commands.args import Arg, ArgSpec, Flag


async def _help(ctx: CommandContext, args) -> CommandResult:
    order = ["builtin", "user", "plugin"]
    by_source: dict[str, list] = {}
    for c in REGISTRY.values():
        by_source.setdefault(c.source, []).append(c)
    show_headers = len(by_source) > 1
    lines: list[str] = []
    for src in order + [s for s in by_source if s not in order]:
        cmds = by_source.pop(src, None)
        if not cmds:
            continue
        if show_headers:
            lines.append(f"[{src}]")
        for c in sorted(cmds, key=lambda c: c.name):
            lines.append(f"{c.usage} — {c.summary}")
    return CommandResult(True, "commands", "\n".join(lines))


async def _sessions(ctx: CommandContext, args) -> CommandResult:
    sessions = list(ctx.bridge.list_sessions())
    if not sessions:
        return CommandResult(True, "no live sessions")
    lines = [f"{'*' if s.active else ' '} {s.handle} · {s.agent_slug} · "
             f"{s.state}" for s in sessions]
    plural = "" if len(sessions) == 1 else "s"
    return CommandResult(True, f"{len(sessions)} session{plural}",
                         "\n".join(lines))


async def _agents(ctx: CommandContext, args) -> CommandResult:
    sub = args.get("subverb")
    if sub in (None, "list"):
        return _agents_list(ctx)
    if sub == "add":
        return await _agents_add(ctx, args)
    if sub == "remove":
        return await _agents_remove(ctx, args)
    return CommandResult(
        False, "usage: /agents [add <slug> <harness> <model> "
        "[--effort E] [--permission P] | remove <slug>]")


def _agents_list(ctx: CommandContext) -> CommandResult:
    names = ctx.bridge.list_agents()
    if not names:
        return CommandResult(True, "no agents configured")
    # Enrich each with its config (harness · model · permission) when the
    # bridge exposes the agent map; fall back to bare names otherwise.
    configs = getattr(ctx.bridge, "_agents", {}) or {}
    lines = []
    for name in names:
        a = configs.get(name)
        if a is None:
            lines.append(f"  {name}")
            continue
        harness = getattr(a, "harness", "") or "?"
        model = getattr(a, "model", "") or "?"
        perm = getattr(a, "permission", "")
        perm = getattr(perm, "value", perm) or "?"
        lines.append(f"  {name} · {harness} · {model} · {perm}")
    plural = "" if len(names) == 1 else "s"
    return CommandResult(True, f"{len(names)} agent{plural}", "\n".join(lines))


async def _agents_add(ctx: CommandContext, args) -> CommandResult:
    slug = args.get("slug")
    harness = args.get("harness")
    model = args.get("model")
    if not (slug and harness and model):
        return CommandResult(False,
                             "usage: /agents add <slug> <harness> <model>"
                             " [--effort E] [--permission P]")
    import aegis.config as cfg
    import aegis.config.edit as cfg_edit
    root = cfg.find_project_root()
    if root is None:
        return CommandResult(False, "no .aegis.yaml found")
    effort = args.get("effort")
    permission = args.get("permission")
    try:
        cfg_edit.add_agent(root, slug, provider=harness, model=model,
                           effort=effort, permission=permission)
    except cfg.ConfigError as e:
        return CommandResult(False, f"agent rejected: {e}")
    kw = {"harness": harness, "model": model}
    if effort is not None:
        kw["effort"] = effort
    if permission is not None:
        kw["permission"] = permission
    try:
        ctx.bridge.register_agent(slug, cfg.Agent(**kw))
    except Exception as e:                                    # noqa: BLE001
        return CommandResult(True, f"agent {slug} saved",
                             f"persisted to .aegis.yaml; restart to activate "
                             f"(live register failed: {e})")
    return CommandResult(True, f"agent {slug} added",
                         f"{harness} · {model} · persisted + hot-registered")


async def _agents_remove(ctx: CommandContext, args) -> CommandResult:
    slug = args.get("slug")
    if not slug:
        return CommandResult(False, "usage: /agents remove <slug>")
    import aegis.config as cfg
    import aegis.config.edit as cfg_edit
    root = cfg.find_project_root()
    if root is None:
        return CommandResult(False, "no .aegis.yaml found")
    try:
        cfg_edit.remove_agent(root, slug)
    except cfg.ConfigError as e:
        return CommandResult(False, f"cannot remove agent: {e}")
    return CommandResult(True, f"agent {slug} removed",
                         "persisted to .aegis.yaml; restart to drop the live "
                         "profile")


async def _spawn(ctx: CommandContext, args) -> CommandResult:
    agent = args["agent"]
    prompt = args.get("prompt")
    agents = ctx.bridge.list_agents()
    if agent not in agents:
        return CommandResult(False, f"unknown agent: {agent}",
                             "available: " + ", ".join(agents))
    handle = await ctx.bridge.spawn(agent, opening_prompt=prompt,
                                    spawned_by=ctx.handle)
    detail = f"agent {agent}" + (f" · prompt: {prompt}" if prompt else "")
    return CommandResult(True, f"spawned {handle}", detail)


async def _queue(ctx: CommandContext, args) -> CommandResult:
    sub = args.get("subverb")
    if sub is None:                       # bare /queues → list
        qm = ctx.bridge.queue_manager
        names = qm.list_queues()
        if not names:
            return CommandResult(True, "no queues configured")
        lines = []
        for n in names:
            q = qm._queues.get(n)
            if q is None:
                lines.append(f"  {n}")
            else:
                lines.append(f"  {n} · {q.agent_profile} · "
                             f"max_parallel {q.max_parallel}")
        plural = "" if len(names) == 1 else "s"
        return CommandResult(True, f"{len(names)} queue{plural}",
                             "\n".join(lines))
    if sub != "new":
        return CommandResult(
            False, "usage: /queues new <name> [agent] [--ephemeral]")
    name = args.get("name")
    if not name:
        return CommandResult(
            False, "usage: /queues new <name> [agent] [--ephemeral]")
    agents = ctx.bridge.list_agents()
    agent = args.get("agent") or (agents[0] if agents else "")
    if not agent:
        return CommandResult(False, "no agent available for the queue")
    if agent not in agents:
        return CommandResult(False, f"unknown agent: {agent}",
                             "available: " + ", ".join(agents))

    if args.flags.get("ephemeral"):
        from aegis.queue import Queue
        q = Queue(name=name, agent_profile=agent, max_parallel=1)
        try:
            ctx.bridge.register_queue(q)
        except ValueError as e:
            return CommandResult(False, f"queue rejected: {e}")
        return CommandResult(True, f"queue {name} created (ephemeral)",
                             f"agent {agent} · max_parallel 1")

    # persist to .aegis.yaml, then hot-register from the reloaded config
    import aegis.config as cfg
    import aegis.config.edit as cfg_edit
    root = cfg.find_project_root()
    if root is None:
        return CommandResult(
            False, "no .aegis.yaml found",
            "run /queues new … --ephemeral for a session-only queue")
    try:
        cfg_edit.add_queue(root, name, agent=agent, max_parallel=1)
    except cfg.ConfigError as e:
        return CommandResult(False, f"queue rejected: {e}")
    try:
        fresh = cfg.load_queues(root)[name]
        ctx.bridge.register_queue(fresh)
    except Exception as e:                                    # noqa: BLE001
        return CommandResult(
            True, f"queue {name} saved",
            f"persisted to .aegis.yaml; restart to activate "
            f"(live register failed: {e})")
    return CommandResult(True, f"queue {name} created",
                         f"agent {agent} · persisted to .aegis.yaml")


async def _enqueue(ctx: CommandContext, args) -> CommandResult:
    queue = args["queue"]
    payload = args["payload"]
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
    SlashCommand("agents", "list or manage agents",
                 "/agents [add <slug> <harness> <model> "
                 "[--effort E] [--permission P] | remove <slug>]", _agents,
                 spec=ArgSpec(
                     positionals=(Arg("subverb", required=False),
                                  Arg("slug", required=False),
                                  Arg("harness", required=False),
                                  Arg("model", required=False)),
                     flags=(Flag("effort"), Flag("permission")))),
    SlashCommand("spawn", "start a new top-level agent",
                 "/spawn <agent> [prompt]", _spawn,
                 spec=ArgSpec(positionals=(
                     Arg("agent"),
                     Arg("prompt", required=False, greedy=True)))),
    SlashCommand("queues", "list or create queues",
                 "/queues [new <name> [agent] [--ephemeral]]", _queue,
                 spec=ArgSpec(
                     positionals=(Arg("subverb", required=False),
                                  Arg("name", required=False),
                                  Arg("agent", required=False)),
                     flags=(Flag("ephemeral", takes_value=False),))),
    SlashCommand("enqueue", "drop a task on a queue",
                 "/enqueue <queue> <payload>", _enqueue,
                 spec=ArgSpec(positionals=(
                     Arg("queue"), Arg("payload", greedy=True)))),
):
    register(_cmd)
