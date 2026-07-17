"""Drop-up command palette panel: an OptionList of completions that grows
upward above the input. A pure view over a ``Completions`` — the pane owns the
data flow (calling ``complete()``) and the key routing (Up/Down/Tab/Enter/Esc);
this widget only renders and tracks the highlight."""
from __future__ import annotations

from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from aegis.commands import Completion, Completions


def _source_style(palette, source: str) -> str:
    """Label colour by command origin: user → success/green, plugin →
    working/amber, builtin (default) → accent."""
    if source == "user":
        return palette.ok
    if source == "plugin":
        return palette.working
    return palette.accent


class CommandPalette(OptionList):
    DEFAULT_CSS = """
    CommandPalette {
        display: none; height: auto; max-height: 8;
        border: round $accent; background: $surface;
    }
    """

    def __init__(self, palette) -> None:
        super().__init__()
        self._palette = palette
        self._items: list[Completion] = []
        self._hint: str = ""

    def update(self, completions: Completions) -> None:
        self._items = list(completions.items)
        self._hint = completions.hint
        self.clear_options()
        if not self._items:
            self.display = False
            return
        rows = []
        for c in self._items:
            t = Text(c.label,
                     style=_source_style(self._palette,
                                         getattr(c, "source", "builtin")))
            if c.detail:
                t.append(f"   {c.detail}", style=self._palette.muted)
            rows.append(Option(t))
        self.add_options(rows)
        self.display = True
        self.highlighted = 0

    def move(self, delta: int) -> None:
        if not self._items:
            return
        n = len(self._items)
        cur = self.highlighted if self.highlighted is not None else 0
        self.highlighted = (cur + delta) % n

    def current(self) -> Completion | None:
        if not self._items or self.highlighted is None:
            return None
        return self._items[self.highlighted]

    def hide(self) -> None:
        self.display = False
        self._items = []
