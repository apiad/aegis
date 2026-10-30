"""Pace: colouring a quota window by where its spend lands at reset.

The projection keeps what is spent and runs the time left at a rate between the
window's own and what finished windows usually spend. Level asks how much is gone. Pace asks whether what is left survives the
period. Critical is level; warning is pace, over 100% at reset with half the
quota spent; under pace is normal below critical. A window whose span or reset
time we cannot read falls all the way back to level.
"""

from datetime import datetime, timedelta, timezone

import pytest

from aegis.quota.core import (
    PACE_PRIOR_WEIGHT,
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


def _pace(percent, remaining, span=H5, usual=100.0, **kw):
    return window_pace(_w(percent, remaining, **kw), span, now=NOW, usual=usual)


def _verdict(percent, remaining, span=H5, **kw):
    return pace_severity(_w(percent, remaining, **kw), span, now=NOW)


# --- the projection itself ----------------------------------------------------


def test_a_new_window_reads_what_windows_usually_spend():
    assert _pace(0.0, timedelta(hours=5)) == pytest.approx(100.0)
    assert _pace(0.0, timedelta(hours=5), usual=24.0) == pytest.approx(24.0)


def test_spending_at_the_usual_rate_stays_on_the_usual_landing():
    for elapsed in (0.01, 0.15, 0.5, 0.9):
        left = timedelta(seconds=H5 * (1 - elapsed))
        assert _pace(24.0 * elapsed, left, usual=24.0) == pytest.approx(24.0)


def test_the_rate_averages_the_window_and_its_history():
    # 3h of a 5h window gone at 30% is a rate of 50 against a usual 100.
    weight = 0.6 / (PACE_PRIOR_WEIGHT + 0.6)
    rate = weight * 50.0 + (1 - weight) * 100.0
    assert _pace(30.0, timedelta(hours=2)) == pytest.approx(30.0 + 0.4 * rate)


def test_a_thin_slice_does_not_spike():
    # Six minutes into a 5h window, 5% spent is a raw rate of 250%.
    assert _pace(5.0, timedelta(hours=4, minutes=54)) == pytest.approx(108.65, abs=0.01)


def test_the_answer_does_not_step_when_a_slice_grows():
    # 38% with 15% of the week gone: 253% raw. A minute moves it by little.
    left = timedelta(days=7 * 0.85)
    got = _pace(38.0, left, span=WK)
    assert got == pytest.approx(153.1, abs=0.1)
    assert abs(_pace(38.0, left - timedelta(minutes=1), span=WK) - got) < 0.1


def test_the_largest_projection_stays_three_digits():
    # The worst case is a full window spent in its first instant, with a usual
    # at the 100% that history is capped to.
    assert _pace(100.0, timedelta(hours=5)) == pytest.approx(400.0)


def test_silence_lets_the_projection_fall():
    early = _pace(0.0, timedelta(hours=4))
    late = _pace(0.0, timedelta(hours=1))
    assert 0.0 < late < early < 100.0


def test_a_finished_window_projects_to_what_it_spent():
    # resets_at in the past: the endpoint has not caught up, or the clock is
    # ahead. Elapsed is the whole span, so pace agrees with level.
    assert _pace(64.0, timedelta(minutes=-3)) == pytest.approx(64.0)


# --- the three bands ----------------------------------------------------------


def test_comfortable_pace_is_normal():
    assert _verdict(30.0, timedelta(hours=2)) == "normal"


def test_under_pace_is_normal_however_much_is_gone():
    # 73% of the week, 86% of it gone, landing at 86%. The period absorbs it; nobody needs to slow down.
    w = _w(73.0, timedelta(days=1), severity="normal", kind="weekly_all")
    assert window_pace(w, WK, now=NOW) == pytest.approx(85.95, abs=0.01)
    assert pace_severity(w, WK, now=NOW) == "normal"


def test_a_level_warning_under_pace_is_dropped():
    # 82% gone with 18 hours of the week left lands at 92%. The level verdict
    # (ours at 80%, or a vendor's) says warning; pace overrules it.
    w = _w(82.0, timedelta(hours=18), severity="warning", kind="weekly_all")
    assert window_pace(w, WK, now=NOW) < 100.0
    assert pace_severity(w, WK, now=NOW) == "normal"


def test_landing_exactly_on_the_limit_is_normal():
    # Over pace is strict: spending exactly the quota by reset is the plan.
    assert _pace(50.0, timedelta(hours=2, minutes=30)) == pytest.approx(100.0)
    assert _verdict(50.0, timedelta(hours=2, minutes=30)) == "normal"


def test_over_pace_with_half_spent_is_a_warning():
    assert _verdict(50.1, timedelta(hours=2, minutes=30)) == "warning"


def test_over_pace_under_half_spent_is_not_yet_a_warning():
    # 45% with 3.5h of a 5h window left projects to 128%, but half the quota is
    # still there to slow down with.
    assert _pace(45.0, timedelta(hours=3, minutes=30)) == pytest.approx(128.1, abs=0.1)
    assert _verdict(45.0, timedelta(hours=3, minutes=30)) == "normal"


def test_ninety_percent_spent_is_critical_on_any_pace():
    assert _verdict(90.0, timedelta(minutes=5)) == "critical"
    assert _verdict(89.9, timedelta(minutes=5)) == "normal"


def test_far_over_pace_below_ninety_is_only_a_warning():
    assert _verdict(85.0, timedelta(hours=3)) == "warning"


# --- level and pace compose ---------------------------------------------------


def test_level_escalates_a_pace_that_says_fine():
    # 97% of the week gone with five minutes left projects to 97%, which is
    # under pace. You have 3% to spend: level keeps it critical.
    w = _w(97.0, timedelta(minutes=5), severity="critical", kind="weekly_all")
    assert window_pace(w, WK, now=NOW) == pytest.approx(97.05, abs=0.01)
    assert pace_severity(w, WK, now=NOW) == "critical"


def test_a_vendor_alarm_survives_a_calm_projection():
    # The API said critical at 60%. We never paint green over that.
    assert _verdict(60.0, timedelta(minutes=30), severity="critical") == "critical"


def test_pace_escalates_a_level_that_says_fine():
    assert _verdict(60.0, timedelta(hours=3), severity="normal") == "warning"


# --- history moves the verdict, and the clock moves it without a reading ------


def test_a_window_that_usually_spends_little_warns_later():
    # 60% at half time: a usual of 100 lands at 115, a usual of 24 at 96.
    w = _w(60.0, timedelta(hours=2, minutes=30))
    assert pace_severity(w, H5, now=NOW) == "warning"
    assert pace_severity(w, H5, now=NOW, usual=24.0) == "normal"


def test_a_first_day_burst_reads_below_its_raw_rate():
    # 18% of the week in its first 20 hours is a raw rate of 151%.
    got = _pace(18.0, timedelta(days=6, hours=4), span=WK, kind="weekly_all")
    assert got == pytest.approx(114.8, abs=0.1)


def test_the_same_burst_relaxes_as_the_week_measures_it():
    got = _pace(18.0, timedelta(days=5, hours=12), span=WK, kind="weekly_all")
    assert got == pytest.approx(92.8, abs=0.1)


def test_an_idle_window_relaxes_without_a_new_reading():
    # One frozen 60% reading, two render times. This is why the projection
    # is computed at render and not at parse.
    w = _w(60.0, timedelta(hours=3))
    assert pace_severity(w, H5, now=NOW) == "warning"
    assert pace_severity(w, H5, now=NOW + timedelta(minutes=90)) == "normal"


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
    # Clamping elapsed to zero would invent a projection from bad input.
    assert _pace(10.0, timedelta(hours=9)) is None


def test_small_clock_skew_does_not_kill_the_projection():
    # A clock a few seconds behind makes a fresh window read as longer than
    # its span. That is skew, not a wrong span, and pace survives it.
    assert _pace(10.0, timedelta(hours=5, seconds=30)) == pytest.approx(130.0)


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
