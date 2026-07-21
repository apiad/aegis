"""System-stats status-bar segment: sampling + formatting + StatusBar wiring."""
from __future__ import annotations

import re

from aegis.tui.sysmeter import SystemStats, format_system, sample_system
from aegis.tui.themes import INK, aegis_colors
from aegis.tui.widgets import StatusBar


def _plain(s: str) -> str:
    """Strip Rich markup tags for content assertions."""
    return re.sub(r"\[[^\]]*\]", "", s)


def _colors():
    return aegis_colors(INK)


def test_format_system_percentages_rounded():
    out = format_system(SystemStats(cpu=23.4, ram=38.0, disk=71.6), _colors())
    assert _plain(out) == "CPU 23% · RAM 38% · DSK 72%"


def test_format_system_plain_when_below_threshold():
    colors = _colors()
    out = format_system(SystemStats(cpu=10.0, ram=20.0, disk=30.0), colors)
    # No amber markup when everything is comfortably below the high mark.
    assert colors.working not in out


def test_format_system_amber_when_high():
    colors = _colors()
    out = format_system(SystemStats(cpu=95.0, ram=20.0, disk=30.0), colors)
    # The hot metric is wrapped in the amber (working) colour; cool ones aren't.
    assert colors.working in out
    # Only CPU crossed the mark — RAM/DSK still read plainly.
    assert _plain(out) == "CPU 95% · RAM 20% · DSK 30%"


def test_format_system_threshold_is_inclusive_at_90():
    colors = _colors()
    assert colors.working in format_system(
        SystemStats(cpu=90.0, ram=0.0, disk=0.0), colors)


def test_sample_system_returns_sane_ranges(tmp_path):
    stats = sample_system(tmp_path)
    assert isinstance(stats, SystemStats)
    for v in (stats.cpu, stats.ram, stats.disk):
        assert isinstance(v, float)
        assert 0.0 <= v <= 100.0


def test_statusbar_set_system_shows_in_render_plain():
    bar = StatusBar("claude", "high", _colors())
    bar.set_system("CPU 23% · RAM 38% · DSK 71%")
    assert "CPU 23% · RAM 38% · DSK 71%" in bar.render_plain()


def test_statusbar_set_system_clears_when_empty():
    bar = StatusBar("claude", "high", _colors())
    bar.set_system("CPU 23% · RAM 38% · DSK 71%")
    bar.set_system("")
    assert "CPU" not in bar.render_plain()
