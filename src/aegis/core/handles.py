"""One authority for who owns which agent handle.

A handle is not free the moment nobody answers to it. ``ConversationPane``
keys its Textual DOM id on the handle it was *born* with
(``id=f"pane-{handle}"``), and Textual ids are immutable and unique within a
parent — so a renamed session keeps occupying its birth name in the DOM for
its whole life, while every mint site that scored candidates against "the
handles sessions carry right now" saw that name as available again. Minting
it a second time raises ``DuplicateIds`` out of the mount worker and takes
the app down. That is the ``/spawn`` crash.

The same shape bites the headless planes more quietly: monitors, reminders,
claims, MCP tokens and history rows are all keyed by handle, so a recycled
name silently inherits a dead session's wakes.

So the rule this class enforces is stronger than "unique among the living":

    a handle bound at any point in this process is never handed to a
    different session again.

Ownership is tracked by *birth* handle. A session renamed three times owns
all four of its names, which is what lets it rename back into one of them
while still refusing every other session the same name.

Retirement is per-process and deliberately not persisted: the constraint
exists to protect live DOM ids and live in-memory planes, both of which die
with the process. Across restarts the transcript is keyed by ``log_id``, not
by handle, so nothing downstream needs the set to survive.
"""
from __future__ import annotations

from collections.abc import Iterable

from aegis.tui.names import generate_name


class HandleRegistry:
    """Every handle this process has bound, mapped to its owner."""

    def __init__(self) -> None:
        # handle -> owner id (the owner's birth handle)
        self._owner: dict[str, str] = {}

    def mint(self, live: Iterable[str] = ()) -> str:
        """Return a fresh handle and record it as its own owner.

        ``live`` is unioned in as belt and braces: every site is expected to
        reserve the handles it chooses itself, and a site that forgets would
        otherwise reintroduce the collision this class exists to stop.
        """
        handle = generate_name(set(self._owner) | set(live))
        self._owner[handle] = handle
        return handle

    def reserve(self, handle: str, *, owner: str | None = None) -> str:
        """Record an externally-chosen handle — a restored tab, a queue
        worker, an explicit ``spawn(handle=...)``. Idempotent; returns the
        owner id, which is the existing one when the handle is already
        known."""
        own = owner or self._owner.get(handle) or handle
        self._owner[handle] = own
        return own

    def owner(self, handle: str) -> str | None:
        """The owner id of ``handle``, or None if it was never bound."""
        return self._owner.get(handle)

    def claimable_by(self, handle: str, holder: str) -> bool:
        """Whether the session currently answering to ``holder`` may take
        ``handle``. True when nobody ever held it, or when it is one of this
        session's own former names."""
        held = self._owner.get(handle)
        return held is None or held == self._owner.get(holder, holder)

    def rename(self, old: str, new: str) -> None:
        """Bind ``new`` to ``old``'s owner. ``old`` stays retired under that
        same owner — its DOM id has not moved."""
        self._owner[new] = self._owner.get(old, old)

    @property
    def known(self) -> set[str]:
        """Every handle bound so far. Diagnostics and tests."""
        return set(self._owner)
