"""TUI-side group tab state + presentation helpers."""
from __future__ import annotations

from dataclasses import dataclass, field


def aggregate_state_emoji(member_states: list[tuple[str, str]]) -> str:
    states = {s for _, s in member_states}
    if "lost" in states:
        return "⛔"
    if "errored" in states:
        return "⚠"
    if "busy" in states:
        return "⏳"
    return "✓"


@dataclass
class GroupTabState:
    """Minimum fields the tab bar reads for a group tab."""

    name: str
    member_states: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.member_states)

    @property
    def active(self) -> int:
        return sum(1 for _, s in self.member_states if s == "busy")

    @property
    def emoji(self) -> str:
        return aggregate_state_emoji(self.member_states)

    def tab_label(self) -> str:
        return f"▣ {self.name} [{self.active}/{self.total} {self.emoji}]"
