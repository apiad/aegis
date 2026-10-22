"""What one member's subscription to a channel may see (#566; the spec's
section 08): in multi-user mode, only what the member owns. One instance per
subscription (channels.View), made by ``App.view_of``.

A channel about one thing (a transcript, a terminal, a session's repos, a
workflow run) is allowed only when the member owns that thing. A channel of
rows (sessions, workflow runs, schedules) carries only the member's rows, and
a remove only for a row this subscription was sent, so a removal says nothing
about anyone else's. A toast an agent raised reaches its owner only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .ops import Principal

if TYPE_CHECKING:
    from .app import App

# The field that keys a row, in each channel of rows.
ROW_KEYS = {
    "sessions": "log_id",
    "workflow-runs": "run",
    "workflows": "run",
    "schedules": "name",
}


class MemberView:
    def __init__(self, app: App, who: Principal) -> None:
        self._app = app
        self._who = who
        self._held: set[Any] = set()

    def _session(self, log_id: str) -> bool:
        owner = self._app.owner_of(log_id)
        return owner is not None and self._app.sees(self._who, owner)

    def _run(self, run: str) -> bool:
        st = self._app.workflows.runs.get(run)
        return st is not None and self._session(st.owner)

    def _schedule(self, name: str) -> bool:
        s = self._app.schedules.items.get(name)
        return s is not None and self._app.sees(self._who, s.owner.get("principal"))

    def _row(self, channel: str, key: Any) -> bool:
        if channel == "sessions":
            return self._session(key)
        if channel == "schedules":
            return self._schedule(key)
        return self._run(key)

    def allows(self, channel: str) -> bool:
        kind, _, key = channel.partition(":")
        if kind in ("transcript", "repos", "term"):
            return self._session(key)
        if kind == "workflow":
            return self._run(key)
        return True

    def snapshot(self, channel: str, data: Any) -> Any:
        key = ROW_KEYS.get(channel)
        if key is None:
            return data
        rows = [r for r in data if self._row(channel, r[key])]
        self._held = {r[key] for r in rows}
        return rows

    def op(self, channel: str, op: dict) -> dict | None:
        if channel == "toasts":
            t = op.get("toast", {})
            if "owner" in t and not self._app.sees(self._who, t["owner"]):
                return None
            source = t.get("from")
            return op if source is None or self._session(source["id"]) else None
        key = ROW_KEYS.get(channel)
        if key is None:
            return op
        if "upsert" in op:
            k = op["upsert"][key]
            if not self._row(channel, k):
                return None
            self._held.add(k)
            return op
        if "remove" in op:
            if op["remove"] not in self._held:
                return None
            self._held.discard(op["remove"])
        return op
