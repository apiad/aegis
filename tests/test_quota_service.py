import pytest

from aegis.quota.core import (
    POLL_S,
    QuotaError,
    QuotaService,
    QuotaSnapshot,
    QuotaWindow,
)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _snap(now, pct=64.0):
    return QuotaSnapshot(
        windows=(QuotaWindow("session", pct, "normal", None, True),), fetched_at=now
    )


def _service(clock, results):
    """`results` is a list of snapshot-or-exception, consumed per call."""
    calls = []

    def fetch(token, **kw):
        calls.append(token)
        item = results.pop(0) if results else _snap(clock())
        if isinstance(item, Exception):
            raise item
        return item

    svc = QuotaService(clock=clock, fetch=fetch, token_reader=lambda path=None: "tok")
    svc._calls = calls
    return svc


@pytest.mark.asyncio
async def test_first_refresh_fetches_and_reports_fresh():
    c = Clock()
    svc = _service(c, [_snap(c())])
    await svc.refresh()
    state = svc.current()
    assert state.failure == ""
    assert state.snapshot.window("session").percent == 64.0
    assert state.age_s == 0.0


@pytest.mark.asyncio
async def test_refresh_respects_the_floor():
    c = Clock()
    svc = _service(c, [_snap(c()), _snap(c(), 70.0)])
    await svc.refresh()
    c.advance(POLL_S - 1)
    await svc.refresh()
    assert len(svc._calls) == 1


@pytest.mark.asyncio
async def test_refresh_fetches_again_past_the_floor():
    c = Clock()
    svc = _service(c, [_snap(1000.0), _snap(1000.0 + POLL_S, 70.0)])
    await svc.refresh()
    c.advance(POLL_S + 1)
    await svc.refresh()
    assert len(svc._calls) == 2
    assert svc.current().snapshot.window("session").percent == 70.0


@pytest.mark.asyncio
async def test_force_bypasses_the_floor():
    c = Clock()
    svc = _service(c, [_snap(c()), _snap(c(), 70.0)])
    await svc.refresh()
    await svc.refresh(force=True)
    assert len(svc._calls) == 2


@pytest.mark.asyncio
async def test_custom_min_interval_allows_an_earlier_refetch():
    c = Clock()
    svc = _service(c, [_snap(c()), _snap(c(), 70.0)])
    await svc.refresh()
    c.advance(11)
    await svc.refresh(min_interval=10.0)
    assert len(svc._calls) == 2


@pytest.mark.asyncio
async def test_missing_credentials_reports_no_credentials():
    c = Clock()
    svc = QuotaService(
        clock=c, fetch=lambda *a, **k: _snap(c()), token_reader=lambda path=None: None
    )
    await svc.refresh()
    assert svc.current().failure == "no_credentials"
    assert svc.current().snapshot is None


@pytest.mark.asyncio
async def test_failure_after_success_goes_stale_and_keeps_the_value():
    c = Clock()
    svc = _service(c, [_snap(1000.0), QuotaError("unreachable")])
    await svc.refresh()
    c.advance(POLL_S + 1)
    await svc.refresh()
    state = svc.current()
    assert state.failure == "unreachable"
    assert state.snapshot is not None
    assert state.age_s == pytest.approx(POLL_S + 1)


@pytest.mark.asyncio
async def test_sustained_failure_keeps_the_last_reading():
    """The last reading stays on screen marked stale for as long as fetches
    fail. Dropping it after five minutes erased cc from F3 entirely (#41)."""
    c = Clock()
    svc = _service(c, [_snap(1000.0)] + [QuotaError("unreachable")] * 10)
    await svc.refresh()
    for _ in range(9):
        c.advance(POLL_S + 1)
        await svc.refresh()
    state = svc.current()
    assert state.snapshot is not None
    assert state.failure == "unreachable"
    assert state.age_s == pytest.approx(9 * (POLL_S + 1))


@pytest.mark.asyncio
async def test_success_clears_a_previous_failure():
    c = Clock()
    svc = _service(
        c, [_snap(1000.0), QuotaError("unreachable"), _snap(1000.0 + 2 * POLL_S, 70.0)]
    )
    await svc.refresh()
    c.advance(POLL_S + 1)
    await svc.refresh()
    c.advance(POLL_S + 1)
    await svc.refresh()
    assert svc.current().failure == ""
    assert svc.current().snapshot.window("session").percent == 70.0


@pytest.mark.asyncio
async def test_unauthorized_is_reported_distinctly():
    c = Clock()
    svc = _service(c, [QuotaError("unauthorized")])
    await svc.refresh()
    assert svc.current().failure == "unauthorized"


@pytest.mark.asyncio
async def test_start_is_idempotent_and_stop_is_safe():
    c = Clock()
    svc = _service(c, [])
    svc.start()
    first = svc._task
    svc.start()
    assert svc._task is first
    await svc.stop()
    assert svc._task is None
    await svc.stop()


@pytest.mark.asyncio
async def test_rate_limit_backs_off_and_ignores_force():
    from aegis.quota.core import BACKOFF_S

    c = Clock()
    svc = _service(c, [QuotaError("rate_limited"), _snap(2000.0)])
    await svc.refresh()
    assert svc.current().failure == "rate_limited"
    # Neither the cadence nor an explicit force may touch it during backoff.
    c.advance(POLL_S + 1)
    await svc.refresh()
    await svc.refresh(force=True)
    assert len(svc._calls) == 1
    # Past the backoff window it tries again.
    c.advance(BACKOFF_S)
    await svc.refresh()
    assert len(svc._calls) == 2
    assert svc.current().failure == ""


@pytest.mark.asyncio
async def test_rate_limit_keeps_a_previous_snapshot_visible():
    c = Clock()
    svc = _service(c, [_snap(1000.0), QuotaError("rate_limited")])
    await svc.refresh()
    c.advance(POLL_S + 1)
    await svc.refresh()
    state = svc.current()
    assert state.failure == "rate_limited"
    assert state.snapshot is not None


# --- the shared disk cache (#41) ---------------------------------------------


class Wall(Clock):
    def __init__(self):
        self.t = 1_800_000_000.0


def _cached(clock, wall, path, results, **kw):
    svc = _service(clock, results)
    svc._wall = wall
    svc._cache = path
    for k, v in kw.items():
        setattr(svc, k, v)
    return svc


@pytest.mark.asyncio
async def test_a_new_service_starts_from_the_cached_reading_with_its_real_age(tmp_path):
    """A TUI restarted during a 429 shows the last reading, aged by the wall
    clock rather than reset to zero by the new process's monotonic one."""
    path = tmp_path / "claude.json"
    c, w = Clock(), Wall()
    first = _cached(c, w, path, [_snap(c())])
    await first.refresh()

    c2 = Clock()
    c2.t = 50.0  # another process: an unrelated monotonic origin
    w.advance(600)
    second = _cached(c2, w, path, [QuotaError("rate_limited")])
    await second.refresh()
    state = second.current()
    assert state.snapshot.window("session").percent == 64.0
    assert state.age_s == pytest.approx(600)
    assert state.failure == "rate_limited"


@pytest.mark.asyncio
async def test_a_reading_another_process_just_fetched_is_adopted_not_refetched(
    tmp_path,
):
    path = tmp_path / "claude.json"
    c, w = Clock(), Wall()
    first = _cached(c, w, path, [_snap(c(), 71.0)])
    await first.refresh()
    w.advance(30)
    second = _cached(Clock(), w, path, [_snap(0.0, 99.0)])
    await second.refresh()
    assert second._calls == []
    assert second.current().snapshot.window("session").percent == 71.0
    assert second.current().failure == ""


@pytest.mark.asyncio
async def test_a_429_seen_by_one_process_backs_off_the_others(tmp_path):
    path = tmp_path / "claude.json"
    c, w = Clock(), Wall()
    first = _cached(c, w, path, [_snap(c()), QuotaError("rate_limited")])
    await first.refresh()
    c.advance(POLL_S + 1)
    w.advance(POLL_S + 1)
    await first.refresh()
    assert first.current().failure == "rate_limited"

    w.advance(10)
    second = _cached(Clock(), w, path, [_snap(0.0, 99.0)])
    await second.refresh(force=True)
    assert second._calls == []
    state = second.current()
    assert state.failure == "rate_limited"
    assert state.snapshot.window("session").percent == 64.0


@pytest.mark.asyncio
async def test_the_floor_follows_the_service_poll_interval():
    c = Clock()
    svc = _service(c, [_snap(c()), _snap(c(), 70.0)])
    svc._poll_s = 180.0
    await svc.refresh()
    c.advance(POLL_S + 1)
    await svc.refresh()
    assert len(svc._calls) == 1
    c.advance(180.0)
    await svc.refresh()
    assert len(svc._calls) == 2


def test_claude_polls_every_three_minutes():
    """60 s per process starved the endpoint into 429s (#41)."""
    from aegis.quota.claude import PROVIDER

    assert PROVIDER.poll_s == 180.0
    assert PROVIDER.turn_floor_s == 60.0


async def test_a_backoff_reports_how_long_until_the_next_try():
    from aegis.quota.core import BACKOFF_S

    c = Clock()
    svc = _service(c, [QuotaError("rate_limited")])
    await svc.refresh()
    assert svc.current().retry_in_s == pytest.approx(BACKOFF_S)
    c.advance(100)
    assert svc.current().retry_in_s == pytest.approx(BACKOFF_S - 100)
    c.advance(BACKOFF_S)
    assert svc.current().retry_in_s == 0.0


async def test_stop_lets_a_cancel_meant_for_its_caller_through():
    """#72: the legacy stop() swallowed every CancelledError, including one
    aimed at whoever was awaiting stop(), so a shutdown that cancelled it hung
    on as if nothing had happened."""
    import asyncio

    svc = QuotaService(fetch=lambda token: None, token_reader=lambda: None)

    async def dies_slowly():  # as a poller does with a fetch thread in flight
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)

    svc._task = asyncio.create_task(dies_slowly())
    await asyncio.sleep(0)
    caller = asyncio.create_task(svc.stop())
    await asyncio.sleep(0.05)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller


def test_the_default_clock_keeps_counting_while_the_machine_sleeps():
    """CLOCK_MONOTONIC stops during suspend on Linux: a reading from before an
    8-hour sleep read as 5 s old, so it showed as fresh and sat inside the
    floor, never refetched (final review of #146)."""
    import time

    from aegis.quota import Quota
    from aegis.quota.core import boot_clock

    assert QuotaService(fetch=None, token_reader=None)._clock is boot_clock
    assert Quota(lambda *a: None).services["claude"]._clock is boot_clock
    if hasattr(time, "CLOCK_BOOTTIME"):
        # BOOTTIME is MONOTONIC plus the time asleep, so it reads at least as
        # much when read after it; on a machine that never slept they are equal.
        mono = time.clock_gettime(time.CLOCK_MONOTONIC)
        assert boot_clock() >= mono
