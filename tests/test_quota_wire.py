"""Quota on the wire: what the `quota` channel and `quota.read` carry, and
when the channel publishes."""

import asyncio
import dataclasses
import threading
from datetime import datetime, timezone


from aegis.quota import Quota, cache_dir
from aegis.quota.core import QuotaError, QuotaProvider, QuotaSnapshot, QuotaWindow

from .conftest import until

WALL = 1_800_000_000.0
H5, WK = 5 * 3600, 7 * 86400


class Clocks:
    """A monotonic clock and a wall clock that advance together."""

    def __init__(self):
        self.mono, self.wall = 1000.0, WALL

    def advance(self, dt):
        self.mono += dt
        self.wall += dt


def _at(clocks, seconds_from_now):
    return datetime.fromtimestamp(clocks.wall + seconds_from_now, timezone.utc)


def provider(clocks, results, calls, *, token="tok"):
    """A Claude-shaped provider. Each entry of `results` is an exception to
    raise, or (percent of the 5-hour window, seconds until it resets)."""

    def fetch(tok, **kw):
        calls.append(tok)
        item = results.pop(0)
        if isinstance(item, Exception):
            raise item
        pct, remaining = item
        return QuotaSnapshot(
            windows=(
                QuotaWindow("session", pct, "normal", _at(clocks, remaining), True),
                QuotaWindow("weekly_all", 47.0, "normal", _at(clocks, 2 * 86400), True),
            ),
            fetched_at=clocks.mono,
        )

    return QuotaProvider(
        name="claude",
        label="Claude",
        harness="claude-code",
        bar_windows=(("session", "5 hours"), ("weekly_all", "week")),
        fetch=fetch,
        read_token=token if callable(token) else (lambda path=None: token),
        window_spans={"session": H5, "weekly_all": WK},
        poll_s=180.0,
        turn_floor_s=60.0,
    )


def make(tmp_path, clocks, results, *, token="tok"):
    calls, sent = [], []
    q = Quota(
        lambda channel, ops: sent.append((channel, ops)),
        providers=(provider(clocks, results, calls, token=token),),
        cache=tmp_path,
        clock=lambda: clocks.mono,
        wall=lambda: clocks.wall,
    )
    return q, calls, sent


async def test_a_reading_goes_on_the_wire_decided(tmp_path):
    c = Clocks()
    q, calls, sent = make(tmp_path, c, [(71.0, 3 * 3600 + 6 * 60)])
    await q.tick()
    snap = q.snapshot()
    (claude,) = snap["providers"]
    assert claude | {"windows": None} == {
        "name": "claude",
        "label": "Claude",
        "harness": "claude-code",
        "account": None,
        "state": "ok",
        "note": "",
        "read_at": round(WALL),
        "retry_at": None,
        "windows": None,
    }
    five, week = claude["windows"]
    # 71% with 38% of the window gone lands at 187% by the reset.
    assert five == {
        "kind": "session",
        "label": "5 hours",
        "percent": 71.0,
        "severity": "critical",
        "projected": 187,
        "starts_at": round(WALL + 11160 - H5),
        "resets_at": round(WALL + 11160),
    }
    assert week["severity"] == "normal" and week["projected"] == 66
    assert sent == [("quota", [{"set": snap}])]


async def test_nothing_is_published_until_the_snapshot_changes(tmp_path):
    c = Clocks()
    q, calls, sent = make(tmp_path, c, [(71.0, 11160)])
    await q.tick()
    await q.tick()
    assert len(sent) == 1 and len(calls) == 1
    c.advance(60)  # the projection moves with the clock, without a fetch
    await q.tick()
    assert len(sent) == 2 and len(calls) == 1
    assert sent[-1][1][0]["set"]["providers"][0]["windows"][0]["projected"] == 185


async def test_a_failing_fetch_after_a_reading_is_stale_and_unprojected(tmp_path):
    c = Clocks()
    q, calls, sent = make(tmp_path, c, [(71.0, 11160), QuotaError("unreachable")])
    await q.tick()
    c.advance(181)
    await q.tick()
    (claude,) = q.snapshot()["providers"]
    assert claude["state"] == "stale" and claude["note"] == "unreachable"
    assert claude["read_at"] == round(WALL)
    for w in claude["windows"]:
        assert (w["severity"], w["projected"], w["starts_at"]) == ("normal", None, None)


async def test_no_credentials_is_absent_and_a_failure_is_a_note(tmp_path):
    q, _, _ = make(tmp_path / "a", Clocks(), [], token=None)
    await q.tick()
    assert q.snapshot() == {"providers": []}

    q, _, _ = make(tmp_path / "b", Clocks(), [QuotaError("unauthorized")])
    await q.tick()
    (claude,) = q.snapshot()["providers"]
    assert (claude["state"], claude["note"], claude["windows"]) == (
        "failed",
        "auth expired",
        [],
    )


async def test_a_rate_limit_says_when_it_will_retry(tmp_path):
    q, _, _ = make(tmp_path, Clocks(), [QuotaError("rate_limited")])
    await q.tick()
    (claude,) = q.snapshot()["providers"]
    assert (claude["note"], claude["retry_at"]) == ("rate limited", round(WALL + 300))


async def test_reading_the_snapshot_never_fetches(tmp_path):
    """`quota.read` is this call; an agent asking must not cost a request."""
    q, calls, _ = make(tmp_path, Clocks(), [(71.0, 11160)])
    await q.tick()
    for _ in range(5):
        q.snapshot()
    assert len(calls) == 1


async def test_a_provider_appears_when_its_credentials_do(tmp_path):
    token = {"value": None}
    c = Clocks()
    q, calls, _ = make(
        tmp_path, c, [(10.0, 11160)], token=lambda path=None: token["value"]
    )
    await q.tick()
    assert q.snapshot()["providers"] == []
    token["value"] = "tok"
    c.advance(181)  # the token is read again on the next poll, not every tick
    await q.tick()
    assert [p["state"] for p in q.snapshot()["providers"]] == ["ok"]


async def test_a_turn_end_refreshes_no_sooner_than_the_turn_floor(tmp_path):
    c = Clocks()
    q, calls, sent = make(tmp_path, c, [(50.0, 11160), (55.0, 11100)])
    q.start()
    await until(lambda: len(calls) == 1, what="the first tick")
    q.turn_ended()
    await asyncio.sleep(0.05)
    assert len(calls) == 1
    c.advance(61)
    q.turn_ended()
    await until(lambda: len(calls) == 2, what="the turn-end refresh")
    await until(
        lambda: sent[-1][1][0]["set"]["providers"][0]["windows"][0]["percent"] == 55.0
    )
    await q.stop()


async def test_start_returns_before_a_slow_first_fetch(tmp_path):
    release = threading.Event()
    c = Clocks()
    calls = []
    slow = provider(c, [(10.0, 11160)], calls)
    fetch = slow.fetch

    def blocking(tok, **kw):
        release.wait(5)
        return fetch(tok, **kw)

    q = Quota(
        lambda *a: None,
        providers=(dataclasses.replace(slow, fetch=blocking),),
        cache=tmp_path,
        clock=lambda: c.mono,
        wall=lambda: c.wall,
    )
    q.start()
    assert q.snapshot() == {"providers": []}
    release.set()
    await until(lambda: q.snapshot()["providers"], what="the reading")
    await q.stop()


def test_the_cache_dir_takes_the_override_then_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("AEGIS_QUOTA_CACHE")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert cache_dir() == tmp_path / "xdg" / "aegis" / "quota"
    monkeypatch.setenv("AEGIS_QUOTA_CACHE", str(tmp_path / "here"))
    assert cache_dir() == tmp_path / "here"
