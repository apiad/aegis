"""Sessions as objects: ``srv.sessions`` and the ``Session`` handle.

A Session is a live handle on one session, known by its log id: every method
reads or acts on the real session, and ``info()`` is the only snapshot. The
actions go through the operation registry as the server's admin person, so they
get the checks the browser gets. A turn has ended when the session is no
longer busy (Session.busy: not working, not flushing, nothing held); the
``sessions`` channel says when to look again, and a one-second fallback covers
a publish the throttle merged away.
"""

from __future__ import annotations

import asyncio
import builtins
import contextlib
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Literal

from .models import (
    UNSETTLED,
    AnyEntry,
    Event,
    Prose,
    SessionInfo,
    Turn,
    TurnEnded,
    entry,
)

if TYPE_CHECKING:
    from ..server import Server
    from ..session import Session as _Live


class SessionNotFound(LookupError):
    """No open session has that log id or handle."""


class Sessions:
    """The open sessions on one server."""

    def __init__(self, server: Server) -> None:
        self._srv = server

    async def spawn(
        self,
        prompt: str | None = None,
        *,
        agent: str | None = None,
        harness: str | None = None,
        model: str | None = None,
        effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None,
        permission: Literal["read", "write", "auto", "full"] | None = None,
        cwd: str | None = None,
        quiet: bool = False,
    ) -> Session:
        """Start a session. With a prompt its first turn is already running:
        ``wait()`` for its end."""
        fields = {
            "prompt": prompt,
            "agent": agent,
            "harness": harness,
            "model": model,
            "effort": effort,
            "permission": permission,
            "cwd": cwd,
        }
        params: dict[str, object] = {k: v for k, v in fields.items() if v is not None}
        if quiet:
            params["quiet"] = True
        r = await self._srv._call("session.spawn", params)
        return Session(self._srv, r["log_id"])

    async def list(self) -> builtins.list[Session]:
        """Every open session, oldest first."""
        return [
            Session(self._srv, s.log_id) for s in self._srv.app.sessions.open_sessions()
        ]

    async def get(self, ref: str) -> Session:
        """The open session whose log id or handle is ``ref``."""
        reg = self._srv.app.sessions
        s = reg.sessions.get(ref) or reg.by_handle(ref)
        if s is None or s.archived:
            raise SessionNotFound(f"no open session {ref!r}")
        return Session(self._srv, s.log_id)


class Session:
    """A live handle on one session."""

    def __init__(self, server: Server, log_id: str) -> None:
        self._srv = server
        self.log_id = log_id

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Session) and other.log_id == self.log_id

    def __hash__(self) -> int:
        return hash(self.log_id)

    def __repr__(self) -> str:
        return f"Session({self.log_id!r})"

    def _live(self) -> _Live:
        s = self._srv.app.sessions.sessions.get(self.log_id)
        if s is None or s.archived:
            raise SessionNotFound(f"session {self.log_id} is closed")
        return s

    def _rows(self) -> list[dict]:
        snap = self._srv.app.sessions.transcript(self.log_id)
        if snap is None:
            raise SessionNotFound(f"session {self.log_id} is closed")
        return snap()["entries"]

    # -- reading --------------------------------------------------------------
    async def info(self) -> SessionInfo:
        """The session's card, now."""
        return SessionInfo.model_validate(self._live().wire())

    async def entries(self) -> builtins.list[AnyEntry]:
        """The whole transcript as it is now."""
        return [entry(r) for r in self._rows()]

    # -- acting ---------------------------------------------------------------
    async def send(self, text: str) -> None:
        """Deliver a message, waking the session if it sleeps. Returns once the
        message is accepted, not when the turn ends."""
        await self._srv._call("session.send", {"log_id": self.log_id, "text": text})

    async def ask(self, text: str, *, timeout: float | None = None) -> Turn:
        """Send ``text`` and wait for the turn it starts to end."""
        before = {r["id"] for r in self._rows()}
        cost = self._live().wire().get("cost_usd") or 0.0
        await self.send(text)
        await self._until_idle(timeout)
        return self._turn(lambda rows: [r for r in rows if r["id"] not in before], cost)

    async def wait(self, *, timeout: float | None = None) -> Turn:
        """Wait until the session is no longer working; the turn that ended,
        or the last one when none was running."""
        await self._until_idle(timeout)
        return self._turn(_since_last_user, None)

    async def interrupt(self) -> None:
        """Stop the current turn; the session stays."""
        await self._srv._call("session.interrupt", {"log_id": self.log_id})

    async def stop(self) -> None:
        """Stop the harness process; the next ``send`` starts it again."""
        await self._srv._call("session.stop", {"log_id": self.log_id})

    async def close(self) -> None:
        """End the session; it stays in the archive."""
        await self._srv._call("session.close", {"log_id": self.log_id})

    async def rename(self, handle: str | None = None, title: str | None = None) -> None:
        """Set the handle, the title, or both."""
        params = {"log_id": self.log_id, "handle": handle, "title": title}
        await self._srv._call(
            "session.rename", {k: v for k, v in params.items() if v is not None}
        )

    # -- events ---------------------------------------------------------------
    async def events(self, *, history: bool = False) -> AsyncIterator[Event]:
        """Each transcript entry once it settles, and ``TurnEnded`` when a turn
        ends. Starts with new entries, or with every entry when ``history``.
        An entry that changes after it settled arrives again with the same id."""
        app = self._srv.app
        q: asyncio.Queue[tuple[str, dict | None]] = asyncio.Queue()
        tsub = app.channels.subscribe(
            f"transcript:{self.log_id}", lambda m: q.put_nowait(("t", m))
        )
        ssub = app.channels.subscribe("sessions", lambda _m: q.put_nowait(("s", None)))
        seen: dict[str, tuple] = {}
        busy = self._live().busy
        try:
            while True:
                src, m = await q.get()
                if src == "s":
                    s = app.sessions.sessions.get(self.log_id)
                    if s is None or s.archived:
                        return
                    if busy and not s.busy:
                        yield TurnEnded(turn=self._turn(_since_last_user, None))
                    busy = s.busy
                    continue
                assert m is not None
                snapshot = m.get("t") == "snapshot"
                rows = (
                    m["data"]["entries"]
                    if snapshot
                    else [op["upsert"] for op in m.get("ops", []) if "upsert" in op]
                )
                for r in rows:
                    if r.get("status") in UNSETTLED:
                        continue
                    key = (r.get("status"), r.get("md"), r.get("summary"))
                    if seen.get(r["id"]) == key:
                        continue
                    seen[r["id"]] = key
                    if snapshot and not history:
                        continue
                    yield entry(r)
        finally:
            app.channels.unsubscribe(tsub)
            app.channels.unsubscribe(ssub)

    # -- helpers --------------------------------------------------------------
    async def _until_idle(self, timeout: float | None) -> None:
        app = self._srv.app
        changed = asyncio.Event()
        sub = app.channels.subscribe("sessions", lambda _m: changed.set())
        try:
            async with asyncio.timeout(timeout):
                while self._live().busy:
                    changed.clear()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(changed.wait(), 1.0)
        finally:
            app.channels.unsubscribe(sub)

    def _turn(self, pick, cost_before: float | None) -> Turn:
        rows = [r for r in pick(self._rows()) if r.get("status") not in UNSETTLED]
        entries = [entry(r) for r in rows]
        w = self._live().wire()
        cost = w.get("cost_usd")
        return Turn(
            attention=w.get("attention"),
            line=w.get("attention_line") or "",
            replies=w.get("replies") or [],
            text="\n\n".join(e.md for e in entries if isinstance(e, Prose) and e.md),
            entries=entries,
            # Known only when ask() read the cost before it sent.
            cost_usd=cost - cost_before
            if cost is not None and cost_before is not None
            else None,
        )


def _since_last_user(rows: list[dict]) -> list[dict]:
    """The rows from the last message to the agent on."""
    for i in range(len(rows) - 1, -1, -1):
        if rows[i].get("kind") == "user":
            return rows[i:]
    return rows
