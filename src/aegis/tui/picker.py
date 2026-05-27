from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
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


class FilePickerModal(ModalScreen):
    """Fuzzy file picker. Dismisses with a resolved Path or None."""

    DEFAULT_CSS = """
    FilePickerModal { align: center middle; }
    FilePickerModal #fp-box {
        width: 70; max-height: 22;
        border: round $panel; background: $surface; padding: 1 2;
    }
    FilePickerModal Input { width: 100%; margin-bottom: 1; border: none;
                            background: $background; }
    FilePickerModal OptionList { width: 100%; max-height: 16;
                                 border: none; background: $surface; }
    """

    def __init__(self, prefill: str = "") -> None:
        super().__init__()
        self._prefill = prefill
        self._all_paths: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="fp-box"):
            yield Input(placeholder="type to filter files…", id="fp-input")
            yield OptionList(id="fp-list")

    def on_mount(self) -> None:
        indexer = getattr(self.app, "_file_indexer", None)
        if indexer is not None and indexer.ready:
            self._all_paths = indexer.paths
            self._boot_input()
        elif indexer is not None:
            ol = self.query_one("#fp-list", OptionList)
            ol.add_option(Option("⏳ indexing files…", id=None))
            self.set_interval(0.15, self._poll_indexer)
            self.query_one("#fp-input", Input).focus()
        else:
            self._sync_walk()
            self._boot_input()

    def _boot_input(self) -> None:
        inp = self.query_one("#fp-input", Input)
        if self._prefill:
            inp.value = self._prefill
        inp.focus()
        self._filter(self._prefill)

    def _poll_indexer(self) -> None:
        indexer = getattr(self.app, "_file_indexer", None)
        if indexer is None or not indexer.ready:
            return
        self._all_paths = indexer.paths
        self._boot_input()

    def _sync_walk(self) -> None:
        """Fallback synchronous walk (used when no FileIndexer is attached)."""
        cwd = Path.cwd()
        paths: list[str] = []
        try:
            for p in sorted(cwd.rglob("*")):
                if p.is_file():
                    try:
                        paths.append(str(p.relative_to(cwd)))
                    except ValueError:
                        paths.append(str(p))
                if len(paths) >= 5000:
                    break
        except PermissionError:
            pass
        self._all_paths = paths

    def _filter(self, text: str) -> None:
        ol = self.query_one("#fp-list", OptionList)
        ol.clear_options()
        needle = text.lower()
        matches = (
            [p for p in self._all_paths if needle in p.lower()]
            if needle
            else self._all_paths[:50]
        )
        for p in matches[:50]:
            ol.add_option(Option(p, id=p))

    def on_input_changed(self, event: Input.Changed) -> None:
        self._filter(event.value)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._select_highlighted()

    def on_option_list_option_selected(
            self, event: OptionList.OptionSelected) -> None:
        opt_id = event.option.id
        if opt_id:
            self.dismiss(Path.cwd() / opt_id)
        else:
            self.dismiss(None)

    def _select_highlighted(self) -> None:
        ol = self.query_one("#fp-list", OptionList)
        try:
            highlighted = ol.highlighted
            if highlighted is not None:
                opt = ol.get_option_at_index(highlighted)
                if opt.id:
                    self.dismiss(Path.cwd() / opt.id)
                    return
        except Exception:
            pass
        self.dismiss(None)

    def key_escape(self) -> None:
        self.dismiss(None)

    def key_enter(self) -> None:
        self._select_highlighted()
