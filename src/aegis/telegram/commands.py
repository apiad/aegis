"""Command registry for the Telegram frontend.

Each chat command is a `Command` registered at import time. The
frontend's dispatcher looks up the verb in `COMMANDS` and calls the
handler with a `CmdContext` carrying the bridge, config, session
manager, optional @peer target, and a reply callable.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CmdContext:
    """Context passed to every command handler.

    bridge:   the _PlaneBridge from cli.py (queue_manager, scheduler, ...)
    cfg:      the AegisConfig (cfg.remotes for @peer routing)
    manager:  the SessionManager (live session lookup, spawn, close)
    target:   the @peer name parsed from the user's command, or None
    reply:    async callable to send text back to the chat
    frontend: the TelegramFrontend instance — only used by the five
              migrated verbs (/new, /close, /interrupt, etc.) that
              mutate the active-session pointer. New commands should
              not touch this.
    """
    bridge:   Any
    cfg:      Any
    manager:  Any
    target:   str | None
    reply:    Callable[[str], Awaitable[None]]
    frontend: Any


@dataclass(frozen=True)
class Command:
    """One registered chat command.

    name:    full verb-plus-subcommand string, e.g. "queue list"
             or just "new" for bare-verb commands.
    summary: one-line description shown in `/help`.
    detail:  multi-line description shown in `/help <name>`.
    handler: async function called by the dispatcher.
    """
    name:    str
    summary: str
    detail:  str
    handler: Callable[[CmdContext, list[str]], Awaitable[None]]


COMMANDS: dict[str, Command] = {}


def register(cmd: Command) -> Command:
    """Register a command at import time. Duplicates fail loud."""
    if cmd.name in COMMANDS:
        raise ValueError(f"duplicate Telegram command {cmd.name!r}")
    COMMANDS[cmd.name] = cmd
    return cmd


def resolve_remote(ctx: CmdContext) -> tuple[str, Any] | None:
    """Look up ctx.target in cfg.remotes. Returns (target_name, spec)
    on success, None when ctx.target is None (local execution), and
    raises a custom marker if ctx.target is set but unknown — handler
    should reply with an error.
    """
    if ctx.target is None:
        return None
    remotes = getattr(ctx.cfg, "remotes", {}) or {}
    if ctx.target not in remotes:
        return None
    return ctx.target, remotes[ctx.target]


# ── existing verbs migrated into the registry ──────────────────


async def _cmd_new(ctx: CmdContext, args: list[str]) -> None:
    slug = args[0] if args else None
    try:
        core = ctx.manager._sync_spawn(slug)
    except KeyError:
        agent_list = ", ".join(ctx.manager.list_agents())
        await ctx.reply(f"unknown agent. agents: {agent_list}")
        return
    ctx.frontend._active = core.handle
    await ctx.reply(f"▸ spawned {core.handle} ({core.agent_slug})")


register(Command(
    name="new",
    summary="/new [slug] — spawn a new agent session",
    detail=(
        "/new [agent-slug]\n\n"
        "Spawn a new agent session. With no arg, uses the default "
        "agent profile. The new session becomes the active session "
        "for bare-text routing. Use /agents to list available profiles."
    ),
    handler=_cmd_new,
))


async def _cmd_close(ctx: CmdContext, args: list[str]) -> None:
    fe = ctx.frontend
    if fe._active is None:
        await ctx.reply("no active agent")
        return
    closed = fe._active
    await ctx.manager.close(closed)
    rest_sessions = ctx.manager.list_sessions()
    fe._active = rest_sessions[0].handle if rest_sessions else None
    tail = f"active: {fe._active}" if fe._active else "no active agent"
    await ctx.reply(f"▸ closed {closed} · {tail}")


register(Command(
    name="close",
    summary="/close — close the active session",
    detail=(
        "/close\n\n"
        "Close the currently-active agent session. If other sessions "
        "exist, the first one becomes active. Otherwise the active "
        "pointer clears."
    ),
    handler=_cmd_close,
))


async def _cmd_interrupt(ctx: CmdContext, args: list[str]) -> None:
    fe = ctx.frontend
    if fe._active is not None:
        await ctx.manager.interrupt(fe._active)
        await ctx.reply(f"▸ interrupted {fe._active}")


register(Command(
    name="interrupt",
    summary="/interrupt — interrupt the active session's current turn",
    detail=(
        "/interrupt\n\n"
        "Stop the active session's in-progress turn. Equivalent to "
        "pressing Escape in the TUI. The session stays open; you can "
        "send another message immediately."
    ),
    handler=_cmd_interrupt,
))


async def _cmd_agents(ctx: CmdContext, args: list[str]) -> None:
    agent_list = ", ".join(ctx.manager.list_agents())
    await ctx.reply(f"agents: {agent_list}")


register(Command(
    name="agents",
    summary="/agents — list available agent profiles",
    detail=(
        "/agents\n\n"
        "List the agent profiles declared in .aegis.yaml. Use one of "
        "these names as the slug argument to /new."
    ),
    handler=_cmd_agents,
))


async def _cmd_sessions(ctx: CmdContext, args: list[str]) -> None:
    sessions = ctx.manager.list_sessions()
    if not sessions:
        await ctx.reply("no sessions")
        return
    # One per line; /underscore_alias is tappable in Telegram (which
    # only auto-links [A-Za-z0-9_]+) and routes back via the _ -> -
    # normalization in _legacy_handle_alias.
    lines = [
        f"{'●' if s.state == 'working' else '○'} "
        f"/{s.handle.replace('-', '_')} {s.state}"
        for s in sessions
    ]
    await ctx.reply("\n".join(lines))


register(Command(
    name="sessions",
    summary="/sessions — list active sessions",
    detail=(
        "/sessions\n\n"
        "List all active agent sessions with their state (working / "
        "ready). Each handle is rendered as /handle_with_underscores "
        "so Telegram makes it tappable; the dispatcher normalizes back "
        "to the real hyphenated handle."
    ),
    handler=_cmd_sessions,
))


async def _cmd_help(ctx: CmdContext, args: list[str]) -> None:
    if not args:
        # Bare /help: group by resource (first whitespace token).
        groups: dict[str, list[Command]] = {}
        for cmd in COMMANDS.values():
            resource = cmd.name.split(" ", 1)[0]
            groups.setdefault(resource, []).append(cmd)
        lines = ["Aegis Telegram commands (/help <name> for detail):", ""]
        for resource in sorted(groups):
            cmds = sorted(groups[resource], key=lambda c: c.name)
            for cmd in cmds:
                lines.append(f"  /{cmd.name} — {cmd.summary}")
            lines.append("")
        # Drop trailing blank
        if lines and lines[-1] == "":
            lines.pop()
        await ctx.reply("\n".join(lines))
        return

    # /help <name> — try exact match first, then prefix match.
    needle = " ".join(args)
    if needle in COMMANDS:
        cmd = COMMANDS[needle]
        await ctx.reply(f"/{cmd.name}\n\n{cmd.detail}")
        return

    matching = [c for c in COMMANDS.values()
                if c.name == needle or c.name.startswith(needle + " ")]
    if matching:
        lines = [f"commands matching {needle!r}:", ""]
        for cmd in sorted(matching, key=lambda c: c.name):
            lines.append(f"  /{cmd.name} — {cmd.summary}")
        await ctx.reply("\n".join(lines))
        return

    await ctx.reply(f"no such command {needle!r}; /help to list all")


register(Command(
    name="help",
    summary="/help [name] — list commands, or show detail for one",
    detail=(
        "/help [name]\n\n"
        "With no argument, lists every registered command grouped by "
        "resource. With a command name (`/help new`), prints the "
        "command's full detail. With a resource prefix "
        "(`/help queue`), lists every subcommand under that resource."
    ),
    handler=_cmd_help,
))


async def _peer_reachable(url: str) -> bool:
    """Quick reachability probe — any HTTP response counts as reachable.
    3s timeout for mobile-fast feedback."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
            await client.get(url.rstrip("/") + "/remote/v1/")
        return True
    except httpx.HTTPError:
        return False


async def _cmd_peers(ctx: CmdContext, args: list[str]) -> None:
    remotes = getattr(ctx.cfg, "remotes", {}) or {}
    if not remotes:
        await ctx.reply("no peers configured")
        return
    lines = ["```",
             f"{'NAME':<12} {'URL':<32} {'AUTH':<8} {'REACHABLE'}"]
    for name in sorted(remotes):
        spec = remotes[name]
        url = getattr(spec, "url", "?")
        auth = "token" if getattr(spec, "token", None) else "—"
        ok = await _peer_reachable(url)
        reach = "✓" if ok else "✗ unreachable"
        lines.append(f"{name:<12} {url:<32} {auth:<8} {reach}")
    lines.append("```")
    await ctx.reply("\n".join(lines))


register(Command(
    name="peers",
    summary="/peers — list configured remotes and their reachability",
    detail=(
        "/peers\n\n"
        "List every peer in .aegis.yaml's `remotes:` block, with URL, "
        "auth status (token configured or not), and a 3-second "
        "reachability probe. No @<peer> argument (the command is "
        "about peers themselves)."
    ),
    handler=_cmd_peers,
))


def _fmt_schedule_table(entries: list[Any]) -> str:
    """Format a list of schedule snapshots as a monospace table.
    Each entry is either a SimpleNamespace (local) or dict (remote)."""
    if not entries:
        return "no schedules"
    lines = ["```",
             f"{'NAME':<22} {'SOURCE':<8} {'NEXT FIRE':<22} "
             f"{'ENABLED':<8} FIRES"]
    for e in entries:
        if isinstance(e, dict):
            name = e.get("name", "?")
            source = e.get("source", "?")
            next_fire = e.get("next_fire") or "—"
            enabled = "✓" if e.get("enabled", True) else "✗"
            fires = e.get("fire_count", 0)
        else:
            name = getattr(e, "name", "?")
            source = getattr(e, "source", "?")
            next_fire = getattr(e, "next_fire", None) or "—"
            enabled = "✓" if getattr(e, "enabled", True) else "✗"
            fires = getattr(e, "fire_count", 0)
        lines.append(f"{name:<22} {source:<8} {next_fire:<22} "
                     f"{enabled:<8} {fires}")
    lines.append("```")
    return "\n".join(lines)


async def _cmd_schedule_list(ctx: CmdContext, args: list[str]) -> None:
    if ctx.target is not None:
        remotes = getattr(ctx.cfg, "remotes", {}) or {}
        if ctx.target not in remotes:
            await ctx.reply(f"unknown peer {ctx.target!r}; "
                             f"known: {sorted(remotes)}")
            return
        from aegis.remote.client import remote_schedule_list
        result = await remote_schedule_list(remotes[ctx.target])
        if "error" in result:
            await ctx.reply(f"▸ remote error: {result['error']}")
            return
        entries = result.get("schedules", [])
        await ctx.reply(_fmt_schedule_table(entries))
        return

    scheduler = getattr(ctx.bridge, "scheduler", None)
    if scheduler is None:
        await ctx.reply("no scheduler configured on this serve")
        return
    entries = scheduler.snapshot()
    await ctx.reply(_fmt_schedule_table(entries))


register(Command(
    name="schedule list",
    summary="/schedule list [@peer] — list schedules with next-fire",
    detail=(
        "/schedule list [@<peer>]\n\n"
        "List every schedule on this serve (or @<peer>) with source "
        "(inline / overlay / pushed), next fire time, enabled state, "
        "and total fire count."
    ),
    handler=_cmd_schedule_list,
))


def _fmt_schedule_show(entry) -> str:
    """Format a single schedule's spec + runtime as a multi-line block."""
    lines = ["```"]
    if isinstance(entry, dict):
        # Remote: full Decision shape from remote_schedule_show.
        name = entry.get("name", "?")
        source = entry.get("source", "?")
        lines.append(f"schedule: {name}  (source: {source})")
        lines.append("")
        spec = entry.get("spec", {})
        for k, v in spec.items():
            lines.append(f"  {k}: {v}")
        runtime = entry.get("runtime") or {}
        if runtime:
            lines.append("")
            for k, v in runtime.items():
                lines.append(f"  {k}: {v}")
    else:
        name = getattr(entry, "name", "?")
        source = getattr(entry, "source", "?")
        lines.append(f"schedule: {name}  (source: {source})")
        lines.append("")
        spec = getattr(entry, "spec", {}) or {}
        for k, v in spec.items():
            lines.append(f"  {k}: {v}")
        for fld in ("next_fire", "last_fire", "fire_count",
                    "in_flight", "enabled"):
            val = getattr(entry, fld, None)
            if val is not None:
                lines.append(f"  {fld}: {val}")
    lines.append("```")
    return "\n".join(lines)


async def _cmd_schedule_show(ctx: CmdContext, args: list[str]) -> None:
    if not args:
        await ctx.reply("usage: /schedule show <name> [@peer]")
        return
    name = args[0]

    if ctx.target is not None:
        remotes = getattr(ctx.cfg, "remotes", {}) or {}
        if ctx.target not in remotes:
            await ctx.reply(f"unknown peer {ctx.target!r}; "
                             f"known: {sorted(remotes)}")
            return
        from aegis.remote.client import remote_schedule_show
        result = await remote_schedule_show(remotes[ctx.target], name)
        if "error" in result:
            await ctx.reply(f"▸ remote error: {result['error']}")
            return
        await ctx.reply(_fmt_schedule_show(result))
        return

    scheduler = getattr(ctx.bridge, "scheduler", None)
    if scheduler is None:
        await ctx.reply("no scheduler configured on this serve")
        return
    entry = scheduler.get(name)
    if entry is None:
        await ctx.reply(f"no such schedule {name!r}")
        return
    await ctx.reply(_fmt_schedule_show(entry))


register(Command(
    name="schedule show",
    summary="/schedule show <name> [@peer] — full spec + runtime",
    detail=(
        "/schedule show <name> [@<peer>]\n\n"
        "Print the full schedule spec (workflow, cron, args, "
        "lifecycle, ...) plus runtime fields (next_fire, last_fire, "
        "fire_count, in_flight, enabled) for one schedule."
    ),
    handler=_cmd_schedule_show,
))


async def _cmd_budget_list(ctx: CmdContext, args: list[str]) -> None:
    if ctx.target is not None:
        remotes = getattr(ctx.cfg, "remotes", {}) or {}
        if ctx.target not in remotes:
            await ctx.reply(f"unknown peer {ctx.target!r}; "
                             f"known: {sorted(remotes)}")
            return
        from aegis.remote.client import remote_budget_list
        result = await remote_budget_list(remotes[ctx.target])
        if "error" in result:
            await ctx.reply(f"▸ remote error: {result['error']}")
            return
        rows = result.get("queues", [])
        await ctx.reply(_fmt_budget_list(rows))
        return

    qm = getattr(ctx.bridge, "queue_manager", None)
    if qm is None:
        await ctx.reply("no queue manager on this serve")
        return
    queues = getattr(qm, "_queues", {})
    if not queues:
        await ctx.reply("no queues configured")
        return
    from datetime import datetime, timezone
    from aegis.budget.evaluator import evaluate_budgets
    now = datetime.now(timezone.utc)
    rows = []
    for name, q in queues.items():
        budgets = getattr(q, "budgets", []) or []
        if not budgets:
            rows.append({"name": name, "budgets_count": 0,
                          "status": "no-budget", "binding": None})
            continue
        tail = qm._load_recent_jsonl(
            name, max_age=max(b.window for b in budgets))
        d = evaluate_budgets(tail, budgets, now)
        if d.allowed:
            rows.append({"name": name, "budgets_count": len(budgets),
                          "status": "ok", "binding": None})
        else:
            c = d.blocked_by[0]
            binding = (f"${c.spent} of ${c.limit} / {c.window_str}"
                        if c.constraint == "usd"
                        else f"{c.spent}/{c.limit} {c.constraint}/{c.window_str}")
            rows.append({"name": name, "budgets_count": len(budgets),
                          "status": "blocked", "binding": binding,
                          "unblock_at": d.unblock_at.isoformat().replace(
                              "+00:00", "Z") if d.unblock_at else None})
    await ctx.reply(_fmt_budget_list(rows))


def _fmt_budget_list(rows: list[dict]) -> str:
    if not rows:
        return "no queues"
    lines = ["```",
             f"{'QUEUE':<14} {'BUDGETS':<8} {'STATUS':<28} UNBLOCKS"]
    for r in rows:
        name = r.get("name", "?")
        count = r.get("budgets_count", 0)
        status_raw = r.get("status", "?")
        if status_raw == "blocked":
            status = f"⛔ {r.get('binding') or 'over'}"
            unblock = r.get("unblock_at") or "—"
        elif status_raw == "ok":
            status = f"✓ {r.get('binding') or 'within budget'}"
            unblock = "—"
        elif status_raw == "no-budget":
            status = "— no budget"
            unblock = "—"
        else:
            status = status_raw
            unblock = r.get("unblock_at") or "—"
        lines.append(f"{name:<14} {count:<8} {status:<28} {unblock}")
    lines.append("```")
    return "\n".join(lines)


register(Command(
    name="budget list",
    summary="/budget list [@peer] — per-queue budget status",
    detail=(
        "/budget list [@<peer>]\n\n"
        "Summarize each queue's budget headroom. Shows the binding "
        "(tightest) constraint per queue, status (ok / blocked / "
        "no-budget), and unblock ETA for blocked queues."
    ),
    handler=_cmd_budget_list,
))


async def _cmd_budget_show(ctx: CmdContext, args: list[str]) -> None:
    if not args:
        await ctx.reply("usage: /budget show <queue> [@peer]")
        return
    queue = args[0]

    if ctx.target is not None:
        remotes = getattr(ctx.cfg, "remotes", {}) or {}
        if ctx.target not in remotes:
            await ctx.reply(f"unknown peer {ctx.target!r}; "
                             f"known: {sorted(remotes)}")
            return
        from aegis.remote.client import remote_budget_show
        result = await remote_budget_show(remotes[ctx.target], queue)
        if "error" in result:
            await ctx.reply(f"▸ remote error: {result['error']}")
            return
        await ctx.reply(_fmt_budget_show(result))
        return

    qm = getattr(ctx.bridge, "queue_manager", None)
    if qm is None:
        await ctx.reply("no queue manager on this serve")
        return
    queues = getattr(qm, "_queues", {})
    if queue not in queues:
        await ctx.reply(f"unknown queue {queue!r}")
        return
    q = queues[queue]
    budgets = getattr(q, "budgets", []) or []
    if not budgets:
        await ctx.reply(f"queue {queue!r} has no budgets configured")
        return
    from datetime import datetime, timezone
    from aegis.budget.evaluator import evaluate_budgets
    tail = qm._load_recent_jsonl(
        queue, max_age=max(b.window for b in budgets))
    d = evaluate_budgets(tail, budgets, datetime.now(timezone.utc))
    payload = {
        "name": queue, "allowed": d.allowed,
        "checks": [{"constraint": c.constraint, "limit": str(c.limit),
                      "spent": str(c.spent), "window": c.window_str,
                      "allowed": c.allowed, "headroom": str(c.headroom)}
                     for c in d.checks],
        "blocked_by": [{"constraint": c.constraint, "window": c.window_str}
                        for c in d.blocked_by],
        "unblock_at": (d.unblock_at.isoformat().replace("+00:00", "Z")
                        if d.unblock_at else None),
    }
    await ctx.reply(_fmt_budget_show(payload))


def _fmt_budget_show(payload: dict) -> str:
    name = payload.get("name", "?")
    lines = ["```", f"budget for queue {name!r}", ""]
    lines.append(f"{'CONSTRAINT':<16} {'LIMIT':<10} {'SPENT':<10} "
                  f"{'WINDOW':<8} {'HEADROOM':<10} STATUS")
    for c in payload.get("checks", []):
        status = "✓" if c.get("allowed") else "⛔"
        lines.append(f"{c['constraint']:<16} {c['limit']:<10} "
                      f"{c['spent']:<10} {c['window']:<8} "
                      f"{c['headroom']:<10} {status}")
    if not payload.get("allowed", True):
        n = len(payload.get("blocked_by", []))
        unblock = payload.get("unblock_at") or "—"
        lines.append("")
        lines.append(f"blocked by {n} budget(s); unblocks at {unblock}")
    lines.append("```")
    return "\n".join(lines)


register(Command(
    name="budget show",
    summary="/budget show <queue> [@peer] — full Decision per BudgetCheck",
    detail=(
        "/budget show <queue> [@<peer>]\n\n"
        "Print every budget on a queue with spent / limit / headroom "
        "/ window / status. Blocked queues also include the "
        "unblock_at ETA."
    ),
    handler=_cmd_budget_show,
))


async def _cmd_queue_list(ctx: CmdContext, args: list[str]) -> None:
    if ctx.target is not None:
        await ctx.reply(
            "▸ /queue list not yet supported cross-host "
            "(local only). Drop @<peer>.")
        return
    qm = getattr(ctx.bridge, "queue_manager", None)
    if qm is None:
        await ctx.reply("no queue manager on this serve")
        return
    queues = getattr(qm, "_queues", {})
    if not queues:
        await ctx.reply("no queues configured")
        return
    lines = ["```",
             f"{'QUEUE':<14} {'AGENT':<14} {'DEPTH':<6} {'IN-FLIGHT':<10} LAST"]
    for name in sorted(queues):
        q = queues[name]
        depth = len(getattr(qm, "_pending", {}).get(name, []))
        in_flight = len(getattr(qm, "_inflight", {}).get(name, []))
        agent = getattr(q, "agent_profile", "?")
        all_tasks = getattr(qm, "_all", {})
        recent = sorted(
            (t for t in all_tasks.values() if t.queue == name
             and t.status in ("completed", "failed")),
            key=lambda t: getattr(t, "completed_at", "") or "",
            reverse=True,
        )
        if recent:
            last = recent[0]
            marker = "✓" if last.status == "completed" else "✗"
            last_str = f"{marker} task#{last.id[:8]}"
        else:
            last_str = "— none"
        lines.append(
            f"{name:<14} {agent:<14} {depth:<6} {in_flight:<10} {last_str}")
    lines.append("```")
    await ctx.reply("\n".join(lines))


register(Command(
    name="queue list",
    summary="/queue list — per-queue depth + in-flight + last task",
    detail=(
        "/queue list\n\n"
        "Local-only in v0.10 (no cross-host queue endpoint yet). "
        "Shows each queue's bound agent profile, pending depth, "
        "in-flight count, and last terminal task."
    ),
    handler=_cmd_queue_list,
))


async def _cmd_queue_show(ctx: CmdContext, args: list[str]) -> None:
    if ctx.target is not None:
        await ctx.reply(
            "▸ /queue show not yet supported cross-host "
            "(local only). Drop @<peer>.")
        return
    if not args:
        await ctx.reply("usage: /queue show <name>")
        return
    name = args[0]
    qm = getattr(ctx.bridge, "queue_manager", None)
    if qm is None:
        await ctx.reply("no queue manager on this serve")
        return
    queues = getattr(qm, "_queues", {})
    if name not in queues:
        await ctx.reply(f"unknown queue {name!r}")
        return
    q = queues[name]
    pending = getattr(qm, "_pending", {}).get(name, [])
    inflight = getattr(qm, "_inflight", {}).get(name, [])
    lines = ["```",
             f"queue: {name}  (agent: {q.agent_profile}, "
             f"max_parallel: {q.max_parallel})", ""]
    if inflight:
        lines.append("IN-FLIGHT")
        for t in inflight:
            handle = getattr(t, "worker_handle", "?") or "?"
            payload = (t.payload or "")[:60]
            lines.append(f"  ⏳ task#{t.id[:8]}  worker:{handle}  "
                          f"payload={payload!r}")
        lines.append("")
    if pending:
        lines.append("PENDING")
        for t in pending:
            payload = (t.payload or "")[:60]
            lines.append(f"  ○ task#{t.id[:8]}  enqueued {t.enqueued_at}  "
                          f"by {t.enqueued_by}  payload={payload!r}")
        lines.append("")
    all_tasks = getattr(qm, "_all", {})
    recent = sorted(
        (t for t in all_tasks.values() if t.queue == name
         and t.status in ("completed", "failed")),
        key=lambda t: getattr(t, "completed_at", "") or "",
        reverse=True,
    )[:10]
    if recent:
        lines.append("RECENT")
        for t in recent:
            marker = "✓" if t.status == "completed" else "✗"
            lines.append(f"  {marker} task#{t.id[:8]}  {t.status}  "
                          f"{getattr(t, 'completed_at', '?')}")
    if not (inflight or pending or recent):
        lines.append("  (no tasks)")
    lines.append("```")
    await ctx.reply("\n".join(lines))


register(Command(
    name="queue show",
    summary="/queue show <name> — pending + in-flight + recent",
    detail=(
        "/queue show <name>\n\n"
        "Local-only in v0.10. Shows the queue's pending tasks "
        "(awaiting dispatch), in-flight tasks (active workers), and "
        "up to 10 most-recent terminal tasks. Payloads are truncated "
        "to 60 characters."
    ),
    handler=_cmd_queue_show,
))


async def _cmd_schedule_run(ctx: CmdContext, args: list[str]) -> None:
    if ctx.target is not None:
        await ctx.reply(
            "▸ /schedule run not yet supported cross-host "
            "(this serve only). Drop @<peer>.")
        return
    if not args:
        await ctx.reply("usage: /schedule run <name>")
        return
    name = args[0]
    scheduler = getattr(ctx.bridge, "scheduler", None)
    if scheduler is None:
        await ctx.reply("no scheduler configured on this serve")
        return
    entry_before = scheduler.get(name)
    if entry_before is None:
        await ctx.reply(f"no such schedule {name!r}")
        return
    try:
        scheduler.fire_now(name)
    except Exception as e:
        await ctx.reply(f"▸ error firing {name!r}: {e}")
        return
    next_fire = getattr(entry_before, "next_fire", None) or "—"
    await ctx.reply(
        f"▸ fired schedule {name!r}\n"
        f"  next regular fire still at {next_fire}")


register(Command(
    name="schedule run",
    summary="/schedule run <name> — fire a schedule now (this serve only)",
    detail=(
        "/schedule run <name>\n\n"
        "Fire-now a schedule on this serve. The next regular fire "
        "tick is unaffected. Local only — cross-host fire-now is not "
        "yet a substrate feature (deferred from v0.8); use @<peer> "
        "and the substrate will reject with a clear error."
    ),
    handler=_cmd_schedule_run,
))
