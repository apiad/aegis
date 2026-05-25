"""Tests for lifecycle exhaustion predicate."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aegis.scheduler.lifecycle import is_exhausted


def test_lifecycle_forever() -> None:
    assert not is_exhausted(
        "forever", fire_count=1000,
        now=datetime.now(timezone.utc))


def test_lifecycle_once() -> None:
    now = datetime.now(timezone.utc)
    assert not is_exhausted("once", fire_count=0, now=now)
    assert is_exhausted("once", fire_count=1, now=now)


def test_lifecycle_fires_n() -> None:
    now = datetime.now(timezone.utc)
    assert not is_exhausted({"fires": 3}, fire_count=2, now=now)
    assert is_exhausted({"fires": 3}, fire_count=3, now=now)


def test_lifecycle_until() -> None:
    until = "2026-05-25T12:00:00+00:00"
    before = datetime(2026, 5, 25, 11, 0, tzinfo=timezone.utc)
    after = datetime(2026, 5, 25, 13, 0, tzinfo=timezone.utc)
    assert not is_exhausted({"until": until}, fire_count=0, now=before)
    assert is_exhausted({"until": until}, fire_count=0, now=after)


def test_lifecycle_invalid_raises() -> None:
    with pytest.raises(ValueError, match="invalid lifecycle"):
        is_exhausted("nonsense", fire_count=0,
                     now=datetime.now(timezone.utc))
