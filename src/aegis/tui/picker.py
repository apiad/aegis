from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import OptionList
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
