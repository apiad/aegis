"""Quota on the wire: what the `quota` channel and `quota.read` carry, and
when the channel publishes."""

import asyncio
import dataclasses
import threading
from datetime import datetime, timezone


from aegis.quota import Quota, account_hash, cache_dir
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


def provider(clocks, results, calls, *, token="tok", account=None):
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
        account=lambda: account,
        window_spans={"session": H5, "weekly_all": WK},
        poll_s=180.0,
        turn_floor_s=60.0,
    )


def make(tmp_path, clocks, results, *, token="tok", account=None):
    calls, sent = [], []
    q = Quota(
        lambda channel, ops: sent.append((channel, ops)),
        providers=(provider(clocks, results, calls, token=token, account=account),),
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
    # 71% with 38% of the window gone lands at 156% by the reset.
    assert five == {
        "kind": "session",
        "label": "5 hours",
        "percent": 71.0,
        "severity": "warning",
        "projected": 156,
        "starts_at": round(WALL + 11160 - H5),
        "resets_at": round(WALL + 11160),
    }
    assert week["severity"] == "normal" and week["projected"] == 70
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
    assert sent[-1][1][0]["set"]["providers"][0]["windows"][0]["projected"] == 155


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


async def test_listeners_hear_every_check_changed_or_not(tmp_path):
    c = Clocks()
    q, _, sent = make(tmp_path, c, [(71.0, 11160)])
    heard = []
    q.listeners.append(heard.append)
    q.listeners.insert(0, lambda snap: 1 / 0)  # a broken listener stops nothing
    await q.tick()
    await q.tick()
    assert len(sent) == 1 and len(heard) == 2
    assert heard[0] == q.snapshot()


async def test_a_projection_starts_from_what_the_account_usually_spends(tmp_path):
    c = Clocks()
    q, _, _ = make(tmp_path, c, [(0.0, H5)], account="acct")
    asked = []

    def history(billing, kind, span):
        asked.append((billing, kind, span))
        return 24.0 if kind == "session" else None

    q.history = history
    await q.tick()
    five, week = q.snapshot()["providers"][0]["windows"]
    assert five["projected"] == 24
    # No finished window of this kind yet: 47% with 5/7 of the week gone.
    assert week["projected"] == 70
    billing = "claude:" + account_hash("acct")
    assert asked == [(billing, "session", H5), (billing, "weekly_all", WK)]


async def test_history_is_read_once_per_window(tmp_path):
    c = Clocks()
    q, _, _ = make(tmp_path, c, [(1.0, H5), (2.0, H5 - 181), (3.0, H5)], account="acct")
    asked = []

    def history(billing, kind, span):
        asked.append(kind)
        return 24.0

    q.history = history
    await q.tick()
    q.snapshot()
    assert asked.count("session") == 1
    c.advance(180)
    await q.tick()  # a vendor's reset time, a second off: the same window
    assert asked.count("session") == 1
    c.advance(H5)
    await q.tick()  # a new window: read again
    assert asked.count("session") == 2


async def test_a_quota_without_a_journal_asks_nothing_and_uses_the_default(tmp_path):
    c = Clocks()
    q, _, _ = make(tmp_path, c, [(0.0, H5)], account="acct")
    await q.tick()
    assert q.snapshot()["providers"][0]["windows"][0]["projected"] == 100
