from datetime import datetime, timedelta, timezone

import pytest

from aegis.usage.quota import (
    QuotaProvider, QuotaSnapshot, QuotaState, QuotaWindow, quota_lines,
    quota_report,
)

NOW = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)


def _provider(name="claude", label="cc", windows=(("session", "5h"),
                                                  ("weekly_all", "wk"))):
    return QuotaProvider(name=name, label=label, harness=f"{name}-harness",
                         bar_windows=windows, fetch=None, read_token=None)


def _state():
    snap = QuotaSnapshot(windows=(
        QuotaWindow("session", 64.0, "normal",
                    NOW + timedelta(hours=3, minutes=30), True),
        QuotaWindow("weekly_all", 7.0, "normal",
                    NOW + timedelta(days=7), False),
        QuotaWindow("weekly_opus", 88.0, "warning",
                    NOW + timedelta(days=7), False),
    ), fetched_at=0.0)
    return QuotaState(snapshot=snap, age_s=12.0)


def _oc_provider():
    return _provider(name="opencode-go", label="oc",
                     windows=(("rolling", "5h"), ("weekly", "wk"),
                              ("monthly", "mo")))


def _oc_state():
    snap = QuotaSnapshot(windows=(
        QuotaWindow("rolling", 0.0, "normal", NOW + timedelta(hours=5), True),
        QuotaWindow("monthly", 14.0, "normal", NOW + timedelta(days=13), True),
    ), fetched_at=0.0)
    return QuotaState(snapshot=snap, age_s=3.0)


# --- one provider's breakdown -------------------------------------------------

def test_lines_cover_every_window_not_just_the_bar_pair():
    text = "\n".join(quota_lines(_state(), now=NOW))
    assert "session" in text
    assert "weekly_all" in text
    assert "weekly_opus" in text


def test_lines_carry_percent_severity_and_countdown():
    text = "\n".join(quota_lines(_state(), now=NOW))
    assert "64%" in text
    assert "warning" in text
    assert "3h30m" in text


def test_lines_report_the_age_of_the_reading():
    assert any("12s" in ln for ln in quota_lines(_state(), now=NOW))


def test_failure_state_explains_itself():
    state = QuotaState(snapshot=None, failure="no_credentials")
    text = "\n".join(quota_lines(state, now=NOW))
    assert "no credentials" in text


# --- the composed report ------------------------------------------------------

def test_a_lone_provider_reports_without_a_heading():
    lines = quota_report([(_provider(), _state())], now=NOW)
    assert "claude" not in "\n".join(lines)
    assert "session" in "\n".join(lines)


def test_two_providers_are_each_headed_by_name():
    text = "\n".join(quota_report(
        [(_provider(), _state()), (_oc_provider(), _oc_state())], now=NOW))
    assert "claude" in text
    assert "opencode-go" in text
    assert "session" in text
    assert "monthly" in text


def test_an_uncredentialed_provider_is_left_out():
    absent = QuotaState(snapshot=None, failure="no_credentials")
    text = "\n".join(quota_report(
        [(_provider(), absent), (_oc_provider(), _oc_state())], now=NOW))
    assert "claude" not in text
    assert "monthly" in text


def test_a_failing_provider_is_reported_not_hidden():
    dead = QuotaState(snapshot=None, failure="unauthorized")
    text = "\n".join(quota_report(
        [(_provider(), dead), (_oc_provider(), _oc_state())], now=NOW))
    assert "auth expired" in text


def test_no_providers_at_all_says_so():
    absent = QuotaState(snapshot=None, failure="no_credentials")
    text = "\n".join(quota_report(
        [(_provider(), absent), (_oc_provider(), absent)], now=NOW))
    assert "no credentials" in text


# --- the command --------------------------------------------------------------

class FakeService:
    def __init__(self, state):
        self._state = state
        self.refreshed = False

    async def refresh(self, **kw):
        self.refreshed = True

    def current(self):
        return self._state


@pytest.mark.asyncio
async def test_command_reports_every_provider(monkeypatch):
    from aegis.commands import CommandContext, dispatch
    from aegis.commands.builtins import usage as usage_cmd

    services = {"claude": FakeService(_state()),
                "opencode-go": FakeService(_oc_state())}
    monkeypatch.setattr(usage_cmd, "_quota_services", lambda ctx: services)
    monkeypatch.setattr(
        usage_cmd, "PROVIDERS", (_provider(), _oc_provider()))

    result = await dispatch("/usage quota", CommandContext(None, "h"))
    assert result.ok
    assert all(s.refreshed for s in services.values())
    assert "session" in result.body
    assert "monthly" in result.body
    assert result.title == "usage · quota · cc 88% · oc 14%"


@pytest.mark.asyncio
async def test_command_title_drops_the_label_for_a_lone_provider(monkeypatch):
    from aegis.commands import CommandContext, dispatch
    from aegis.commands.builtins import usage as usage_cmd

    services = {"claude": FakeService(_state())}
    monkeypatch.setattr(usage_cmd, "_quota_services", lambda ctx: services)
    monkeypatch.setattr(usage_cmd, "PROVIDERS", (_provider(),))

    result = await dispatch("/usage quota", CommandContext(None, "h"))
    assert result.title == "usage · quota · 88%"
