"""ConversationPane command palette: typing `/` drops up a completion panel
above the input, filtering live; a plain message shows no panel."""
from __future__ import annotations

import pytest

from aegis.tui.palette import CommandPalette, _source_style
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


class _FakePalette:
    accent = "#1111ff"
    ok = "#22cc22"
    working = "#ffaa00"
    muted = "#888888"


def test_source_style_maps_each_source_distinctly():
    p = _FakePalette()
    assert _source_style(p, "user") == p.ok
    assert _source_style(p, "plugin") == p.working
    assert _source_style(p, "builtin") == p.accent
    # unknown falls back to accent (the builtin baseline)
    assert _source_style(p, "?") == p.accent


def test_palette_update_preserves_source():
    from aegis.commands import Completion, Completions
    pal = CommandPalette(_FakePalette())
    pal.update(Completions(items=(
        Completion("/a ", "/a", "s", source="user"),
        Completion("/b ", "/b", "s", source="plugin"),
    )))
    assert [c.source for c in pal._items] == ["user", "plugin"]


# ---------- the `@` spelling ---------------------------------------------
#
# `@handle` is sugar classify_input rewrites into `/peer handle`, so it is a
# command in every way that matters. The pane had FOUR `startswith("/")`
# gates and widening only the submit one left `@` a command you could send
# but not discover: no palette, no outline. Alex found it by typing `@` and
# seeing nothing come up.

@pytest.mark.asyncio
async def test_palette_shows_peer_handles_on_at():
    app = _app(GatedSession())
    async with app.run_test() as pilot:
        pane = app._panes[0]
        _type(pane, "@")
        await pilot.pause()
        pal = pane.query_one(CommandPalette)
        assert pal.display is True, "typing @ raised no palette"
        assert pal._items, "the palette came up empty"
        assert all(c.insert.startswith("@") for c in pal._items)


@pytest.mark.asyncio
async def test_palette_filters_handles_as_you_type():
    app = _app(GatedSession())
    async with app.run_test() as pilot:
        pane = app._panes[0]
        handle = pane.handle
        _type(pane, "@" + handle[:3])
        await pilot.pause()
        pal = pane.query_one(CommandPalette)
        assert pal.display is True
        assert any(handle in c.insert for c in pal._items)


@pytest.mark.asyncio
async def test_an_at_line_reads_as_a_command_not_a_message():
    """The outline colour is the type-time signal that this line will be
    executed rather than sent. `@` earns it for the same reason `/` does."""
    app = _app(GatedSession())
    async with app.run_test() as pilot:
        pane = app._panes[0]
        _type(pane, "@somebody hi")
        await pilot.pause()
        assert pane.has_class("slash-command")


@pytest.mark.asyncio
async def test_a_literal_at_escape_is_a_message_not_a_command():
    """`@@` is the escape — it addresses nobody, so no palette and no
    command outline."""
    app = _app(GatedSession())
    async with app.run_test() as pilot:
        pane = app._panes[0]
        _type(pane, "@@someone")
        await pilot.pause()
        assert pane.query_one(CommandPalette).display is False
        assert not pane.has_class("slash-command")


@pytest.mark.asyncio
async def test_an_email_mid_line_is_not_addressing_anyone():
    app = _app(GatedSession())
    async with app.run_test() as pilot:
        pane = app._panes[0]
        _type(pane, "mail me at a@b.com")
        await pilot.pause()
        assert pane.query_one(CommandPalette).display is False
