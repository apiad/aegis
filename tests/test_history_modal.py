"""Hermetic tests for HistoryModal — layout, filter, and dismiss outcomes."""
import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label, OptionList

from aegis.state.history import SessionHistoryRow
from aegis.tui.history import HistoryModal


def _row(handle: str, *, is_open: bool = False,
         session_id: str | None = None,
         profile: str = "claude-sonnet",
         provider: str = "claude-code",
         last: str = "2026-05-28T14:00:00Z") -> SessionHistoryRow:
    return SessionHistoryRow(
        handle=handle, profile=profile, provider=provider,
        cwd="/tmp", created_at=last, closed_at=None,
        last_activity_at=last, preview="hello",
        session_id=session_id, is_open=is_open, crash_inferred=False)


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
        assert ids == {"h1", "h2"}


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
        assert ol.get_option_at_index(0).id == "apple"


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
async def test_escape_dismisses_none():
    app = _Harness([_row("h1")], agents={"claude-sonnet"})
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.result is None
