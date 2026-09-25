"""The reconciler: reap what finished, then start what fits.

Every tick does the same two things in the same order and holds nothing. A
crash costs one tick. The board is the only state: the marker on each card's
pinned comment carries the task id, so nothing here has to be remembered
between runs.
"""

from __future__ import annotations

import json
from pathlib import Path

from aegis.workflows.builtins.afk.board import (
    BoardError,
    fetch_board,
    parse_marker,
    render_marker,
    set_field,
    upsert_comment,
)
from aegis.workflows.builtins.afk.decide import (
    eligible,
    quota_gate,
    rank,
    resolve_repo_path,
    review_triggers,
)
from aegis.workflows.builtins.afk.payload import compose
from aegis.workflows.builtins.afk.preflight import NO_GATE, preflight
from aegis.workflows.builtins.afk.render import card_comment, result_section
from aegis.workflows.builtins.afk.report import ReportError, parse_report

STATUSES = {
    "todo": "Todo",
    "waiting": "Waiting",
    "running": "Running",
    "needs_review": "Needs review",
    "blocked": "Blocked",
    "failed": "Failed",
    "done": "Done",
}
FIELDS = {
    "status": "Status",
    # Not "Repo": GitHub Projects refuses that name and "Repository" as
    # reserved values, so a board carrying the documented default could
    # not be built at all. Verified against the live API 2026-09-25.
    "repo": "Target repo",
    "priority": "Priority",
    "deadline": "Deadline",
    "progress": "Progress",
    "waiting_on": "Waiting on",
}


async def read_quota(_engine):
    """The live Claude window reading, or None.

    Separated so tests can replace it and so a provider change lands in one
    place. Never raises: an exception here would read as a crashed tick when
    the honest answer is "I could not ask".
    """
    try:
        from aegis.usage.quota_providers import build_services, read_all

        readings = await read_all(build_services())
        for provider, state in readings:
            if provider.name == "claude":
                return state
    except Exception:  # noqa: BLE001
        return None
    return None


def _statuses(cfg: dict) -> dict[str, str]:
    return {**STATUSES, **(cfg.get("status_names") or {})}


def _fields(cfg: dict) -> dict[str, str]:
    return {**FIELDS, **(cfg.get("field_names") or {})}


async def _gh(engine, argv: list[str]) -> str:
    quoted = " ".join(
        a if a.startswith("-") or a.isalnum() else json.dumps(a) for a in argv
    )
    res = await engine.bash(quoted)
    if res.get("exit") != 0:
        raise BoardError(
            f"{argv[:3]} exited {res.get('exit')}: {str(res.get('stdout'))[:300]}"
        )
    return res.get("stdout") or ""


async def fetch_comment(engine, card) -> str:
    """The coordinator's own pinned comment on a card, or "".

    Its marker is where the task id lives, so this is how a later tick learns
    what it started. Found by marker rather than by author or position: the
    coordinator authenticates as the operator, whose other comments are not
    state.
    """
    raw = await _gh(
        engine,
        ["gh", "api", f"repos/{card.repo}/issues/{card.number}/comments", "--paginate"],
    )
    for c in json.loads(raw or "[]"):
        if parse_marker(c.get("body") or ""):
            return c["body"]
    return ""


async def _write_card(
    engine, schema, card, *, status, coordinator, result, marker, fields
):
    """Comment first, then status. A comment that fails leaves the card where
    it was; a status moved before a failed comment leaves a card in a state
    with no explanation on it."""
    plan = ""  # the progress schedule owns the plan section
    await upsert_comment(
        lambda argv: _gh(engine, argv),
        card,
        body=card_comment(
            coordinator=coordinator, plan=plan, result=result, marker=marker
        ),
    )
    if status:
        await set_field(
            lambda argv: _gh(engine, argv),
            schema,
            card,
            field=fields["status"],
            value=status,
        )


async def check_gh(engine) -> str:
    """Empty when `gh` can write the board, else why not.

    Checked once per tick, before any card is touched. A coordinator that
    cannot write the board would otherwise start workers and lose every
    result — the expensive half runs and the recorded half does not.
    """
    res = await engine.bash("gh auth status")
    if res.get("exit") != 0:
        return "gh is not authenticated"
    if "project" not in (res.get("stdout") or ""):
        return "gh token is missing the 'project' scope"
    return ""


async def run_tick(engine, cfg: dict, *, now: str) -> str:
    blocked = await check_gh(engine)
    if blocked:
        engine.log(f"afk: touching nothing — {blocked}")
        return f"aborted: {blocked}"

    statuses = _statuses(cfg)
    fields = _fields(cfg)
    repo_root = Path(cfg["repo_root"])

    schema, cards = await fetch_board(
        lambda argv: _gh(engine, argv),
        owner=cfg["owner"],
        owner_type=cfg["owner_type"],
        project=cfg["project"],
    )
    by_status: dict[str, list] = {}
    for c in cards:
        by_status.setdefault(c.fields.get(fields["status"], ""), []).append(c)

    reaped = 0
    running = by_status.get(statuses["running"], [])
    for card in list(running):
        try:
            if await _reap_one(
                engine, schema, card, cfg, statuses, fields, repo_root, now=now
            ):
                reaped += 1
                running.remove(card)
        except BoardError as e:
            engine.log(f"afk: board write failed on #{card.number}: {e}")
        except Exception as e:  # noqa: BLE001
            engine.log(f"afk: reaping #{card.number} raised {e!r}")

    gate = quota_gate(
        await read_quota(engine),
        weekly_stop_at=float(cfg["weekly_stop_at"]),
        session_stop_at=float(cfg["session_stop_at"]),
    )
    if not gate.may_start:
        engine.log(f"afk: starting nothing — {gate.reason}")
        return f"reaped {reaped}, started 0 ({gate.reason})"

    running_repos = {c.fields.get(fields["repo"]) for c in running}
    capacity = max(0, int(cfg["max_in_flight"]) - len(running))
    candidates = eligible(
        cards,
        status_names=statuses,
        repo_root=repo_root,
        running_repos=running_repos,
        status_field=fields["status"],
        repo_field=fields["repo"],
    )
    ordered = rank(
        candidates, priority_order=tuple(cfg["priority_order"]), field_names=fields
    )

    started = 0
    for card in ordered:
        if started >= capacity:
            break
        repo = card.fields.get(fields["repo"])
        if repo in running_repos:
            continue
        try:
            if await _start_one(
                engine, schema, card, cfg, statuses, fields, repo_root, now=now
            ):
                started += 1
                running_repos.add(repo)
        except BoardError as e:
            engine.log(f"afk: board write failed starting #{card.number}: {e}")
        except Exception as e:  # noqa: BLE001
            engine.log(f"afk: starting #{card.number} raised {e!r}")

    return f"reaped {reaped}, started {started} ({gate.reason})"


async def _reap_one(
    engine, schema, card, cfg, statuses, fields, repo_root, *, now: str
) -> bool:
    marker = parse_marker(await fetch_comment(engine, card))
    task_id = marker.get("task")
    attempt = int(marker.get("attempt") or 1)
    state = engine.task_status(task_id) if task_id else None

    if state is None:
        if attempt >= int(cfg["max_attempts"]):
            await _write_card(
                engine,
                schema,
                card,
                status=statuses["blocked"],
                coordinator=(
                    f"The run was lost again (attempt {attempt}). Parking this "
                    f"for a person: a card that keeps losing its worker is not "
                    f"something the loop should keep retrying."
                ),
                result="",
                marker=render_marker(tick=now, attempt=attempt),
                fields=fields,
            )
        else:
            await _write_card(
                engine,
                schema,
                card,
                status=statuses["todo"],
                coordinator=(
                    f"The run was lost — task {task_id!r} is unknown to the "
                    f"queue, which usually means the daemon restarted. Back to "
                    f"Todo for attempt {attempt + 1}."
                ),
                result="",
                marker=render_marker(tick=now, attempt=attempt + 1),
                fields=fields,
            )
        return True

    if state.get("status") not in ("completed", "error", "cancelled"):
        return False

    raw = state.get("result") or state.get("error") or ""
    gate_cmd = marker.get("gate") or NO_GATE
    try:
        report = parse_report(raw)
    except ReportError as e:
        await _write_card(
            engine,
            schema,
            card,
            status=statuses["failed"],
            coordinator=f"The worker's report could not be read: {e}",
            result=result_section(
                None, gate_cmd=gate_cmd, measured_exit=None, gate_output=raw
            ),
            marker=render_marker(tick=now, attempt=attempt),
            fields=fields,
        )
        return True

    measured_exit = None
    gate_output = ""
    if gate_cmd != NO_GATE:
        repo_path = resolve_repo_path(repo_root, card.fields.get(fields["repo"]))
        res = await engine.bash(gate_cmd, cwd=str(repo_path))
        measured_exit = res.get("exit")
        gate_output = res.get("stdout") or ""

    if report.status == "blocked":
        status, note = statuses["blocked"], "The worker reported it was blocked."
    elif measured_exit not in (0, None) or report.status == "failed":
        status, note = statuses["failed"], "The gate is red."
    else:
        triggers = review_triggers(
            report,
            card,
            review_changed_files=int(cfg["review_changed_files"]),
            vague_body_chars=int(cfg["vague_body_chars"]),
            acceptance_markers=tuple(cfg["acceptance_markers"]),
        )
        status = statuses["needs_review"]
        note = "Gate green. " + (
            f"Flagged for review ({', '.join(triggers)})."
            if triggers
            else "No review triggers fired."
        )

    await _write_card(
        engine,
        schema,
        card,
        status=status,
        coordinator=note,
        result=result_section(
            report,
            gate_cmd=gate_cmd,
            measured_exit=measured_exit,
            gate_output=gate_output,
        ),
        marker=render_marker(tick=now, attempt=attempt),
        fields=fields,
    )
    return True


async def _start_one(
    engine, schema, card, cfg, statuses, fields, repo_root, *, now: str
) -> bool:
    try:
        repo_path = resolve_repo_path(repo_root, card.fields.get(fields["repo"]))
    except ValueError as e:
        await _write_card(
            engine,
            schema,
            card,
            status=statuses["blocked"],
            coordinator=f"Cannot resolve this card's repo: {e}",
            result="",
            marker=render_marker(tick=now),
            fields=fields,
        )
        return False

    pre = await preflight(
        lambda cmd, cwd: engine.bash(cmd, cwd=cwd),
        repo_path,
        gate_commands=tuple(cfg["gate_commands"]),
    )
    if not pre.ok:
        await _write_card(
            engine,
            schema,
            card,
            status=statuses["blocked"],
            coordinator=f"Not starting: {pre.reason}",
            result="",
            marker=render_marker(tick=now),
            fields=fields,
        )
        return False

    payload = compose(
        card, repo_path=str(repo_path), branch=pre.branch, gate_cmd=pre.gate_cmd
    )
    task_id = await engine.enqueue(cfg["worker_queue"], payload, callback=False)
    await _write_card(
        engine,
        schema,
        card,
        status=statuses["running"],
        coordinator=(
            f"Started at {now} on `{pre.gate_cmd}` in "
            f"`{repo_path}` (branch `{pre.branch}`)."
        ),
        result="",
        marker=render_marker(task=task_id, tick=now, attempt=1, gate=pre.gate_cmd),
        fields=fields,
    )
    return True
