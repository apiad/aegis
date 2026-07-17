"""Canonical aegis theme ids, in a Textual-free module so the harness-agnostic
commands core can list themes without importing ``aegis.tui.themes`` (which
imports Textual). Mirrors the keys of ``aegis.tui.themes.THEMES`` in their
full Textual-id form."""
from __future__ import annotations

THEME_NAMES: tuple[str, ...] = ("aegis-ink", "aegis-parchment", "aegis-slate")
