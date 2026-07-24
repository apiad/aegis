import pytest

from aegis.usage.quota import (
    POLL_S, QuotaError, QuotaService, QuotaSnapshot, QuotaWindow,
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
        windows=(QuotaWindow("session", pct, "normal", None, True),),
        fetched_at=now)


def _service(clock, results):
    """`results` is a list of snapshot-or-exception, consumed per call."""
    calls = []

    def fetch(token, **kw):
        calls.append(token)
        item = results.pop(0) if results else _snap(clock())
        if isinstance(item, Exception):
            raise item
        return item

    svc = QuotaService(clock=clock, fetch=fetch,
                       token_reader=lambda path=None: "tok")
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
    svc = QuotaService(clock=c, fetch=lambda *a, **k: _snap(c()),
                       token_reader=lambda path=None: None)
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
async def test_sustained_failure_eventually_drops_the_snapshot():
    c = Clock()
    svc = _service(c, [_snap(1000.0)] + [QuotaError("unreachable")] * 10)
    await svc.refresh()
    for _ in range(9):
        c.advance(POLL_S + 1)
        await svc.refresh()
    state = svc.current()
    assert state.snapshot is None
    assert state.failure == "unreachable"


@pytest.mark.asyncio
async def test_success_clears_a_previous_failure():
    c = Clock()
    svc = _service(c, [_snap(1000.0), QuotaError("unreachable"),
                       _snap(1000.0 + 2 * POLL_S, 70.0)])
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
    from aegis.usage.quota import BACKOFF_S
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
