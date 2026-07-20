"""Pure renderer for the monitor strip."""
from __future__ import annotations

from aegis.monitor.schema import MonitorView
from aegis.tui.monitor_strip import _bar, render_monitors
from aegis.tui.themes import INK, aegis_colors


def _p():
    return aegis_colors(INK)


def test_empty_renders_blank():
    assert render_monitors([], _p()).plain == ""


def test_progress_monitor_shows_bar_pct_eta():
    v = MonitorView(id="a", description="pytest", state="watching",
                    pct=62.0, eta_s=18.0, elapsed_s=30.0)
    out = render_monitors([v], _p()).plain
    assert "pytest" in out
    assert "62%" in out
    assert "ETA 0:18" in out


def test_no_progress_monitor_shows_watching():
    v = MonitorView(id="a", description="dev server", state="watching",
                    pct=None, eta_s=None, elapsed_s=42.0)
    out = render_monitors([v], _p()).plain
    assert "dev server" in out
    assert "watching" in out
    assert "0:42" in out
    assert "%" not in out


def test_multiple_monitors_joined():
    vs = [
        MonitorView(id="a", description="build", state="watching",
                    pct=10.0, eta_s=None, elapsed_s=1.0),
        MonitorView(id="b", description="dl", state="watching",
                    pct=None, eta_s=None, elapsed_s=2.0),
    ]
    out = render_monitors(vs, _p()).plain
    assert "build" in out and "dl" in out


def test_bar_fill_ratio():
    assert _bar(0, width=8) == "░" * 8
    assert _bar(100, width=8) == "▓" * 8
    assert _bar(50, width=8) == "▓" * 4 + "░" * 4
