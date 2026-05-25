"""Hermetic two-serve schedule push cycle test.

A pushes a schedule to B; B's hot-reload picks it up (triggered
manually by the test for determinism); B fires it via FakeClock; A
reads the JSONL audit log back; A deletes; B drops it.
"""
from __future__ import annotations

import pytest

from aegis.workflow.decorator import workflow
from tests.fixtures.two_serves import build_two_serves


@pytest.mark.asyncio
async def test_schedule_push_cycle_e2e(monkeypatch, tmp_path):
    # Register a tiny test workflow. Inline because other tests in the
    # suite clear _REGISTRY in their fixtures — a module-level decorator
    # wouldn't survive collection order.
    @workflow("test_tick")
    async def _test_tick(engine):
        return "ok"

    pair = await build_two_serves(monkeypatch, tmp_path)
    try:
        spec_body = {
            "workflow": "test_tick",
            "cron": "*/1 * * * *",
            "lifecycle": "forever",
        }
        push = await pair.push_schedule_a_to_b(
            name="ticker", spec_body=spec_body)
        assert push["name"] == "ticker", push

        # No real hot-reload watcher in this hermetic test — load from disk.
        await pair.reload_b()
        await pair.wait_for_schedule_on_b("ticker", timeout=5.0)

        # Advance FakeClock past the next minute and tick once.
        await pair.tick_b(seconds=70)
        await pair.wait_for_fire_count_on_b("ticker", count=1, timeout=5.0)

        logs = await pair.fetch_schedule_logs_from_a("ticker")
        assert "records" in logs, logs
        assert any(rec.get("event") == "fire_completed"
                   for rec in logs["records"]), logs["records"]

        await pair.remove_schedule_from_a("ticker")
        await pair.wait_for_schedule_gone_on_b("ticker", timeout=5.0)
    finally:
        await pair.shutdown()
