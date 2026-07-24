from datetime import datetime, timedelta, timezone

import pytest

from aegis.usage.quota import (
    QuotaSnapshot, QuotaState, QuotaWindow, quota_lines,
)

NOW = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)


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


@pytest.mark.asyncio
async def test_command_registers_and_returns_lines(monkeypatch):
    from aegis.commands import CommandContext, dispatch
    from aegis.commands.builtins import usage as usage_cmd

    class FakeService:
        refreshed = False

        async def refresh(self, **kw):
            self.refreshed = True

        def current(self):
            return _state()

    fake = FakeService()
    monkeypatch.setattr(usage_cmd, "_quota_service", lambda ctx: fake)
    result = await dispatch("/usage quota", CommandContext(None, "h"))
    assert result.ok
    assert fake.refreshed is True
    assert "session" in result.body
    assert result.title == "usage · quota · 5h 64%"
