"""ConversationPane command palette: typing `/` drops up a completion panel
above the input, filtering live; a plain message shows no panel."""
from __future__ import annotations

import pytest

from aegis.tui.palette import CommandPalette
from aegis.tui.widgets import GrowingInput
# reuse the pane harness shape from tests/test_pane_slash_command.py
from tests.test_pane_slash_command import GatedSession, _app


def _type(pane, text):
    inp = pane.query_one(GrowingInput)
    inp.text = text
    pane.on_text_area_changed(None)


@pytest.mark.asyncio
async def test_palette_shows_commands_on_slash():
    app = _app(GatedSession())
    async with app.run_test() as pilot:
        pane = app._panes[0]
        _type(pane, "/sp")
        await pilot.pause()
        pal = pane.query_one(CommandPalette)
        assert pal.display is True
        assert any(c.label == "/spawn" for c in pal._items)


@pytest.mark.asyncio
async def test_palette_hidden_for_plain_text():
    app = _app(GatedSession())
    async with app.run_test() as pilot:
        pane = app._panes[0]
        _type(pane, "hello")
        await pilot.pause()
        assert pane.query_one(CommandPalette).display is False


@pytest.mark.asyncio
async def test_palette_accept_splices_command():
    app = _app(GatedSession())
    async with app.run_test() as pilot:
        pane = app._panes[0]
        _type(pane, "/sp")
        await pilot.pause()
        pane._accept_completion(pane.query_one(CommandPalette).current())
        await pilot.pause()
        assert pane.query_one(GrowingInput).value == "/spawn "
