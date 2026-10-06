from datetime import datetime, timedelta, timezone

from aegis.themes import AegisColors
from aegis.tui.fit import plain_width, strip_markup
from aegis.usage.quota import (
    QuotaProvider, QuotaSnapshot, QuotaState, QuotaWindow, format_quota_bar,
)

COLORS = AegisColors(
    ready="green", working="yellow", error="red", accent="blue",
    muted="grey50", ok="green", err="red", user="blue", user_bg="black")

NOW = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)


def _provider(name="claude", label="cc", windows=(("session", "5h"),
                                                  ("weekly_all", "wk"))):
    return QuotaProvider(name=name, label=label, harness=f"{name}-harness",
                         bar_windows=windows, fetch=None, read_token=None)


def _state(session_pct=64.0, weekly_pct=7.0, severity="normal",
           failure="", age=0.0, resets_in=timedelta(hours=3, minutes=30)):
    snap = QuotaSnapshot(windows=(
        QuotaWindow("session", session_pct, severity, NOW + resets_in, True),
        QuotaWindow("weekly_all", weekly_pct, "normal",
                    NOW + timedelta(days=7), False),
    ), fetched_at=0.0)
    return QuotaState(snapshot=snap, age_s=age, failure=failure)


def _oc_provider():
    return _provider(name="opencode-go", label="oc",
                     windows=(("rolling", "5h"), ("weekly", "wk"),
                              ("monthly", "mo")))


def _oc_state(rolling=0.0, weekly=0.0, monthly=14.0, failure=""):
    snap = QuotaSnapshot(windows=(
        QuotaWindow("rolling", rolling, "normal", NOW + timedelta(hours=5),
                    True),
        QuotaWindow("weekly", weekly, "normal", NOW + timedelta(days=4), True),
        QuotaWindow("monthly", monthly, "normal", NOW + timedelta(days=13),
                    True),
    ), fetched_at=0.0)
    return QuotaState(snapshot=snap, failure=failure)


def _bar(*readings, now=NOW):
    return format_quota_bar(list(readings), COLORS, now=now)


def _plain(tiers):
    return [strip_markup(t) for t in tiers]


# --- one provider: the contract the bar has always had -----------------------

def test_calm_shows_both_windows_without_a_countdown():
    assert _plain(_bar((_provider(), _state())))[0] == "⧗ 5h 64% · wk 7%"


def test_a_lone_provider_is_not_labelled():
    assert "cc" not in _plain(_bar((_provider(), _state())))[0]


def test_warning_grows_a_countdown_on_that_window_only():
    tiers = _bar((_provider(), _state(session_pct=87.0, severity="warning")))
    assert _plain(tiers)[0] == "⧗ 5h 87% ⟶3h30m · wk 7%"


def test_warning_uses_the_working_colour():
    tiers = _bar((_provider(), _state(session_pct=87.0, severity="warning")))
    assert "[yellow]" in tiers[0]


def test_critical_uses_the_error_colour():
    tiers = _bar((_provider(), _state(session_pct=97.0, severity="critical")))
    assert "[red]" in tiers[0]


def test_middle_tier_is_narrower_and_drops_the_window_names():
    tiers = _bar((_provider(), _state()))
    assert plain_width(tiers[1]) < plain_width(tiers[0])
    assert _plain(tiers)[1] == "⧗ 64/7%"


def test_stale_keeps_the_numbers_and_shows_their_age():
    tiers = _bar((_provider(), _state(failure="unreachable", age=125.0)))
    assert _plain(tiers)[0] == "⧗ 5h 64% · wk 7% (2m old)"
    assert "[grey50]" in tiers[0]


def test_unauthorized_says_auth_expired():
    state = QuotaState(snapshot=None, failure="unauthorized")
    assert _plain(_bar((_provider(), state)))[0] == "⧗ quota — auth expired"


def test_unreachable_says_unreachable():
    state = QuotaState(snapshot=None, failure="unreachable")
    assert _plain(_bar((_provider(), state)))[0] == "⧗ quota — unreachable"


def test_rate_limited_says_so():
    state = QuotaState(snapshot=None, failure="rate_limited")
    assert _plain(_bar((_provider(), state)))[0] == "⧗ quota — rate limited"


def test_nothing_known_yet_renders_nothing():
    assert _bar((_provider(), QuotaState())) == ()


def test_missing_reset_timestamp_omits_the_countdown():
    snap = QuotaSnapshot(windows=(
        QuotaWindow("session", 91.0, "warning", None, True),), fetched_at=0.0)
    tiers = _bar((_provider(), QuotaState(snapshot=snap)))
    assert _plain(tiers)[0] == "⧗ 5h 91%"


# --- absent credentials are not a failure ------------------------------------

def test_a_provider_without_credentials_is_omitted_entirely():
    state = QuotaState(snapshot=None, failure="no_credentials")
    assert _bar((_provider(), state)) == ()


def test_an_uncredentialed_provider_does_not_crowd_out_a_credentialed_one():
    absent = QuotaState(snapshot=None, failure="no_credentials")
    tiers = _bar((_provider(), absent), (_oc_provider(), _oc_state()))
    # Only opencode survives, so it is the lone provider and goes unlabelled.
    assert _plain(tiers)[0] == "⧗ 5h 0% · wk 0% · mo 14%"


# --- two providers ------------------------------------------------------------

def test_two_providers_are_labelled_and_separated():
    tiers = _bar((_provider(), _state()), (_oc_provider(), _oc_state()))
    assert _plain(tiers)[0] == (
        "⧗ cc 5h 64% · wk 7% │ oc 5h 0% · wk 0% · mo 14%")


def test_each_provider_renders_its_own_declared_windows():
    tiers = _bar((_provider(), _state()), (_oc_provider(), _oc_state()))
    assert _plain(tiers)[0].count("·") == 3   # one within cc, two within oc


def test_middle_tier_keeps_the_labels():
    tiers = _bar((_provider(), _state()), (_oc_provider(), _oc_state()))
    assert _plain(tiers)[1] == "⧗ cc 64/7% │ oc 0/0/14%"


def test_narrowest_tier_is_one_worst_window_per_provider():
    tiers = _bar((_provider(), _state()), (_oc_provider(), _oc_state()))
    assert _plain(tiers)[2] == "⧗ cc64 oc14"


def test_narrowest_tier_picks_the_window_closest_to_exhaustion():
    tiers = _bar((_provider(), _state(session_pct=12.0, weekly_pct=88.0)),
                 (_oc_provider(), _oc_state(rolling=3.0, monthly=1.0)))
    assert _plain(tiers)[2] == "⧗ cc88 oc3"


def test_narrowest_tier_is_the_narrowest():
    tiers = _bar((_provider(), _state()), (_oc_provider(), _oc_state()))
    assert plain_width(tiers[2]) < plain_width(tiers[1]) < plain_width(tiers[0])


def test_a_lone_provider_still_gets_a_worst_window_tier():
    assert _plain(_bar((_provider(), _state())))[2] == "⧗ 64%"


def test_one_provider_failing_does_not_hide_the_other():
    dead = QuotaState(snapshot=None, failure="unauthorized")
    tiers = _bar((_provider(), dead), (_oc_provider(), _oc_state()))
    assert _plain(tiers)[0] == "⧗ cc auth expired │ oc 5h 0% · wk 0% · mo 14%"


def test_severity_colour_survives_into_the_narrowest_tier():
    tiers = _bar((_provider(), _state(session_pct=97.0, severity="critical")),
                 (_oc_provider(), _oc_state()))
    assert "[red]" in tiers[2]


def test_everything_absent_renders_nothing():
    absent = QuotaState(snapshot=None, failure="no_credentials")
    assert _bar((_provider(), absent), (_oc_provider(), absent)) == ()
