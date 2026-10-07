"""Pace: colouring a quota window by where its spend lands at reset.

Level asks how much is gone. Pace asks whether what is left survives the
period. The two verdicts compose with ``worse``, so level can escalate pace
but never relax it, and a window whose span or reset time we cannot read
falls all the way back to level.
"""

from datetime import datetime, timedelta, timezone

import pytest

from aegis.quota.core import (
    PACE_FLOOR,
    QuotaWindow,
    pace_severity,
    window_pace,
)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
H5 = 5 * 3600
WK = 7 * 86400


def _w(percent, remaining, *, severity="normal", kind="session"):
    """A window with ``remaining`` left on its clock; None means no reset time."""
    resets = None if remaining is None else NOW + remaining
    return QuotaWindow(kind, percent, severity, resets, True)


def _pace(percent, remaining, span=H5, **kw):
    return window_pace(_w(percent, remaining, **kw), span, now=NOW)


def _verdict(percent, remaining, span=H5, **kw):
    return pace_severity(_w(percent, remaining, **kw), span, now=NOW)


# --- the projection itself ----------------------------------------------------


def test_half_the_window_doubles_the_spend():
    # 30% gone with 3h of a 5h window elapsed lands at 50%.
    assert _pace(30.0, timedelta(hours=2)) == pytest.approx(50.0)


def test_a_burst_projects_past_the_limit():
    assert _pace(45.0, timedelta(hours=3, minutes=30)) == pytest.approx(150.0)


def test_nothing_spent_projects_to_nothing():
    assert _pace(0.0, timedelta(hours=3)) == 0.0


def test_the_floor_caps_what_a_thin_slice_can_claim():
    # Six minutes into a 5h window is 2% elapsed; without the floor 5% spent
    # would project to 250%. The floor reads it as 45 minutes' worth instead.
    assert _pace(5.0, timedelta(hours=4, minutes=54)) == pytest.approx(5.0 / PACE_FLOOR)


def test_the_floor_clamps_and_does_not_mute():
    # Same six minutes, a fifth of the window gone: still red, and should be.
    assert _pace(20.0, timedelta(hours=4, minutes=54)) == pytest.approx(
        133.33, abs=0.01
    )


def test_the_floor_bounds_the_largest_projection():
    # The worst case is a full window spent in its first instant. The floor
    # caps that at 667%, which is what keeps the printed tail three digits.
    assert _pace(100.0, timedelta(hours=5)) == pytest.approx(100.0 / PACE_FLOOR)
    assert 100.0 / PACE_FLOOR < 1000


def test_a_finished_window_projects_to_what_it_spent():
    # resets_at in the past: the endpoint has not caught up, or the clock is
    # ahead. Elapsed is the whole span, so pace agrees with level.
    assert _pace(64.0, timedelta(minutes=-3)) == pytest.approx(64.0)


# --- the three bands ----------------------------------------------------------


def test_comfortable_pace_is_normal():
    assert _verdict(30.0, timedelta(hours=2)) == "normal"


def test_exactly_twenty_percent_of_headroom_is_a_warning():
    # e = 0.5, so 40% spent projects to exactly 80%. "More than 20% left" is
    # strict, so the boundary belongs to warning.
    assert _pace(40.0, timedelta(hours=2, minutes=30)) == pytest.approx(80.0)
    assert _verdict(40.0, timedelta(hours=2, minutes=30)) == "warning"


def test_a_shade_under_the_boundary_is_still_normal():
    assert _verdict(39.9, timedelta(hours=2, minutes=30)) == "normal"


def test_landing_exactly_on_the_limit_is_a_warning():
    assert _pace(50.0, timedelta(hours=2, minutes=30)) == pytest.approx(100.0)
    assert _verdict(50.0, timedelta(hours=2, minutes=30)) == "warning"


def test_running_out_before_the_period_does_is_critical():
    assert _verdict(50.1, timedelta(hours=2, minutes=30)) == "critical"


# --- level and pace compose ---------------------------------------------------


def test_level_escalates_a_pace_that_says_fine():
    # 97% of the week gone with five minutes left projects to 97%, which is
    # only a warning. You have 3% to spend: level keeps it critical.
    w = _w(97.0, timedelta(minutes=5), severity="critical", kind="weekly_all")
    assert window_pace(w, WK, now=NOW) == pytest.approx(97.05, abs=0.01)
    assert pace_severity(w, WK, now=NOW) == "critical"


def test_a_vendor_alarm_survives_a_calm_projection():
    # The API said critical at 60%. We never paint green over that.
    assert _verdict(60.0, timedelta(minutes=30), severity="critical") == "critical"


def test_pace_escalates_a_level_that_says_fine():
    assert (
        _verdict(45.0, timedelta(hours=3, minutes=30), severity="normal") == "critical"
    )


# --- the weekly window reads its own first day harshly, then relaxes ---------


def test_a_first_day_burst_reads_as_the_floor_allows():
    # 18% of the week in its first 20 hours: the floor makes this 120%.
    got = _pace(18.0, timedelta(days=6, hours=4), span=WK, kind="weekly_all")
    assert got == pytest.approx(120.0)


def test_the_same_burst_relaxes_once_a_full_day_has_measured_it():
    got = _pace(18.0, timedelta(days=5, hours=12), span=WK, kind="weekly_all")
    assert got == pytest.approx(84.0)


def test_an_idle_window_relaxes_without_a_new_reading():
    # One frozen 45% reading, three render times. This is why the projection
    # is computed at render and not at parse.
    w = _w(45.0, timedelta(hours=3, minutes=30))
    at = {
        "1h30m": NOW,
        "2h30m": NOW + timedelta(hours=1),
        "3h00m": NOW + timedelta(hours=1, minutes=30),
    }
    assert pace_severity(w, H5, now=at["1h30m"]) == "critical"
    assert pace_severity(w, H5, now=at["2h30m"]) == "warning"
    assert pace_severity(w, H5, now=at["3h00m"]) == "normal"


# --- everything we refuse to guess about -------------------------------------


def test_no_reset_time_means_no_projection():
    assert _pace(64.0, None) is None
    assert _verdict(64.0, None, severity="warning") == "warning"


def test_an_unknown_window_kind_has_no_span_and_no_projection():
    assert window_pace(_w(64.0, timedelta(hours=1)), None, now=NOW) is None
    assert pace_severity(_w(64.0, timedelta(hours=1)), None, now=NOW) == "normal"


def test_a_zero_span_is_not_divided_by():
    assert window_pace(_w(64.0, timedelta(hours=1)), 0, now=NOW) is None


def test_a_reset_further_out_than_the_span_is_a_wrong_span():
    # Either the clock is badly off or our span for this kind is wrong.
    # Clamping elapsed to zero would invent a 6.7x projection from bad input.
    assert _pace(10.0, timedelta(hours=9)) is None


def test_small_clock_skew_does_not_kill_the_projection():
    # A clock a few seconds behind makes a fresh window read as longer than
    # its span. That is skew, not a wrong span, and pace survives it.
    assert _pace(10.0, timedelta(hours=5, seconds=30)) == pytest.approx(
        10.0 / PACE_FLOOR
    )


def test_a_brand_new_window_uses_the_floor():
    assert _pace(1.0, timedelta(hours=5)) == pytest.approx(1.0 / PACE_FLOOR)


# --- the spans the providers declare -----------------------------------------


def test_claude_declares_its_window_spans():
    from aegis.quota.claude import PROVIDER

    assert PROVIDER.window_spans == {
        "session": H5,
        "weekly_all": WK,
        "weekly_opus": WK,
        "weekly_scoped": WK,
    }


def test_opencode_declares_its_window_spans():
    from aegis.quota.opencode import PROVIDER

    assert PROVIDER.window_spans == {
        "rolling": H5,
        "weekly": WK,
        "monthly": 30 * 86400,
    }


def test_a_provider_that_declares_no_spans_keeps_level_only():
    from aegis.quota.core import QuotaProvider

    p = QuotaProvider(
        name="x",
        label="x",
        harness="x",
        bar_windows=(("session", "5h"),),
        fetch=None,
        read_token=None,
    )
    assert p.window_spans == {}
    assert (
        window_pace(
            _w(90.0, timedelta(hours=4)), p.window_spans.get("session"), now=NOW
        )
        is None
    )
