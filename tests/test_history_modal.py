"""Hermetic tests for HistoryModal — layout, filter, and dismiss outcomes."""
import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label, OptionList

from aegis.state.history import SessionHistoryRow
from aegis.tui.history import HistoryModal, _row_label


def _row(handle: str, *, is_open: bool = False,
         session_id: str | None = None,
         profile: str = "claude-sonnet",
         provider: str = "claude-code",
         log_id: str | None = None,
         preview: str = "hello",
         title: str = "",
         last: str = "2026-05-28T14:00:00Z") -> SessionHistoryRow:
    return SessionHistoryRow(
        log_id=log_id or f"20260528T140000Z-{handle}",
        handle=handle, profile=profile, provider=provider,
        cwd="/tmp", created_at=last, closed_at=None,
        last_activity_at=last, preview=preview,
        session_id=session_id, is_open=is_open, crash_inferred=False,
        title=title)


class _Harness(App):
    def __init__(self, rows, agents, resume_capable=frozenset({"claude-code"})):
        super().__init__()
        self._rows = rows
        self._agents = agents
        self._rc = set(resume_capable)
        self.result = "UNSET"

    def compose(self) -> ComposeResult:
        yield Label("host")

    def on_mount(self) -> None:
        self.push_screen(
            HistoryModal(self._rows, agents=self._agents,
                         resume_capable_providers=self._rc),
            callback=self._store)

    def _store(self, result) -> None:
        self.result = result


def _optlist(app) -> OptionList:
    return app.screen.query_one("#hist-list", OptionList)


def test_inferred_row_marks_its_guessed_profile():
    """An attributed row and a rebuilt one must not look identical — resume
    can genuinely fail on the rebuilt one's guessed profile."""
    from dataclasses import replace
    from aegis.tui.history import _row_label
    agents = {"claude-sonnet"}
    known = _row_label(_row("h1"), agents, {"claude-code"})
    guessed = _row_label(replace(_row("h1"), inferred=True), agents,
                         {"claude-code"})
    assert "claude-sonnet~" in guessed
    assert "~" not in known


@pytest.mark.asyncio
async def test_renders_all_rows():
    app = _Harness([_row("h1"), _row("h2", is_open=True)],
                   agents={"claude-sonnet"})
    async with app.run_test() as pilot:
        await pilot.pause()
        ol = _optlist(app)
        assert ol.option_count == 2
        ids = {ol.get_option_at_index(i).id for i in range(ol.option_count)}
        assert ids == {"20260528T140000Z-h1", "20260528T140000Z-h2"}


@pytest.mark.asyncio
async def test_empty_state():
    app = _Harness([], agents=set())
    async with app.run_test() as pilot:
        await pilot.pause()
        # Empty-state placeholder present; no option list at all.
        assert app.screen.query_one("#hist-empty", Label) is not None
        assert not app.screen.query("#hist-list")


@pytest.mark.asyncio
async def test_filter_narrows_rows():
    app = _Harness([_row("apple"), _row("banana")],
                   agents={"claude-sonnet"})
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a", "p", "p")
        await pilot.pause()
        ol = _optlist(app)
        assert ol.option_count == 1
        assert ol.get_option_at_index(0).id == "20260528T140000Z-apple"


@pytest.mark.asyncio
async def test_enter_open_fresh_for_closed_non_resumable():
    # No session_id → not resumable → open fresh.
    app = _Harness([_row("h1")], agents={"claude-sonnet"})
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        kind, payload = app.result
        assert kind == "open_fresh"
        assert payload.handle == "h1"


@pytest.mark.asyncio
async def test_enter_jump_for_open_row():
    app = _Harness([_row("h1", is_open=True)], agents={"claude-sonnet"})
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.result == ("jump", "h1")


@pytest.mark.asyncio
async def test_enter_resume_for_closed_resumable_row():
    app = _Harness([_row("h1", session_id="up-1")], agents={"claude-sonnet"})
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        kind, payload = app.result
        assert kind == "resume"
        assert payload.handle == "h1"
        assert payload.session_id == "up-1"


@pytest.mark.asyncio
async def test_missing_profile_row_is_non_actionable():
    # Row's profile not in agents → Enter does nothing, modal stays.
    app = _Harness([_row("h1", session_id="up-1", profile="ghost")],
                   agents={"claude-sonnet"})
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.result == "UNSET"
        assert isinstance(app.screen, HistoryModal)


@pytest.mark.asyncio
async def test_reused_handle_lists_every_session():
    """Handles come from a finite pool and get reused across sessions, so two
    logs can carry the same one. Keying the option list by handle made the
    second one a duplicate id: Textual raised DuplicateID out of on_mount,
    truncating the listing at the first collision and killing the app."""
    rows = [_row("recycled", log_id="20260528T140000Z-recycled"),
            _row("other", log_id="20260527T090000Z-other"),
            _row("recycled", log_id="20260526T080000Z-recycled")]
    app = _Harness(rows, agents={"claude-sonnet"})
    async with app.run_test() as pilot:
        await pilot.pause()
        ol = _optlist(app)
        assert ol.option_count == 3
        ids = {ol.get_option_at_index(i).id for i in range(ol.option_count)}
        assert ids == {"20260528T140000Z-recycled",
                       "20260527T090000Z-other",
                       "20260526T080000Z-recycled"}


@pytest.mark.asyncio
async def test_reused_handle_opens_the_row_you_picked():
    """Two sessions share a handle; the second must resolve to its own row.
    Looking the selection up by handle returned the first match, silently
    reopening a different conversation than the one highlighted."""
    rows = [_row("recycled", log_id="new-one", session_id="sid-new"),
            _row("recycled", log_id="old-one", session_id="sid-old")]
    app = _Harness(rows, agents={"claude-sonnet"})
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")      # highlight the second (older) row
        await pilot.press("enter")
        await pilot.pause()
        kind, payload = app.result
        assert kind == "resume"
        assert payload.log_id == "old-one"
        assert payload.session_id == "sid-old"


@pytest.mark.asyncio
async def test_escape_dismisses_none():
    app = _Harness([_row("h1")], agents={"claude-sonnet"})
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.result is None


# ---- session titles ----
#
# The title is what a session was *about*; the preview is only the first
# thing anyone happened to say. When both exist the title is the useful one.

_AGENTS = {"claude-sonnet"}
_RESUMABLE = {"claude-code"}


def test_row_label_prefers_the_title_over_the_preview():
    row = _row("lucid-knuth", title="eviction race",
               preview="hey can you look at the cache thing")
    label = _row_label(row, _AGENTS, _RESUMABLE)
    assert "eviction race" in label
    assert "hey can you look" not in label


def test_row_label_falls_back_to_the_preview_without_a_title():
    row = _row("lucid-knuth", title="",
               preview="hey can you look at the cache thing")
    label = _row_label(row, _AGENTS, _RESUMABLE)
    assert "hey can you look" in label


def test_the_filter_matches_on_the_title():
    # _matches touches no DOM, so this needs no running App.
    modal = HistoryModal(
        [_row("lucid-knuth", title="eviction race",
              preview="unrelated words")],
        agents=_AGENTS, resume_capable_providers=_RESUMABLE)
    assert modal._matches(modal._rows[0], "eviction") is True
    assert modal._matches(modal._rows[0], "nonsense") is False
