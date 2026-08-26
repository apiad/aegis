"""LoopState — the operator's looping instruction.

`/loop <text>` arms one of these on an AgentSession. It is re-delivered at
every turn boundary at which the session would otherwise settle idle, until
the agent reaps it (``aegis_loop_stop``), the cap is reached, the operator
stops it, the turn is interrupted, or the harness errors.

In-memory and session-scoped by design: a loop does not survive a restart.
Auto-firing a restored loop would mean a cold TUI starts spending tokens at
boot without anyone asking it to.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MAX_ITERATIONS = 20

# A loop that has produced nothing for this many consecutive turns is
# spinning rather than thinking. The judge is told the count; this cap
# only stops the number growing without bound.
STILL_STREAK_CAP = 10


@dataclass
class LoopState:
    """One armed loop. ``iteration`` counts deliveries and is incremented as
    the turn is dispatched, so the Nth delivery reads ``iteration N/max``."""

    text: str
    iteration: int = 0
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    # Consecutive turns that changed nothing. The judge is TOLD this
    # rather than left to infer it: the count is a fact, and inference is
    # what the judge exists to remove.
    still_streak: int = 0
    # What the agent said when it called aegis_loop_stop. Advisory since
    # the judge outranks it; cleared once the judge has seen it, because
    # re-presenting a rejected claim every iteration would bias every
    # later verdict.
    advisory: str = ""

    def exhausted(self) -> bool:
        return self.iteration >= self.max_iterations

    def note(self, facts) -> None:
        """Fold one turn's facts into the still-streak.

        ``facts`` is None before the first turn completes, which counts as
        no movement — nothing has landed yet, and that is true.
        """
        if getattr(facts, "moved", False):
            self.still_streak = 0
        else:
            self.still_streak = min(self.still_streak + 1, STILL_STREAK_CAP)

    def render(self, addendum: str = "") -> str:
        """The body delivered to the agent.

        The instruction is VERBATIM — the previous turn may have ended
        somewhere unhelpful, and the instruction has to be present in the
        turn that acts on it. The judge's addendum is appended, never
        substituted: a judge-authored replacement is the ideal vector for
        reading a looping instruction more narrowly each iteration, which
        is the failure the 2026-07-30 burn is an instance of.

        The old stop-coda is gone. The agent no longer decides; see
        ``aegis.core.loop_judge``.
        """
        if not addendum:
            return self.text
        return f"{self.text}\n\nStill outstanding: {addendum}"

    def status(self) -> dict:
        return {"text": self.text, "iteration": self.iteration,
                "max_iterations": self.max_iterations,
                "still_streak": self.still_streak}
