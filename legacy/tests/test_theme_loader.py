from pathlib import Path

from aegis.themes import (
    AegisColors, AegisTheme, aegis_colors, load_theme, list_theme_names,
)


def test_load_ink_reproduces_textual_theme():
    t = load_theme("aegis-ink")
    assert isinstance(t, AegisTheme)
    tt = t.to_textual_theme()
    assert tt.name == "aegis-ink"
    assert tt.dark is True
    assert tt.background == "#0e0e0d"
    assert tt.foreground == "#DCD9CF"
    assert tt.accent == "#E0A872"
    assert tt.success == "#9DB07E"
    assert tt.variables["aegis-muted"] == "#76736a"
    assert tt.variables["aegis-userbg"] == "#24241f"


def test_to_aegis_colors_matches_golden():
    c = load_theme("aegis-ink").to_aegis_colors()
    assert isinstance(c, AegisColors)
    assert c.ready == "#9DB07E"
    assert c.working == "#E0A872"
    assert c.error == "#C56B5C"
    assert c.accent == "#E0A872"
    assert c.muted == "#76736a"
    assert c.user_bg == "#24241f"


def test_to_css_variables_emits_expected_vars():
    css = load_theme("aegis-ink").to_css_variables()
    assert ":root" in css
    assert "--aegis-bg: #0e0e0d" in css
    assert "--aegis-fg: #DCD9CF" in css
    assert "--aegis-accent: #E0A872" in css
    assert "--aegis-muted: #76736a" in css
    assert "--aegis-user-bg: #24241f" in css
    assert "--aegis-ok: #9DB07E" in css
    assert "--aegis-err: #C56B5C" in css


def test_all_three_bundled_themes_load():
    names = list_theme_names()
    assert {"aegis-ink", "aegis-parchment", "aegis-slate"} <= set(names)
    for name in ("aegis-ink", "aegis-parchment", "aegis-slate"):
        assert load_theme(name).to_textual_theme().name == name


def test_missing_theme_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        load_theme("does-not-exist")


def test_overlay_merges_over_base(tmp_path: Path):
    overlay = tmp_path / "aegis-ink.yaml"
    overlay.write_text("colors:\n  accent: \"#ABCDEF\"\n", encoding="utf-8")
    t = load_theme("aegis-ink", user_dir=tmp_path)
    # Overlaid key wins; untouched keys keep base values.
    assert t.colors["accent"] == "#ABCDEF"
    assert t.colors["background"] == "#0e0e0d"
    assert t.variables["aegis-muted"] == "#76736a"
