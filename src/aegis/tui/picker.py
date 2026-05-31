from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option


def filter_path_tokens(
        tokens: list[str], cwd: Path,
        indexed_paths: list[str]) -> list[str]:
    """Keep tokens that look like paths to files; drop everything else.

    Absolute tokens whose resolved form lives under ``cwd`` are
    re-rooted to the relative form so picker matching against the
    relative-path index works.

    A token counts as a path when any of:
      - ``cwd / token`` is an existing file,
      - the token is absolute and exists on disk,
      - the token matches an entry in ``indexed_paths`` exactly or by
        ``"/" + token`` suffix.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in tokens:
        norm = _normalize_token(raw, cwd)
        if norm is None or norm in seen:
            continue
        if not _is_path_like(norm, cwd, indexed_paths):
            continue
        seen.add(norm)
        out.append(norm)
    return out


def _normalize_token(raw: str, cwd: Path) -> str | None:
    t = raw.strip()
    if not t:
        return None
    p = Path(t)
    if p.is_absolute():
        try:
            return str(p.resolve().relative_to(cwd.resolve()))
        except (ValueError, OSError):
            return t
    return t


def _is_path_like(
        token: str, cwd: Path, indexed_paths: list[str]) -> bool:
    try:
        if (cwd / token).is_file():
            return True
    except OSError:
        pass
    p = Path(token)
    if p.is_absolute():
        try:
            if p.is_file():
                return True
        except OSError:
            pass
    return any(ip == token or ip.endswith("/" + token)
               for ip in indexed_paths)


def resolve_unique_match(token: str, paths: list[str]) -> str | None:
    """If exactly one indexed path matches the token, return it; else None.

    A path matches when it equals the token or ends with ``"/" + token``
    — so a bare basename resolves only when unique across the index.
    """
    candidates = [p for p in paths
                  if p == token or p.endswith("/" + token)]
    return candidates[0] if len(candidates) == 1 else None


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

    BINDINGS = [
        Binding("down", "highlight_next", show=False, priority=True),
        Binding("up", "highlight_prev", show=False, priority=True),
        Binding("pagedown", "highlight_page", "page", show=False,
                priority=True),
        Binding("pageup", "highlight_page_up", show=False, priority=True),
        Binding("escape", "cancel", show=False, priority=True),
    ]

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
        self._booted = False
        self._poll_timer = None

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
            self._poll_timer = self.set_interval(0.15, self._poll_indexer)
            self.query_one("#fp-input", Input).focus()
        else:
            self._sync_walk()
            self._boot_input()

    def _boot_input(self) -> None:
        if self._booted:
            return
        self._booted = True
        inp = self.query_one("#fp-input", Input)
        if self._prefill:
            inp.value = self._prefill
        inp.focus()
        self._filter(self._prefill)

    def _poll_indexer(self) -> None:
        indexer = getattr(self.app, "_file_indexer", None)
        if indexer is None or not indexer.ready:
            return
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
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
        seen: set[str] = set()
        for p in matches[:50]:
            if p in seen:
                continue
            seen.add(p)
            ol.add_option(Option(p, id=p))
        if ol.option_count > 0:
            ol.highlighted = 0

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

    def action_cancel(self) -> None:
        self.dismiss(None)

    def key_enter(self) -> None:
        self._select_highlighted()

    def _move_highlight(self, delta: int) -> None:
        ol = self.query_one("#fp-list", OptionList)
        if ol.option_count == 0:
            return
        cur = ol.highlighted if ol.highlighted is not None else 0
        ol.highlighted = max(0, min(ol.option_count - 1, cur + delta))

    def action_highlight_next(self) -> None:
        self._move_highlight(1)

    def action_highlight_prev(self) -> None:
        self._move_highlight(-1)

    def action_highlight_page(self) -> None:
        self._move_highlight(10)

    def action_highlight_page_up(self) -> None:
        self._move_highlight(-10)


class _TokenChooser(ModalScreen):
    """Pick one backtick token from a list — routes to FilePickerModal."""

    DEFAULT_CSS = """
    _TokenChooser { align: center middle; }
    _TokenChooser OptionList {
        width: 50; max-height: 16;
        border: round $panel; background: $surface;
    }
    """

    def __init__(self, tokens: list[str]) -> None:
        super().__init__()
        # Defensive dedup: OptionList raises DuplicateID on repeated ids,
        # and a repeated token has no distinct action anyway.
        seen: set[str] = set()
        self._tokens = [t for t in tokens
                        if not (t in seen or seen.add(t))]

    def compose(self) -> ComposeResult:
        yield OptionList(*[Option(t, id=t) for t in self._tokens])

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(
            self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def key_escape(self) -> None:
        self.dismiss(None)
