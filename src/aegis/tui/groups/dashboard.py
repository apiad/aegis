"""GroupDashboard — the body rendered when a group tab is focused.

Three panels stacked: Members, Current broadcast, Recent broadcasts.
Pure render — reads from a snapshot dataclass populated by the
GroupTabState observer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from textwrap import dedent

from textual.widget import Widget


@dataclass(frozen=True)
class MemberRow:
    handle: str
    state: str
    detail: str


@dataclass(frozen=True)
class BroadcastRow:
    id: str
    mode: str
    status: str
    started: str
    summary: str


@dataclass(frozen=True)
class DashboardSnapshot:
    name: str
    members: list[MemberRow] = field(default_factory=list)
    current: BroadcastRow | None = None
    recent: list[BroadcastRow] = field(default_factory=list)


def state_glyph(s: str) -> str:
    return {"idle": "✓", "busy": "⏳", "errored": "⚠", "lost": "⛔"}.get(s, "?")


def render_dashboard(snap: DashboardSnapshot) -> str:
    members = "\n".join(
        f"  {state_glyph(m.state)} {m.handle:<18} {m.state:<8} · {m.detail}"
        for m in snap.members
    ) or "  (no members)"
    if snap.current:
        c = snap.current
        current = dedent(f"""\
            Current broadcast
              id   {c.id} · started {c.started} · mode: {c.mode}
              {c.summary}
        """).rstrip()
    else:
        current = "Current broadcast\n  (no broadcast in flight)"
    recent = "\n".join(
        f"  {r.id} {r.status} {r.started}  {r.mode:<9} {r.summary}"
        for r in snap.recent
    ) or "  (no broadcasts yet)"
    return (
        f"▣ {snap.name} — {len(snap.members)} members\n\n"
        f"Members\n{members}\n\n"
        f"{current}\n\n"
        f"Recent broadcasts\n{recent}\n"
    )


class GroupDashboard(Widget):
    def __init__(self, snap: DashboardSnapshot, **kw):
        super().__init__(**kw)
        self._snap = snap

    def render(self) -> str:
        return render_dashboard(self._snap)
