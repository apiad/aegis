"""Building a FleetSnapshot from the live manager.

Every read is in-memory. The one rule: nothing here opens a file. The
dashboard redraws on an event stream and a transcript read in that path
would stutter the whole grid.

Optional sources are optional on purpose. Headless callers and most of
the test suite hold a manager with no repo tracker, no claim registry and
no monitors, and a dashboard that raises rather than omitting a section
would take them all down.
"""

from __future__ import annotations

import socket

from collections import Counter
from dataclasses import replace

from aegis.budget.cost import compute
from aegis.fleet.models import BandView, CardView, FleetSnapshot, Origin, RepoCount
from aegis.repos.render import _branch_cell, _churn, _counts


def build_snapshot(manager, *, now: float, ghosts=None) -> FleetSnapshot:
    sessions = list(getattr(manager, "_sessions", []))
    repos = _repo_index(sessions)
    cards = tuple(
        _card(s, index=i + 1, now=now, manager=manager, repos=repos)
        for i, s in enumerate(sessions)
    )
    # The band counts the live fleet. A ghost is a session that has already
    # closed, kept on screen briefly, so it is drawn but never counted.
    band = _band(cards, sessions=sessions, manager=manager)
    if ghosts:
        # ghost_since and now are both monotonic, and the renderer has no
        # clock, so the ghost's age is taken here.
        cards = cards + tuple(
            replace(c, ghost_s=now - c.ghost_since) if c.ghost_since is not None else c
            for c, _died in ghosts.values()
        )
    return FleetSnapshot(band=band, cards=cards)


def _card(s, *, index: int, now: float, manager, repos) -> CardView:
    m = s.metrics
    plan = getattr(s, "plan", None)
    ps = plan.snapshot(now) if plan is not None else None
    view = repos.get(s.handle)
    return CardView(
        handle=s.handle,
        title=getattr(s, "title", ""),
        state=s.state.value,
        agent_slug=getattr(s, "agent_slug", ""),
        host=getattr(getattr(s, "place", None), "host", "local"),
        repo=_repo_label(view),
        origin=getattr(s, "origin", None) or Origin(),
        uptime_s=m.session_seconds(now),
        # turn_start is None between turns; session_seconds would otherwise
        # read as a turn that has been running since the session began.
        turn_s=m.turn_seconds(now) if m.turn_start is not None else 0.0,
        cost_usd=_cost(s),
        ctx_pct=(100.0 * m.last_true_input / m.context_window)
        if m.context_window
        else 0.0,
        plan_done=ps.done if ps else 0,
        plan_total=ps.total if ps else 0,
        plan_current=(ps.current.subject if ps and ps.current else ""),
        did=getattr(s, "_last_recap_line", ""),
        doing=getattr(getattr(s, "fleet_recap", None), "doing", ""),
        events=getattr(s, "recent_events", ()),
        claims=_claims_for(manager, s.handle),
        monitor=_monitor_label(manager, s.handle),
        tab_index=index,
    )


def _band(cards, *, sessions, manager) -> BandView:
    """The four state counters are disjoint and sum to ``total``.

    ``waiting`` is a ready session that ended its turn to wait: it armed a
    monitor that is still live, or it is owed a queue callback. A working
    session with a monitor is still working.
    """
    working = ready = waiting = error = 0
    for c in cards:
        if c.state == "working":
            working += 1
        elif c.state == "error":
            error += 1
        elif _monitors_for(manager, c.handle) or _owed_callback(manager, c.handle):
            waiting += 1
        else:
            ready += 1
    ephemeral = [c.origin.kind for c in cards if c.origin.ephemeral]
    ctx = [(c.handle, c.ctx_pct) for c in cards]
    qm = getattr(manager, "queue_manager", None)
    mm = getattr(manager, "monitor_manager", None)
    return BandView(
        # The machine this dashboard runs on. The first card's host would
        # name a remote box whenever tab 1 happens to be one.
        host=socket.gethostname(),
        total=len(cards),
        yours=len(cards) - len(ephemeral),
        ephemeral=len(ephemeral),
        by_kind=tuple(sorted(Counter(ephemeral).items())),
        working=working,
        ready=ready,
        waiting=waiting,
        error=error,
        ctx_avg=sum(p for _h, p in ctx) / len(ctx) if ctx else 0.0,
        ctx_worst=max(ctx, key=lambda hp: hp[1]) if ctx else None,
        cost_live=sum(c.cost_usd for c in cards),
        # getattr: a session source without the counters has spent nothing.
        recap_cost=sum(getattr(s, "fleet_recap_cost_usd", 0.0) for s in sessions),
        recap_calls=sum(getattr(s, "fleet_recap_calls", 0) for s in sessions),
        recap_cancelled=sum(getattr(s, "fleet_recap_cancelled", 0) for s in sessions),
        queues=(len(qm._workers), len(qm._queues)) if qm is not None else (0, 0),
        monitors=len(mm.snapshot()) if mm is not None else 0,
        repos=tuple(
            RepoCount(name=v.label, agents=len(v.writers), shared=v.shared)
            for v in _repo_views(sessions)
        ),
    )


def _cost(s) -> float:
    """List-price cost so far. A session with no resolved agent profile —
    every headless caller and most of the suite — costs 0.0 rather than
    raising, for the same reason the other sources are optional."""
    agent = getattr(s, "agent", None)
    if agent is None:
        return 0.0
    return float(compute(s.metrics, agent.harness, agent.model).usd)


def _repo_views(sessions) -> list:
    """The tracker is app-wide and every session carries the same one, so
    read it off the first session that has it. ``[]`` when none does."""
    for s in sessions:
        tracker = getattr(s, "repo_tracker", None)
        if tracker is not None:
            return tracker.snapshot()
    return []


def _repo_index(sessions) -> dict:
    """``{handle: RepoView}``, each handle's most recently written repo.
    ``snapshot()`` is newest first, so the first row naming a handle wins."""
    index: dict = {}
    for v in _repo_views(sessions):
        for h in v.writers:
            index.setdefault(h, v)
    return index


def _repo_label(view) -> str:
    """``une-tools · main +3 ~2``, built from the repo sidebar's own cells
    so the card and the sidebar never disagree about a repo."""
    if view is None:
        return ""
    branch, _alarm = _branch_cell(view)
    parts = [branch, *_churn(view), _counts(view)]
    tail = " ".join(p for p in parts if p)
    return f"{view.label} · {tail}" if tail else view.label


def _claims_for(manager, handle: str) -> int:
    locks = getattr(manager, "locks", None)
    if locks is None:
        return 0
    return sum(1 for c in locks.active() if c.handle == handle)


def _monitors_for(manager, handle: str) -> list:
    mm = getattr(manager, "monitor_manager", None)
    if mm is None:
        return []
    return mm.snapshot(for_handle=handle)


def _monitor_label(manager, handle: str) -> str:
    """``pytest 60%`` for the first live monitor, ``+n`` for the rest."""
    views = _monitors_for(manager, handle)
    if not views:
        return ""
    first = views[0]
    label = first.description
    if first.pct is not None:
        label = f"{label} {first.pct:.0f}%"
    if len(views) > 1:
        label = f"{label} +{len(views) - 1}"
    return label


def _owed_callback(manager, handle: str) -> bool:
    """A queue task, pending or running, that calls back to ``handle``.

    Matched on ``callback_handle``: ``enqueued_by`` is a sender tag such as
    ``agent:<handle>`` and would never equal a bare handle.
    """
    qm = getattr(manager, "queue_manager", None)
    if qm is None:
        return False
    running = (task for task, _last_text in qm._workers.values())
    pending = (task for tasks in qm._pending.values() for task in tasks)
    return any(t.callback and t.callback_handle == handle for t in (*running, *pending))
