from datetime import datetime, timedelta, timezone

from aegis.themes import AegisColors
from aegis.tui.fit import plain_width, strip_markup
from aegis.usage.quota import (
    QuotaSnapshot, QuotaState, QuotaWindow, format_quota_tiers,
)

COLORS = AegisColors(
    ready="green", working="yellow", error="red", accent="blue",
    muted="grey50", ok="green", err="red", user="blue", user_bg="black")

NOW = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)


def _state(session_pct=64.0, weekly_pct=7.0, severity="normal",
           failure="", age=0.0, resets_in=timedelta(hours=3, minutes=30)):
    snap = QuotaSnapshot(windows=(
        QuotaWindow("session", session_pct, severity, NOW + resets_in, True),
        QuotaWindow("weekly_all", weekly_pct, "normal",
                    NOW + timedelta(days=7), False),
    ), fetched_at=0.0)
    return QuotaState(snapshot=snap, age_s=age, failure=failure)


def _plain(tiers):
    return [strip_markup(t) for t in tiers]


def test_calm_shows_both_windows_without_a_countdown():
    tiers = format_quota_tiers(_state(), COLORS, now=NOW)
    assert _plain(tiers)[0] == "⧗ 5h 64% · wk 7%"


def test_warning_grows_a_countdown_on_that_window_only():
    tiers = format_quota_tiers(
        _state(session_pct=87.0, severity="warning"), COLORS, now=NOW)
    assert _plain(tiers)[0] == "⧗ 5h 87% ⟶3h30m · wk 7%"


def test_warning_uses_the_working_colour():
    tiers = format_quota_tiers(
        _state(session_pct=87.0, severity="warning"), COLORS, now=NOW)
    assert "[yellow]" in tiers[0]


def test_critical_uses_the_error_colour():
    tiers = format_quota_tiers(
        _state(session_pct=97.0, severity="critical"), COLORS, now=NOW)
    assert "[red]" in tiers[0]


def test_short_tier_is_narrower():
    tiers = format_quota_tiers(_state(), COLORS, now=NOW)
    assert plain_width(tiers[1]) < plain_width(tiers[0])
    assert _plain(tiers)[1] == "⧗ 64/7%"


def test_stale_keeps_the_numbers_and_shows_their_age():
    tiers = format_quota_tiers(
        _state(failure="unreachable", age=125.0), COLORS, now=NOW)
    assert _plain(tiers)[0] == "⧗ 5h 64% · wk 7% (2m old)"
    assert "[grey50]" in tiers[0]


def test_no_credentials_says_so():
    state = QuotaState(snapshot=None, failure="no_credentials")
    assert _plain(format_quota_tiers(state, COLORS, now=NOW))[0] == (
        "⧗ quota — no credentials")


def test_unauthorized_says_auth_expired():
    state = QuotaState(snapshot=None, failure="unauthorized")
    assert _plain(format_quota_tiers(state, COLORS, now=NOW))[0] == (
        "⧗ quota — auth expired")


def test_unreachable_says_unreachable():
    state = QuotaState(snapshot=None, failure="unreachable")
    assert _plain(format_quota_tiers(state, COLORS, now=NOW))[0] == (
        "⧗ quota — unreachable")


def test_nothing_known_yet_renders_nothing():
    assert format_quota_tiers(QuotaState(), COLORS, now=NOW) == ()


def test_missing_reset_timestamp_omits_the_countdown():
    snap = QuotaSnapshot(windows=(
        QuotaWindow("session", 91.0, "warning", None, True),), fetched_at=0.0)
    tiers = format_quota_tiers(QuotaState(snapshot=snap), COLORS, now=NOW)
    assert _plain(tiers)[0] == "⧗ 5h 91%"


def test_rate_limited_says_so():
    state = QuotaState(snapshot=None, failure="rate_limited")
    assert _plain(format_quota_tiers(state, COLORS, now=NOW))[0] == (
        "⧗ quota — rate limited")
