"""Coordination slash commands: /groups, /schedules — thin calls over the
groups bridge and the scheduler-push helpers."""
from __future__ import annotations

from aegis.commands import (
    CommandContext, CommandResult, SlashCommand, register,
)
from aegis.commands.args import Arg, ArgSpec


def _schedule_names(bridge) -> list:
    from aegis.scheduler.push import list_payload
    rows = list_payload(getattr(bridge, "scheduler", None), bridge.state_root,
                        bridge.inline_schedule_names()).get("schedules", [])
    return [r["name"] for r in rows]


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


async def _schedules(ctx: CommandContext, args) -> CommandResult:
    from aegis.scheduler.push import (
        list_payload, logs_payload, remove_schedule, show_payload)
    b = ctx.bridge
    sub = args.get("subverb")
    if sub in (None, "list"):
        rows = list_payload(getattr(b, "scheduler", None), b.state_root,
                            b.inline_schedule_names()).get("schedules", [])
        if not rows:
            return CommandResult(True, "no schedules")
        lines = [f"  {'●' if r.get('enabled') else '○'} {r['name']} · "
                 f"next {r.get('next_fire', '?')}" for r in rows]
        return CommandResult(True, f"{len(rows)} schedule"
                             f"{'' if len(rows) == 1 else 's'}",
                             "\n".join(lines))
    name = args.get("name")
    if not name:
        return CommandResult(
            False, "usage: /schedules show|enable|disable|remove|logs <name>")
    if sub == "show":
        p = show_payload(getattr(b, "scheduler", None), b.state_root,
                         b.inline_schedule_names(), name)
        if p is None:
            return CommandResult(False, f"schedule {name} not found")
        return CommandResult(True, f"schedule {name}",
                             "\n".join(f"{k}: {v}" for k, v in p.items()))
    if sub in ("enable", "disable"):
        import aegis.config as cfg
        import aegis.config.edit as cfg_edit
        root = cfg.find_project_root()
        if root is None:
            return CommandResult(False, "no .aegis.yaml found")
        try:
            cfg_edit.set_schedule_enabled(root, name, sub == "enable")
        except (KeyError, cfg.ConfigError, FileNotFoundError) as e:
            return CommandResult(False, f"cannot {sub} {name}: {e}")
        return CommandResult(True, f"schedule {name} {sub}d")
    if sub == "remove":
        r = remove_schedule(getattr(b, "scheduler", None), b.state_root,
                            b.inline_schedule_names(), name)
        if getattr(r, "status", None) == "ok":
            return CommandResult(True, f"schedule {name} removed")
        return CommandResult(False, f"cannot remove {name}",
                             getattr(r, "status", "error"))
    if sub == "logs":
        recs = logs_payload(b.state_root, name).get("records", [])
        if not recs:
            return CommandResult(True, f"no logs for {name}")
        lines = [str(rec) for rec in recs[-20:]]
        return CommandResult(True, f"schedule {name} · {len(recs)} records",
                             "\n".join(lines))
    return CommandResult(
        False, "usage: /schedules [show|enable|disable|remove|logs <name>]")


for _cmd in (
    SlashCommand("groups", "list groups, or status/dissolve one",
                 "/groups [status|dissolve <name>]", _groups,
                 spec=ArgSpec(positionals=(
                     Arg("subverb", required=False,
                         completer=("list", "status", "dissolve")),
                     Arg("name", required=False,
                         completer=lambda b: [g["name"]
                                              for g in b.groups.list_groups()])))),
    SlashCommand(
        "schedules", "list schedules, or show/enable/disable/remove/logs one",
        "/schedules [show|enable|disable|remove|logs <name>]", _schedules,
        spec=ArgSpec(positionals=(
            Arg("subverb", required=False,
                completer=("list", "show", "enable", "disable", "remove",
                           "logs")),
            Arg("name", required=False, completer=_schedule_names)))),
):
    register(_cmd)
