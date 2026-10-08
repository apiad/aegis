"""What a session needs from a person, decided once, in Python.

Facts decide what they can: a running turn is working, a failed one is an
error, a live monitor or task is waiting. The agent's own `turn_end` decides
what only it knows: whether its last message asked something (needs_you),
presented something to read (review), or finished (done). No model reads the
transcript to guess. The first rule that holds wins:

    working > error > needs_you > waiting > review > done

A queue worker's needs_you is done: its answer goes to whoever enqueued it,
and nobody reads its tab, so its replies are dropped too.
"""

from __future__ import annotations


def card(standing: dict, *, working: bool, worker: bool, waits: list[str]) -> dict:
    report = standing.get("report")
    said = report["attention"] if report else "done"
    if worker and said == "needs_you":
        said = "done"
    error = standing.get("turn_error") or ""
    if working:
        kind = "working"
    elif error:
        kind = "error"
    elif said == "needs_you":
        kind = "needs_you"
    elif waits:
        kind = "waiting"
    else:
        kind = said
    if kind == "error":
        line = error
    elif kind in ("needs_you", "review") and report:
        line = report["line"]
    else:
        line = ""
    shows_report = kind in ("needs_you", "review", "done") and not worker
    plan = standing.get("plan") or []
    return {
        "attention": kind,
        "attention_line": line,
        "replies": list(report["replies"]) if report and shows_report else [],
        "waiting_on": ", ".join(waits) if kind == "waiting" else "",
        "plan": plan,
        "plan_now": next((i["text"] for i in plan if i["state"] == "doing"), ""),
        "plan_did": standing.get("did") or "",
        "plan_done": sum(1 for i in plan if i["state"] == "done"),
        "plan_total": len(plan),
    }
