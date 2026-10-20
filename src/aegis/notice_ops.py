"""``notice.announce``: a notice a person or an admin's agent writes.

An announcement reaches every owner's sessions, so the op is for an admin
only: a member, and an agent in a member's session, is refused. Its author is
the agent's handle or the person's name, and the body says it was written,
not measured. The agent that writes one is marked as told, so its own tool
result does not carry it back. The person's ``/announce`` (App._command)
calls the same op.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from .ops import OpError

if TYPE_CHECKING:
    from .app import App


class Announce(BaseModel):
    model_config = {"extra": "forbid"}

    text: str | None = Field(
        None,
        min_length=1,
        max_length=500,
        description="What every agent on this server should know, in one or two sentences.",
    )
    level: Literal["warn", "critical"] = Field(
        "warn", description="critical also shows the person a toast."
    )
    until: str | None = Field(
        None,
        description="When it ends by itself: HH:MM (today, or tomorrow when "
        "that has passed) or an ISO time. Omitted: until cleared.",
    )
    clear: str | None = Field(None, description="The id of an announcement to end now.")


HHMM = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")


def until_ts(text: str, now: float) -> float:
    """``until`` as a timestamp in the server's local time: ``HH:MM`` is the
    next such minute, an ISO time must be in the future."""
    if m := HHMM.fullmatch(text):
        day = datetime.fromtimestamp(now)
        at = day.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
        if at.timestamp() <= now:
            at += timedelta(days=1)
        return at.timestamp()
    try:
        at = datetime.fromisoformat(text)
    except ValueError:
        raise OpError("bad_until", f"until is HH:MM or an ISO time, not {text!r}")
    if at.timestamp() <= now:
        raise OpError("bad_until", f"{text} has already passed")
    return at.timestamp()


def register_notice_ops(app: App) -> None:
    r = app.registry

    @r.op("notice.announce", Announce, agent=True, admin=True)
    async def notice_announce(p: Announce, caller):
        """Tell every agent on this server something once, on its next
        message or aegis tool result, such as a restart or a freeze. With
        text: adds one and returns it with its id. With clear: ends that one
        now. With neither: lists those in force. For an admin's sessions
        only."""
        store = app.notices.announcements
        if p.text is not None and p.clear is not None:
            raise OpError("bad_params", "give text to announce or clear to end one")
        if p.clear is not None:
            if not store.clear(p.clear):
                raise OpError("unknown_announcement", f"no announcement {p.clear}")
            return {"cleared": p.clear}
        if p.text is None:
            if p.until is not None or "level" in p.model_fields_set:
                raise OpError("bad_params", "until and level go with text")
            return store.active(time.time())
        until = None if p.until is None else until_ts(p.until, time.time())
        s = app.sessions.sessions.get(caller.log_id or "") if caller.is_agent else None
        author = s.handle if s is not None else app.who(caller).name
        a = store.add(p.text, p.level, author, until)
        if s is not None:
            s.notices_sent[f"announce:{a['id']}"] = p.level
            try:
                s._metas.write_soon(s.log_id, s.meta)
            except OSError:  # the in-memory record still holds (notices.py)
                pass
        return a
