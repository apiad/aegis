from textual.theme import Theme
from aegis.tui.themes import (
    INK, PARCHMENT, SLATE, THEMES, DEFAULT_THEME, AegisColors, aegis_colors,
)


def test_registry_has_default():
    assert THEMES["ink"] is INK
    assert INK.name == DEFAULT_THEME == "aegis-ink"


def test_registry_has_parchment_and_slate():
    assert THEMES["parchment"] is PARCHMENT
    assert THEMES["slate"] is SLATE
    assert PARCHMENT.name == "aegis-parchment"
    assert SLATE.name == "aegis-slate"
    assert PARCHMENT.dark and SLATE.dark


def test_new_themes_map_all_roles_distinctly():
    for theme in (PARCHMENT, SLATE):
        c = aegis_colors(theme)
        assert isinstance(c, AegisColors)
        for v in (c.ready, c.working, c.error, c.accent,
                  c.muted, c.ok, c.err, c.user, c.user_bg):
            assert isinstance(v, str) and v.startswith("#")
        assert c.ready == theme.success
        assert c.working == theme.warning
        assert c.error == theme.error
        assert c.accent == theme.accent
        assert c.user_bg != theme.background      # genuinely lighter band
        assert c.muted != theme.foreground        # aegis-muted is set


def test_aegis_colors_maps_all_roles():
    c = aegis_colors(INK)
    assert isinstance(c, AegisColors)
    for v in (c.ready, c.working, c.error, c.accent,
              c.muted, c.ok, c.err, c.user, c.user_bg):
        assert isinstance(v, str) and v.startswith("#")
    assert c.ready == INK.success
    assert c.working == INK.warning
    assert c.error == INK.error
    assert c.accent == INK.accent
    assert c.muted == "#76736a"
    assert c.user_bg == "#24241f"
    assert c.user_bg != INK.background      # genuinely lighter band


def test_missing_variable_falls_back_to_foreground():
    bare = Theme(name="bare", primary="#111111", foreground="#abcdef")
    c = aegis_colors(bare)
    assert c.muted == "#abcdef"
