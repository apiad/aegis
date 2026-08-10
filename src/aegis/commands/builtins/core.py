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
from aegis.config.edit import _VALID_PROVIDERS


def _agent_choices(bridge) -> list:
    """Palette completer for an agent-slug argument: slug + config detail
    (harness · model · permission) when the bridge exposes the agent map."""
    cfgs = getattr(bridge, "_agents", {}) or {}
    out: list = []
    for name in bridge.list_agents():
        a = cfgs.get(name)
        if a is None:
            out.append(name)
            continue
        perm = getattr(getattr(a, "permission", ""), "value",
                       getattr(a, "permission", "")) or "?"
        out.append((name, f"{getattr(a, 'harness', '?')} · "
                          f"{getattr(a, 'model', '?')} · {perm}"))
    return out


def _agent_at_host_choices(bridge) -> list:
    """Agent slugs, plus one ``slug@host`` entry per configured host.

    Any harness runs on any host, so the palette offers the product rather
    than assuming a profile is bound to a machine.
    """
    out = list(_agent_choices(bridge))
    hosts = sorted(getattr(bridge, "_hosts", {}) or {})
    if not hosts:
        return out
    for name in bridge.list_agents():
        for h in hosts:
            out.append((f"{name}@{h}", f"{name} · on {h}"))
    return out


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


async def _spawn_opening(ctx: CommandContext, prompt: str) -> str:
    """The new agent's first turn: the operator's words, plus where they
    were standing when they typed them.

    `/spawn opus please verify this test` used to hand a fresh agent three
    words and no referent — the one row of the context-carrying table
    (handoff / @peer / fork / spawn) that arrived blind. This closes it
    with `@peer`'s machinery: a bounded tail of the source transcript and
    a pointer to `aegis_read_peer` for the rest.

    ``read_peer`` is reused rather than growing a second bridge method:
    both ``AppBridge`` implementations already have it and both are
    already correct, and the TUI's ``peer_ask`` shipped a degraded path
    three times by being a second copy of a call ``SessionManager`` got
    right.

    Every failure returns the bare prompt. The preamble rides on the
    tail — a pane whose first input is this `/spawn`, a bridge with no
    ``read_peer``, a damaged log — because provenance pointing at a
    transcript nobody can read buys the new agent a failed tool call and
    a paragraph of confusion.
    """
    from aegis.peer import (
        TEASER_BUDGET_TOKENS, TEASER_ITEM_CHARS, TEASER_MAX_TURNS,
        compose_spawn,
    )
    read = getattr(ctx.bridge, "read_peer", None)
    if read is None or not ctx.handle:
        return prompt
    try:
        # The TEASER pair, not `read_peer`'s own READ defaults: this is a
        # push, and the READ budget measured 95,346 chars of preamble on
        # a real 410KB transcript. `test_read_peer_takes_the_same_window_
        # knobs_on_both_bridges` pins both bridges accepting them, since
        # the except below would otherwise turn a signature drift into a
        # silently missing preamble in one frontend.
        window = await read(ctx.handle, TEASER_MAX_TURNS,
                            budget_tokens=TEASER_BUDGET_TOKENS,
                            item_chars=TEASER_ITEM_CHARS)
    except Exception:                                         # noqa: BLE001
        return prompt
    if not window.get("ok") or not window.get("text"):
        return prompt
    slug = next((s.agent_slug for s in ctx.bridge.list_sessions()
                 if s.handle == ctx.handle), "")
    return compose_spawn(source=ctx.handle, slug=slug or "unknown",
                         prompt=prompt, tail=window["text"],
                         header=window.get("header", ""))


async def _spawn(ctx: CommandContext, args) -> CommandResult:
    from aegis.hosts.errors import HostError
    from aegis.hosts.resolve import parse_at_host

    # `<agent>@<host>[:<cwd>]` — host and cwd are per-session placement,
    # exactly like --model and --effort, and equally unpersisted.
    agent, host, cwd = parse_at_host(args["agent"])
    prompt = args.get("prompt")
    agents = ctx.bridge.list_agents()
    if agent not in agents:
        return CommandResult(False, f"unknown agent: {agent}",
                             "available: " + ", ".join(agents))
    model = args.get("model")
    effort = args.get("effort")
    # The confirmation line below echoes `prompt`, not this — printing the
    # whole composed preamble back into the source pane would bury the
    # three words the operator actually typed.
    opening = await _spawn_opening(ctx, prompt) if prompt else None
    try:
        handle = await ctx.bridge.spawn(agent, opening_prompt=opening,
                                        spawned_by=ctx.handle,
                                        model=model, effort=effort,
                                        host=host, cwd=cwd)
    except HostError as e:
        return CommandResult(False, f"cannot spawn on {host!r}", str(e))
    detail = f"agent {agent}" + (f" · prompt: {prompt}" if prompt else "")
    if model:
        detail += f" · model: {model}"
    if effort:
        detail += f" · effort: {effort}"
    if host:
        detail += f" · host: {host}"
    if cwd:
        detail += f" · cwd: {cwd}"
    return CommandResult(True, f"spawned {handle}", detail)


async def _fork(ctx: CommandContext, args) -> CommandResult:
    """Branch this pane's conversation into a new tab.

    Legal here where it would not be over MCP: a slash command is served
    by aegis rather than sent to the agent as a turn, so the pane is idle
    and there is no half-written turn to branch from.
    """
    prompt = args.get("prompt")
    try:
        handle = await ctx.bridge.fork(
            ctx.handle, prompt=prompt, slug=args.get("slug"),
            model=args.get("model"), effort=args.get("effort"),
            forked_by=ctx.handle)
    except ValueError as e:                  # the guard's refusal reasons
        return CommandResult(False, f"cannot fork: {e}")
    detail = f"branched from {ctx.handle}"
    if prompt:
        detail += f" · prompt: {prompt}"
    return CommandResult(True, f"forked {handle}", detail)


# Handles already told that `text_generation:` is unset. The warning is
# worth saying once and tiresome after that.
_WARNED_UNSET: set[str] = set()


async def _btw(ctx: CommandContext, args) -> CommandResult:
    """Answer a side question inline, from this pane's own transcript tail.

    Legal mid-turn where `/fork` is not: `/btw` never touches the harness
    session. It reads aegis's own log and makes one independent call, so
    there is no half-written turn for it to branch from.
    """
    prompt = args.get("prompt")
    if not prompt:
        return CommandResult(False, "usage: /btw <question>",
                             "a bare /btw is a typo, not a request")
    note = await ctx.bridge.side_note(ctx.handle, prompt)
    if not note.ok:
        return CommandResult(False, "side note failed",
                             note.error or "no answer")
    body = [note.footer] if note.footer else []
    if note.needs_more:
        body.append(f"answered from {note.header} — `/fork` if you want it "
                    f"to actually go look.")
    if getattr(note, "billed_to_session_profile", False) \
            and ctx.handle not in _WARNED_UNSET:
        _WARNED_UNSET.add(ctx.handle)
        body.append("`text_generation:` is unset in .aegis.yaml — this side "
                    "note billed at your session's own model. Point it at a "
                    "cheap profile.")
    # The effect carries the note so a frontend can give it its own
    # treatment — as a plain dict, because the web seam ships `effect`
    # straight out as JSON and a dataclass there would break `/btw` on the
    # web client only. title/body stay populated so any frontend that
    # ignores the effect still shows the answer rather than nothing.
    from dataclasses import asdict
    return CommandResult(True, note.answer, "\n".join(body),
                         effect={"kind": "side_note", "note": asdict(note)})


def _peer_targets(bridge) -> list:
    """Palette completer for a peer handle.

    Busy peers are marked rather than hidden: a busy target is refused, so
    the constraint is worth seeing at type-time instead of hitting it as a
    rejection at send-time.
    """
    return [(s.handle,
             f"{s.agent_slug} · busy" if s.state != "ready" else s.agent_slug)
            for s in bridge.list_sessions()]


async def _peer(ctx: CommandContext, args) -> CommandResult:
    """Ask an idle peer a question, from where you're standing.

    Legal while *this* pane is mid-turn — the guard reads the target, and
    spending a long turn's dead time on an idle peer is the point.
    """
    target = args.get("handle")
    prompt = args.get("prompt")
    if not target:
        return CommandResult(False, "usage: /peer <handle> <question>",
                             "which peer?")
    if not prompt:
        return CommandResult(False, "usage: /peer <handle> <question>",
                             "a peer ask with no question is a typo")
    answer = await ctx.bridge.peer_ask(ctx.handle, target, prompt,
                                       cc=bool(args.get("cc")))
    if not answer.ok:
        return CommandResult(False, f"@{target} could not answer",
                             answer.error or "no answer")
    from dataclasses import asdict
    return CommandResult(True, answer.answer, answer.footer,
                         effect={"kind": "peer_answer",
                                 "answer": asdict(answer)})


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
                     positionals=(
                         Arg("subverb", required=False,
                             completer=("list", "add", "remove")),
                         Arg("slug", required=False),
                         Arg("harness", required=False,
                             completer=tuple(sorted(_VALID_PROVIDERS))),
                         Arg("model", required=False)),
                     flags=(Flag("effort"), Flag("permission")))),
    SlashCommand("spawn", "start a new agent, from where you're standing",
                 "/spawn <agent>[@host[:cwd]] [prompt] "
                 "[--model M] [--effort E]", _spawn,
                 spec=ArgSpec(
                     positionals=(
                         Arg("agent", completer=_agent_at_host_choices),
                         Arg("prompt", required=False, greedy=True)),
                     flags=(Flag("model"), Flag("effort")))),
    SlashCommand("fork", "branch this conversation into a new tab",
                 "/fork [prompt] [--slug S] [--model M] [--effort E]", _fork,
                 spec=ArgSpec(
                     positionals=(
                         Arg("prompt", required=False, greedy=True),),
                     flags=(Flag("slug"), Flag("model"), Flag("effort")))),
    SlashCommand("btw", "answer a side question from the recent window",
                 "/btw <question>", _btw,
                 spec=ArgSpec(
                     positionals=(
                         Arg("prompt", required=False, greedy=True),)),
                 # Never awaited in a frontend's input handler: the call
                 # takes 12-17s, and in Textual that would hold the pane's
                 # message pump for all of it. "A side note that doesn't
                 # cost a conversation" was true about money and false
                 # about attention until this flag existed.
                 #
                 # The default cancel_note is right here: cancelling a
                 # /btw really is clean, because it never touched a
                 # harness session, so nothing happened anywhere.
                 deferred=True),
    SlashCommand("peer", "ask an idle peer, from where you're standing",
                 "/peer <handle> [--cc] <question>", _peer,
                 spec=ArgSpec(
                     positionals=(
                         Arg("handle", required=False,
                             completer=_peer_targets),
                         Arg("prompt", required=False, greedy=True)),
                     flags=(Flag("cc", takes_value=False),)),
                 deferred=True,
                 # "cancelled" would be a lie. By the time the operator
                 # hits ESC the peer has already taken the turn; it runs
                 # to completion and lands in its own transcript whether
                 # or not anyone is still listening. Nothing was
                 # cancelled — you stopped waiting.
                 cancel_note="stopped waiting — {handle}'s turn is still "
                             "running, so go read its tab"),
    SlashCommand("queues", "list or create queues",
                 "/queues [new <name> [agent] [--ephemeral]]", _queue,
                 spec=ArgSpec(
                     positionals=(
                         Arg("subverb", required=False,
                             completer=("list", "new")),
                         Arg("name", required=False),
                         Arg("agent", required=False,
                             completer=_agent_choices)),
                     flags=(Flag("ephemeral", takes_value=False),))),
    SlashCommand("enqueue", "drop a task on a queue",
                 "/enqueue <queue> <payload>", _enqueue,
                 spec=ArgSpec(positionals=(
                     Arg("queue",
                         completer=lambda b: b.queue_manager.list_queues()),
                     Arg("payload", greedy=True)))),
):
    register(_cmd)
