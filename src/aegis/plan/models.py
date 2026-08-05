"""Plan domain types.

No Textual, no I/O, no clock reads: the tracker is handed every timestamp
so a replayed log reproduces the live numbers exactly.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanTask:
    """One task with its accumulated working time.

    ``working_s is None`` means the task never entered in_progress, which
    renders as an em dash. That is deliberately distinct from 0.0, which
    would claim the task ran and took no time.
    """
    key: str
    subject: str
    status: str
    active_form: str | None = None
    working_s: float | None = None

    @property
    def label(self) -> str:
        """Present-continuous while in progress, imperative otherwise."""
        if self.status == "in_progress" and self.active_form:
            return self.active_form
        return self.subject


@dataclass(frozen=True)
class PlanState:
    tasks: tuple[PlanTask, ...] = ()

    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def done(self) -> int:
        return sum(1 for t in self.tasks if t.status == "completed")

    @property
    def current(self) -> PlanTask | None:
        return next((t for t in self.tasks if t.status == "in_progress"), None)

    def __bool__(self) -> bool:
        return bool(self.tasks)


@dataclass(frozen=True)
class PlanSnapshot:
    """The small roll-up the coordination plane carries on SessionInfo, so
    a peer deciding who to hand work to also learns how far along everyone
    is without pulling a full task list."""
    done: int = 0
    total: int = 0
    current: str | None = None
    current_working_s: float | None = None
    updated_at: str | None = None
