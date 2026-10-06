"""Pure renderer for the monitor strip."""
from __future__ import annotations

from aegis.monitor.schema import MonitorView
from aegis.tui.monitor_strip import format_mon, render_monitors
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


def test_multiple_monitors_stack_one_per_line():
    vs = [
        MonitorView(id="a", description="build", state="watching",
                    pct=10.0, eta_s=None, elapsed_s=1.0),
        MonitorView(id="b", description="dl", state="watching",
                    pct=None, eta_s=None, elapsed_s=2.0),
    ]
    lines = render_monitors(vs, _p()).plain.splitlines()
    assert len(lines) == 2
    assert "build" in lines[0] and "dl" in lines[1]
    # Continuation lines align under the first one's description.
    assert lines[0].startswith("monitors: ")
    assert lines[1].startswith(" " * len("monitors: "))


def test_bar_fill_ratio():
    """Through the public row now: `_bar` is gone and the shared
    `fleet.render.bar` draws it, so the fill ratio is asserted where a
    reader can see it rather than on a private helper."""
    def bar_of(pct):
        v = MonitorView(id="m", description="x", state="watching",
                        pct=pct, eta_s=None, elapsed_s=1)
        row = format_mon(v, _p(), 56).plain
        return "".join(ch for ch in row if ch in "█░")

    assert bar_of(0) == "░" * 8
    assert bar_of(100) == "█" * 8
    assert bar_of(50) == "█" * 4 + "░" * 4


def test_a_monitor_bar_is_drawn_the_way_every_other_bar_is():
    """One glyph in the program. A strip that disagrees with the sidebar
    about what a bar looks like is the fault this change removes."""
    v = MonitorView(id="m", description="pytest", state="watching",
                    pct=62.0, eta_s=100, elapsed_s=160)
    row = format_mon(v, _p(), 56).plain
    assert "█" in row and "▓" not in row
