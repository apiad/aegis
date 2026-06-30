"""Back-compat shim. Theme data now lives in YAML under
``src/aegis/data/themes/`` and is loaded by ``aegis.themes``. This module
re-exports the names the TUI has always imported, building the Textual
``Theme`` objects from the loaded YAML so existing call sites and snapshot
tests are unaffected.
"""
from __future__ import annotations

from textual.theme import Theme

from aegis.themes import AegisColors, aegis_colors, load_theme

INK: Theme = load_theme("aegis-ink").to_textual_theme()
PARCHMENT: Theme = load_theme("aegis-parchment").to_textual_theme()
SLATE: Theme = load_theme("aegis-slate").to_textual_theme()

THEMES: dict[str, Theme] = {"ink": INK, "parchment": PARCHMENT, "slate": SLATE}
DEFAULT_THEME = "aegis-ink"

__all__ = [
    "INK", "PARCHMENT", "SLATE", "THEMES", "DEFAULT_THEME",
    "AegisColors", "aegis_colors",
]
