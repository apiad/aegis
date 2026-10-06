"""System-stats status-bar segment: sampling + formatting + StatusBar wiring.

Also the three static rows the sidebar's SYSTEM section carries beside the
meters — clock/locale, working directory, running build.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aegis.tui.sysmeter import (
    SystemStats, current_locale, format_build, format_clock, format_cwd,
    format_system, sample_system,
)
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


# -- the sidebar's static SYSTEM rows -----------------------------------


def _at(hour: int = 11, minute: int = 3) -> datetime:
    """A fixed instant in a fixed zone — a clock formatter tested against
    `now()` asserts nothing, and one tested against a naive datetime never
    exercises the zone."""
    return datetime(2026, 8, 11, hour, minute,
                    tzinfo=timezone(timedelta(hours=-4), "CDT"))


def test_format_clock_leads_with_date_time_zone_and_locale():
    tiers = format_clock(_at(), "en_US.UTF-8", _colors())
    assert _plain(tiers[0]) == "2026-08-11 11:03 CDT · en_US.UTF-8"


def test_format_clock_narrows_to_the_time():
    assert _plain(format_clock(_at(), "en_US.UTF-8", _colors())[-1]) == "11:03"


def test_format_clock_without_a_locale_drops_the_separator():
    tiers = format_clock(_at(), "", _colors())
    assert _plain(tiers[0]) == "2026-08-11 11:03 CDT"


def test_format_clock_on_a_naive_datetime_omits_the_zone():
    tiers = format_clock(datetime(2026, 8, 11, 11, 3), "", _colors())
    assert _plain(tiers[0]) == "2026-08-11 11:03"


def test_format_cwd_collapses_home():
    tiers = format_cwd(Path.home() / "Workspace/repos/aegis", _colors())
    assert _plain(tiers[0]) == "CWD ~/Workspace/repos/aegis"


def test_format_cwd_narrows_from_the_head():
    tiers = format_cwd(Path.home() / "Workspace/repos/aegis", _colors())
    assert _plain(tiers[1]) == "CWD …/repos/aegis"
    assert _plain(tiers[-1]) == "CWD aegis"


def test_format_cwd_outside_home_stays_absolute():
    assert _plain(format_cwd(Path("/srv/app"), _colors())[0]) == "CWD /srv/app"


def test_format_cwd_at_home_is_a_bare_tilde():
    assert _plain(format_cwd(Path.home(), _colors())[0]) == "CWD ~"


def test_format_build_reports_the_running_version():
    from aegis.version import BUILD
    tiers = format_build(_colors())
    assert _plain(tiers[0]) == f"aegis {BUILD}"
    assert _plain(tiers[-1]) == BUILD.split("+")[0]


def test_current_locale_falls_back_to_the_environment(monkeypatch):
    monkeypatch.setattr("locale.getlocale", lambda *a: (None, None))
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.setenv("LANG", "es_ES.UTF-8")
    assert current_locale() == "es_ES.UTF-8"


def test_current_locale_joins_language_and_encoding(monkeypatch):
    monkeypatch.setattr("locale.getlocale", lambda *a: ("en_US", "UTF-8"))
    assert current_locale() == "en_US.UTF-8"
