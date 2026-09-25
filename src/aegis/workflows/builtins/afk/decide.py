"""Every decision this package makes, as pure functions.

Nothing here touches the network, the filesystem beyond a path check, or the
clock. That is deliberate: these are the answers a person will want to argue
with the morning after a run, and an answer you can only get by running the
whole loop is an answer nobody audits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from aegis.workflows.builtins.afk.board import Card
from aegis.workflows.builtins.afk.report import Report

_CHECKLIST_RE = re.compile(r"^\s*[-*]\s*\[[ xX]\]", re.M)


@dataclass(frozen=True)
class Gate:
    may_start: bool
    reason: str


def quota_gate(state, *, weekly_stop_at: float, session_stop_at: float) -> Gate:
    """Whether there is subscription room to start new work.

    Three ways to say no, and the third is the one that matters: a reading we
    could not take, or one taken while fetches are failing, is never read as
    permission.
    """
    if state is None or state.snapshot is None:
        return Gate(False, "quota unread; not starting new work")
    if state.failure:
        return Gate(False, f"quota reading is stale ({state.failure})")
    weekly = state.snapshot.window("weekly_all")
    session = state.snapshot.window("session")
    if weekly is None or session is None:
        return Gate(False, "quota payload carried no weekly or five-hour window")
    if weekly.percent >= weekly_stop_at:
        return Gate(
            False,
            f"weekly window at {weekly.percent:.0f}% (stop at {weekly_stop_at:.0f}%)",
        )
    if session.percent >= session_stop_at:
        return Gate(
            False,
            f"five-hour window at {session.percent:.0f}% "
            f"(stop at {session_stop_at:.0f}%)",
        )
    return Gate(True, f"weekly {weekly.percent:.0f}%, five-hour {session.percent:.0f}%")


def resolve_repo_path(repo_root: Path, repo: str | None) -> Path:
    """The checkout a card names, or a refusal.

    The `Repo` single-select's options are the whitelist, but the whitelist
    is enforced here too: a board can be edited, and a worker dispatched
    outside `repo_root` writes wherever the value points.
    """
    if not repo:
        raise ValueError("card has no Repo value")
    root = Path(repo_root).resolve()
    candidate = (root / repo).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"{repo!r} resolves outside {root}")
    if not candidate.is_dir():
        raise ValueError(f"no checkout at {candidate}")
    return candidate


def eligible(
    cards: list[Card],
    *,
    status_names: dict[str, str],
    repo_root: Path,
    running_repos: set[str],
    status_field: str = "Status",
    repo_field: str = "Repo",
) -> list[Card]:
    """Cards that could be started right now.

    `Waiting` is eligible alongside `Todo`: it is the coordinator's own
    deferral and is reconsidered every tick. Excluding it would strand every
    card the coordinator ever chose to defer.
    """
    startable = {status_names["todo"], status_names["waiting"]}
    out: list[Card] = []
    for card in cards:
        if card.state != "OPEN":
            continue
        if card.fields.get(status_field) not in startable:
            continue
        repo = card.fields.get(repo_field)
        if repo in running_repos:
            continue
        try:
            resolve_repo_path(repo_root, repo)
        except ValueError:
            continue
        out.append(card)
    return out


def rank(
    cards: list[Card],
    *,
    priority_order: tuple[str, ...],
    field_names: dict[str, str],
) -> list[Card]:
    """Deterministic ordering: priority, then nearest deadline, then age.

    The second plan replaces this with an agent. It is kept simple on
    purpose — a stand-in that looked clever would make the agent's
    improvement hard to see.
    """
    pri_field = field_names["priority"]
    dl_field = field_names["deadline"]
    order = {name: i for i, name in enumerate(priority_order)}

    def key(card: Card):
        pri = order.get(card.fields.get(pri_field, ""), len(order))
        deadline = card.fields.get(dl_field) or "9999-99-99"
        return (pri, deadline, card.number)

    return sorted(cards, key=key)


def review_triggers(
    report: Report,
    card: Card,
    *,
    review_changed_files: int,
    vague_body_chars: int,
    acceptance_markers: tuple[str, ...],
) -> list[str]:
    """Why this card needs a reviewer, or an empty list.

    "The prose was vague" is the real reason you want a reviewer and is not
    computable, so it is substituted by two things that are: a card that
    neither enumerates what to do nor says how you would know it worked.
    """
    reasons: list[str] = []
    if report.judgement:
        reasons.append("judgement")
    if report.changed is not None and report.changed > review_changed_files:
        reasons.append("changed")
    body = card.body or ""
    has_checklist = bool(_CHECKLIST_RE.search(body))
    lowered = body.lower()
    has_marker = any(m.lower() in lowered for m in acceptance_markers)
    if not has_checklist and not has_marker and len(body) < vague_body_chars:
        reasons.append("vague")
    return reasons
