from datetime import timedelta

import pytest

from aegis.budget.windows import parse_window


def test_parse_minutes():
    assert parse_window("30m") == timedelta(minutes=30)


def test_parse_hours():
    assert parse_window("1h") == timedelta(hours=1)
    assert parse_window("24h") == timedelta(hours=24)


def test_parse_days():
    assert parse_window("7d") == timedelta(days=7)


def test_parse_weeks():
    assert parse_window("1w") == timedelta(weeks=1)


def test_parse_rejects_unknown_suffix():
    with pytest.raises(ValueError, match="unknown window suffix"):
        parse_window("5y")


def test_parse_rejects_zero():
    with pytest.raises(ValueError, match="must be positive"):
        parse_window("0h")


def test_parse_rejects_negative():
    with pytest.raises(ValueError, match="must be positive"):
        parse_window("-1h")


def test_parse_rejects_no_suffix():
    with pytest.raises(ValueError, match="must end with"):
        parse_window("60")


def test_parse_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        parse_window("")
