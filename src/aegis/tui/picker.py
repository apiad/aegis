from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option


class AgentPicker(ModalScreen[str | None]):
    DEFAULT_CSS = """
    AgentPicker { align: center middle; }
    AgentPicker OptionList {
        width: 40; max-height: 16;
        border: round $panel; background: $surface;
    }
    """

    def __init__(self, slugs: list[str]) -> None:
        super().__init__()
        self._slugs = slugs

    def compose(self) -> ComposeResult:
        yield OptionList(*[Option(s, id=s) for s in self._slugs])

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(
            self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def key_escape(self) -> None:
        self.dismiss(None)


class TerminalNamePrompt(ModalScreen[str | None]):
    """Single-Input modal — asks for a terminal name and returns it on
    Enter, or None on Escape."""

    DEFAULT_CSS = """
    TerminalNamePrompt { align: center middle; }
    TerminalNamePrompt Input {
        width: 40; border: round $panel; background: $surface;
    }
    """

    def compose(self) -> ComposeResult:
        yield Input(placeholder="terminal name (e.g. build, dev)…")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        self.dismiss(name or None)

    def key_escape(self) -> None:
        self.dismiss(None)
