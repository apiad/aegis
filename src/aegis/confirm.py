"""One-time tokens that let an agent go ahead after a second thought.

Some things an agent may do should not be its first move: closing a session it
did not start, or one still at work (#278), and later cutting another session's
turn (#206). The first call is refused with a token and with what the agent
should weigh; the same call with that token goes through. A token is bound to
one action, one caller and one target, by log id, and works once.

A token lasts ``TTL_S``, five minutes: it confirms a reading of live state, so
an agent that goes off to ask the person and comes back later gets a fresh
refusal carrying the state as it is then, not a pass on what it read before.
Tokens live in memory only; a restart voids them, which costs one more refusal.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

TTL_S = 300.0
# How long a spent or expired token is remembered, so a retry hears why it no
# longer works rather than that aegis never issued it.
KEEP_S = 3600.0


@dataclass
class _Grant:
    action: str
    caller: str
    target: str
    expires: float
    used: bool = False


class Confirmations:
    def __init__(self) -> None:
        self.clock = time.monotonic
        self._grants: dict[str, _Grant] = {}

    def issue(self, action: str, caller: str, target: str) -> str:
        now = self.clock()
        self._grants = {
            k: g for k, g in self._grants.items() if g.expires + KEEP_S > now
        }
        token = secrets.token_urlsafe(9)
        self._grants[token] = _Grant(action, caller, target, now + TTL_S)
        return token

    def redeem(self, token: str, action: str, caller: str, target: str) -> str | None:
        """None when ``token`` confirms ``action`` by ``caller`` on ``target``;
        otherwise why it does not, as a clause. Its own caller spends it either
        way, so a token never answers twice; another caller's attempt leaves it
        alone, so nobody can void a token they were not given."""
        g = self._grants.get(token)
        if g is None:
            return "aegis did not issue it, or the server restarted since"
        if g.caller != caller:
            return "it was given to another session"
        if g.used:
            return "it was already used once"
        g.used = True
        if self.clock() > g.expires:
            return f"it expired: a token lasts {TTL_S / 60:.0f} minutes"
        if g.action != action:
            return "it was given for another action"
        if g.target != target:
            return "it was given for another session"
        return None
