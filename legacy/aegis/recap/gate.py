"""When a recap is worth firing, and when it is worth drawing.

Claude Code gates its recap on turn count (>=3 turns, "never twice in a
row") and anthropics/claude-code#56346 reports the predictable result: in
a conversation of questions and reads, 10+ identical recaps accumulate.

So the recap is paid for on every turn (unless recaps are off), because
the fleet card's ``did`` line should always say what the last turn did
(operator ruling, 2026-09-16). Only a turn that moved the substrate draws its recap
into the transcript, which keeps a conversation of questions free of
repeated lines. Ten identical drawn recaps would still require ten turns
that each changed something and each changed it the same way.

The cost of recapping every turn is accepted: measured 2026-09-16, one
turn recap is $0.007-0.015 on haiku.
"""

from __future__ import annotations

from aegis.digest.models import TurnFacts


def should_draw_recap(facts: TurnFacts) -> bool:
    """True when the recap belongs in the transcript: the turn moved the
    substrate. An errored digest is not movement (see ``TurnFacts.moved``).
    The identity guard against a repeated line is applied by the caller,
    once the new line has been paid for."""
    return facts.moved


def should_fleet_recap(*, state, turn_s, since_last_s, watchers, cfg) -> bool:
    """When a mid-turn recap is worth firing.

    Four conditions, all required. `watchers` is how many clients have this
    session's card or sidebar on screen: nobody looking, nobody pays, which
    is what makes a dashboard left open all day affordable at all.
    """
    if cfg.recap == "off":
        return False
    if state != "working":
        return False
    if watchers < 1:
        return False
    if turn_s < cfg.recap_after_s:
        return False
    return since_last_s >= cfg.recap_interval_s
