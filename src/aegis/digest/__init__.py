"""What a turn actually did — the half no transcript window contains.

`/btw`'s window carries what was *said*. The loop judge and the recap both
need what was *done*: commits, files written, plan movement. That is the
whole reason this is one shared collector rather than two private helpers.

Best-effort by contract, like ``titlegen``: any failure yields a
``TurnFacts`` with ``error`` set. A summary must never be able to disturb
the conversation it summarises.
"""
from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.digest.render import render_facts

__all__ = ["CommitLine", "RepoDelta", "TurnFacts", "render_facts"]
