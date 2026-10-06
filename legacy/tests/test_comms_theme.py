"""The aegis layer gets its own colour, and it costs no theme YAML."""
from __future__ import annotations

import pytest

from aegis.themes import list_theme_names, load_theme


@pytest.mark.parametrize("name", list_theme_names())
def test_every_bundled_theme_yields_a_comms_colour(name):
    colors = load_theme(name).to_aegis_colors()
    assert colors.comms
    assert colors.comms.startswith("#")


@pytest.mark.parametrize("name", list_theme_names())
def test_comms_is_the_theme_primary(name):
    theme = load_theme(name)
    assert theme.to_aegis_colors().comms == theme.colors["primary"]


def test_comms_falls_back_to_the_accent_when_primary_is_empty():
    """Textual's Theme requires `primary` positionally, so it can never be
    missing — but it can be empty, and an empty colour string paints
    nothing. Degrade to the accent rather than to an invisible glyph."""
    from textual.theme import Theme as TextualTheme

    from aegis.themes import aegis_colors
    bare = TextualTheme(name="bare", dark=True, primary="",
                        foreground="#DDDDDD", accent="#FF00FF")
    assert aegis_colors(bare).comms == "#FF00FF"
