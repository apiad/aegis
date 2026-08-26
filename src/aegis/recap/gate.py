"""When a recap is worth firing.

Claude Code gates its recap on turn count (>=3 turns, "never twice in a
row") and anthropics/claude-code#56346 reports the predictable result: in
a conversation of questions and reads, 10+ identical recaps accumulate.

Gate on substrate movement instead. Ten identical recaps would require ten
turns that each changed something and each changed it the same way.

This is a cost requirement, not a nicety. Measured 2026-08-26, one recap
is ~6,900 input tokens; without the gate that is every turn rather than
every productive turn.
"""
from __future__ import annotations

from aegis.digest.models import TurnFacts


def should_recap(facts: TurnFacts, *, last_line: str,
                 enabled: bool) -> bool:
    """True when this turn earned a recap.

    ``last_line`` is the previous recap's text. It cannot be compared here
    — we do not know the new line until we have paid for it — so the
    identity guard is applied after generation, in the caller. It is taken
    as a parameter anyway so the gate reads as the one place the policy
    lives.
    """
    if not enabled:
        return False
    return facts.moved
