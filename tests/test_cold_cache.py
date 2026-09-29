"""Warn before a message re-reads a large context uncached. Issue #25."""

import pytest

from aegis.cold_cache import (
    CACHE_TTL_S,
    MIN_CONTEXT_TOKENS,
    cold_cache_warning,
)
from aegis.events import Result
from tests.test_pane_windowing import _app

TTL = CACHE_TTL_S["claude-code"]


def _warn(**kw):
    args = dict(
        harness="claude-code",
        idle_s=TTL + 60,
        context_tokens=MIN_CONTEXT_TOKENS * 3,
        agent_slug="opus",
    )
    args.update(kw)
    return cold_cache_warning(**args)


def test_a_large_context_idle_past_the_ttl_is_warned():
    text = _warn()
    assert text is not None
    assert "/spawn opus continue this task" in text
    assert "300k" in text


def test_no_warning_while_the_cache_is_still_warm():
    assert _warn(idle_s=TTL - 1) is None


def test_no_warning_for_a_small_context():
    assert _warn(context_tokens=MIN_CONTEXT_TOKENS - 1) is None


def test_no_warning_for_a_harness_whose_ttl_is_unknown():
    assert _warn(harness="gemini") is None


def _warnings(pane) -> int:
    return sum("continue this task" in r.payload for r in pane._history)


async def _idle_pane(app, pilot, *, idle_s: float, context_tokens: int):
    pane = app._panes[0]
    pane._core.metrics.last_true_input = context_tokens
    pane._on_core_event(None, Result(duration_ms=1, is_error=False))
    await pilot.pause()
    pane._last_result.ended_at -= idle_s
    return pane


@pytest.mark.asyncio
async def test_the_pane_warns_once_when_it_is_looked_at_after_the_ttl():
    app = _app()
    async with app.run_test() as pilot:
        pane = await _idle_pane(
            app, pilot, idle_s=TTL + 60, context_tokens=MIN_CONTEXT_TOKENS * 3
        )
        pane.refresh_result_age()
        pane.refresh_result_age()
        await pilot.pause()
        assert _warnings(pane) == 1


@pytest.mark.asyncio
async def test_the_pane_stays_quiet_inside_the_ttl():
    app = _app()
    async with app.run_test() as pilot:
        pane = await _idle_pane(
            app, pilot, idle_s=TTL - 60, context_tokens=MIN_CONTEXT_TOKENS * 3
        )
        pane.refresh_result_age()
        await pilot.pause()
        assert _warnings(pane) == 0


@pytest.mark.asyncio
async def test_each_idle_stretch_gets_its_own_warning():
    """A warning belongs to the turn it follows. After the next turn ends and
    the tab goes cold again, the operator is facing a new re-read."""
    app = _app()
    async with app.run_test() as pilot:
        pane = await _idle_pane(
            app, pilot, idle_s=TTL + 60, context_tokens=MIN_CONTEXT_TOKENS * 3
        )
        pane.refresh_result_age()
        pane._on_core_event(None, Result(duration_ms=1, is_error=False))
        await pilot.pause()
        pane._last_result.ended_at -= TTL + 60
        pane.refresh_result_age()
        await pilot.pause()
        assert _warnings(pane) == 2
