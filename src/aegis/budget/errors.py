"""Typed exceptions for budget rejection."""
from __future__ import annotations

from aegis.budget.evaluator import Decision


class BudgetExceeded(Exception):
    """Raised when a queue's budgets reject an enqueue.

    Carries the full Decision so callers can inspect blocked_by /
    unblock_at and choose a retry strategy.
    """

    def __init__(self, queue: str, decision: Decision) -> None:
        self.queue = queue
        self.decision = decision
        binding = ", ".join(
            f"{c.spent}/{c.limit} {c.constraint} in {c.window_str}"
            for c in decision.blocked_by)
        super().__init__(f"queue {queue!r} over budget: {binding}")
