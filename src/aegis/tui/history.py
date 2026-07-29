"""Ctrl+H history modal — pick a prior session and reopen it.

Dismiss payloads:
    ("jump", handle)     — switch focus to a live tab
    ("resume", row)      — drv.resume() via the existing protocol
    ("open_fresh", row)  — _spawn(profile, cwd=row.cwd)
    None                 — Escape / no action.

Interaction: a filter Input is focused; Up/Down move the highlighted row;
Enter performs the *smart* primary action for the highlighted row (jump if
live, resume if resumable, else open fresh). A row whose profile is missing
from the current agents map is shown dimmed and is non-actionable.
"""
from __future__ import annotations

from typing import Iterable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option

from aegis.state.history import SessionHistoryRow


def _relative_time(iso_ts: str, now_iso: str | None = None) -> str:
    """Best-effort relative-time formatter ('2m ago', '3h ago')."""
    from datetime import datetime, timezone
    try:
        ts = datetime.fromisoformat(iso_ts.rstrip("Z")).replace(
            tzinfo=timezone.utc)
    except ValueError:
        return iso_ts
    now = (datetime.fromisoformat(now_iso.rstrip("Z")).replace(
        tzinfo=timezone.utc) if now_iso else datetime.now(timezone.utc))
    delta = (now - ts).total_seconds()
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _is_resumable(row: SessionHistoryRow, agents: set[str],
                  resume_capable: set[str]) -> bool:
    return (row.profile in agents and not row.is_open
            and row.session_id is not None
            and row.provider in resume_capable)


def _glyph(row: SessionHistoryRow, agents: set[str],
           resume_capable: set[str]) -> str:
    if row.profile not in agents:
        return "⊘"
    if row.is_open:
        return "●"
    if _is_resumable(row, agents, resume_capable):
        return "↻"
    return "○"


def _row_label(row: SessionHistoryRow, agents: set[str],
               resume_capable: set[str]) -> str:
    glyph = _glyph(row, agents, resume_capable)
    rel = _relative_time(row.last_activity_at)
    preview = (row.preview or "").replace("\n", " ")[:40]
    profile = row.profile if row.profile in agents else f"<{row.profile}?>"
    if row.inferred:
        # The log predates SessionMeta, so the profile is the caller's
        # default rather than what actually ran — say so, since resume may
        # well fail on it.
        profile = f"{profile}~"
    return f"{glyph} {row.handle:<18} {profile:<16} {rel:<9} {preview}"


class HistoryModal(ModalScreen):
    """Pick a prior session to reopen."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
    ]

    DEFAULT_CSS = """
    HistoryModal { align: center middle; }
    HistoryModal #hist-box {
        width: 82; max-height: 24;
        border: round $panel; background: $surface; padding: 1 2;
    }
    HistoryModal Input { width: 100%; margin-bottom: 1; border: none;
                         background: $background; }
    HistoryModal OptionList { width: 100%; max-height: 16;
                              border: none; background: $surface; }
    HistoryModal #hist-empty { width: 100%; color: $text-muted;
                               content-align: center middle; height: 3; }
    HistoryModal #hist-help { width: 100%; color: $text-muted;
                              content-align: center middle; }
    """

    def __init__(self, rows: Iterable[SessionHistoryRow], *,
                 agents: set[str],
                 resume_capable_providers: set[str]) -> None:
        super().__init__()
        self._rows = list(rows)
        self._agents = agents
        self._resume_capable = resume_capable_providers

    def compose(self) -> ComposeResult:
        with Vertical(id="hist-box"):
            if not self._rows:
                yield Label("no history yet", id="hist-empty")
                return
            yield Input(placeholder="filter…", id="hist-input")
            yield OptionList(id="hist-list")
            yield Label("↑↓ move · Enter open · Esc cancel", id="hist-help")

    def on_mount(self) -> None:
        if not self._rows:
            return
        self._refresh("")
        self.query_one("#hist-input", Input).focus()

    # --- filtering + list state ------------------------------------

    def _matches(self, row: SessionHistoryRow, needle: str) -> bool:
        if not needle:
            return True
        needle = needle.lower()
        haystack = " ".join(
            [row.handle, row.profile, row.cwd, row.preview]).lower()
        return needle in haystack

    def _refresh(self, needle: str) -> None:
        ol = self.query_one("#hist-list", OptionList)
        ol.clear_options()
        for r in self._rows:
            if not self._matches(r, needle):
                continue
            label = _row_label(r, self._agents, self._resume_capable)
            ol.add_option(Option(label, id=r.handle))
        if ol.option_count > 0:
            ol.highlighted = 0

    def _row_for(self, handle: str | None) -> SessionHistoryRow | None:
        for r in self._rows:
            if r.handle == handle:
                return r
        return None

    def _select(self, handle: str | None) -> None:
        row = self._row_for(handle)
        if row is None or row.profile not in self._agents:
            return  # missing profile → non-actionable
        if row.is_open:
            self.dismiss(("jump", row.handle))
        elif _is_resumable(row, self._agents, self._resume_capable):
            self.dismiss(("resume", row))
        else:
            self.dismiss(("open_fresh", row))

    def _highlighted_handle(self) -> str | None:
        ol = self.query_one("#hist-list", OptionList)
        if ol.highlighted is None or ol.option_count == 0:
            return None
        return ol.get_option_at_index(ol.highlighted).id

    # --- events ----------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh(event.value)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._select(self._highlighted_handle())

    def on_option_list_option_selected(
            self, event: OptionList.OptionSelected) -> None:
        self._select(event.option.id)

    def action_cursor_down(self) -> None:
        ol = self.query_one("#hist-list", OptionList)
        if ol.option_count and ol.highlighted is not None:
            ol.highlighted = min(ol.highlighted + 1, ol.option_count - 1)

    def action_cursor_up(self) -> None:
        ol = self.query_one("#hist-list", OptionList)
        if ol.option_count and ol.highlighted is not None:
            ol.highlighted = max(ol.highlighted - 1, 0)

    def action_cancel(self) -> None:
        self.dismiss(None)
