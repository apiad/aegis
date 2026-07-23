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

_CODA = (
    "\n\nIf this instruction is now fully satisfied, call "
    "aegis_loop_stop(from_handle='{handle}', reason='<why>') and stop. "
    "Otherwise continue."
)


@dataclass
class LoopState:
    """One armed loop. ``iteration`` counts deliveries and is incremented as
    the turn is dispatched, so the Nth delivery reads ``iteration N/max``."""

    text: str
    iteration: int = 0
    max_iterations: int = DEFAULT_MAX_ITERATIONS

    def exhausted(self) -> bool:
        return self.iteration >= self.max_iterations

    def render(self, handle: str) -> str:
        """The body delivered to the agent: the instruction verbatim, plus
        the stop coda. Verbatim matters — the previous turn may have ended
        somewhere unhelpful, and the instruction has to be present in the
        turn that acts on it."""
        return self.text + _CODA.format(handle=handle)

    def status(self) -> dict:
        return {"text": self.text, "iteration": self.iteration,
                "max_iterations": self.max_iterations}
