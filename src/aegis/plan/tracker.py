"""PlanTracker — folds AgentPlan events plus turn boundaries into a
PlanState carrying per-task working time.

Working time, not wall-clock: a session idles between turns, and a task
left in_progress overnight would otherwise report nine hours for one
minute of work. Elapsed accrues only while the session is mid-turn, which
is also exactly when the strip's in-progress circle spins — the rotating
glyph is a literal rendering of the clock running.

The tracker never reads a clock. Every method takes an explicit ``ts``,
which is what lets a replayed transcript reproduce the live numbers.
"""
from __future__ import annotations

from datetime import UTC, datetime

from aegis.events import AgentPlan
from aegis.plan.models import PlanSnapshot, PlanState, PlanTask

# Prefix marking a positional (id-less) key, so an adopted record can be
# told apart from one keyed by a real Task* id.
_POS = "pos:"


class PlanTracker:
    def __init__(self) -> None:
        # key → mutable record. Insertion-ordered, which is plan order.
        self._tasks: dict[str, dict] = {}
        self._working = False
        # The key currently accruing time, and the ts it started. Non-None
        # only while a task is in_progress AND the session is mid-turn —
        # that conjunction is the whole definition of the metric.
        self._accruing: str | None = None
        self._since: float = 0.0
        self._updated_at: str | None = None

    # -- internals ---------------------------------------------------

    def _flush(self, ts: float) -> None:
        """Bank elapsed time into whichever task was accruing."""
        if self._accruing is not None:
            rec = self._tasks.get(self._accruing)
            if rec is not None:
                rec["working_s"] = (rec["working_s"] or 0.0) + (ts - self._since)
        self._accruing = None

    def _rearm(self, ts: float) -> None:
        """Resume accruing if a task is in progress and we are working."""
        if not self._working:
            return
        cur = next((k for k, r in self._tasks.items()
                    if r["status"] == "in_progress"), None)
        if cur is not None:
            self._accruing = cur
            self._since = ts

    @staticmethod
    def _key(entry, index: int) -> str:
        """Task* entries have stable ids, so a rename keeps its timing.
        Snapshot sources (TodoWrite, ACP) have none — resending a full
        ordered list is itself the identity claim, so fall back to
        position plus subject."""
        return entry.id if entry.id else f"{_POS}{index}:{entry.content}"

    def _prior(self, key: str, entry, index: int) -> dict | None:
        """The previous record for this entry, tolerating an id that only
        just arrived.

        TaskCreate emits its plan BEFORE the tool_result carrying the id,
        so a task first appears unidentified — keyed positionally — and
        gains its id on the next revision. Keyed naively that reads as one
        task vanishing and another appearing, and the time already banked
        is silently dropped. Adopt the positional record instead.
        """
        prev = self._tasks.get(key)
        if prev is not None or not entry.id:
            return prev
        by_pos = self._tasks.get(f"{_POS}{index}:{entry.content}")
        if by_pos is not None:
            return by_pos
        # Position may have shifted (a task deleted above it), so fall
        # back to matching an as-yet-unidentified task by subject.
        return next((r for k, r in self._tasks.items()
                     if k.startswith(_POS) and r["subject"] == entry.content),
                    None)

    # -- surface -----------------------------------------------------

    def apply_plan(self, plan: AgentPlan, ts: float) -> None:
        self._flush(ts)
        seen: dict[str, dict] = {}
        for i, entry in enumerate(plan.entries):
            key = self._key(entry, i)
            prev = self._prior(key, entry, i)
            started = bool(prev and prev["started"]) \
                or entry.status == "in_progress"
            seen[key] = {
                "subject": entry.content,
                "status": entry.status,
                "active_form": entry.active_form
                    or (prev or {}).get("active_form"),
                # Carried across revisions, so a task going in_progress →
                # completed → in_progress resumes rather than restarts.
                "working_s": (prev or {}).get("working_s"),
                "started": started,
            }
            if started and seen[key]["working_s"] is None:
                seen[key]["working_s"] = 0.0
        self._tasks = seen
        self._updated_at = datetime.now(UTC).isoformat()
        self._rearm(ts)

    def set_working(self, working: bool, ts: float) -> None:
        if working == self._working:
            return          # redundant call, must not double-count
        self._flush(ts)
        self._working = working
        self._rearm(ts)

    def snapshot(self, ts: float) -> PlanState:
        """Read-only: the live figure is computed, never banked, so
        reading repeatedly does not compound it."""
        live = ts - self._since if self._accruing is not None else 0.0
        return PlanState(tasks=tuple(
            PlanTask(
                key=key,
                subject=rec["subject"],
                status=rec["status"],
                active_form=rec["active_form"],
                working_s=None if not rec["started"] else
                    (rec["working_s"] or 0.0)
                    + (live if key == self._accruing else 0.0),
            )
            for key, rec in self._tasks.items()
        ))

    def roll_up(self, ts: float) -> PlanSnapshot:
        st = self.snapshot(ts)
        cur = st.current
        return PlanSnapshot(
            done=st.done, total=st.total,
            current=cur.subject if cur else None,
            current_working_s=cur.working_s if cur else None,
            updated_at=self._updated_at,
        )

    @property
    def working(self) -> bool:
        """True while the session is mid-turn — the strip spins on this."""
        return self._working
