from datetime import datetime, timedelta, timezone

from aegis.fleet.models import CardView, MonitorRow, QuotaGauge
from aegis.tui.sysmeter import SystemStats, sample_system
from aegis.usage.quota import QuotaSnapshot, QuotaState, QuotaWindow, quota_gauges
from aegis.usage.quota_providers import PROVIDERS

NOW = datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc)


def test_system_stats_carry_ram_in_gigabytes(tmp_path):
    s = sample_system(tmp_path)
    assert s.ram_total_gb > 0 and 0 < s.ram_used_gb <= s.ram_total_gb


def _state(*windows):
    return QuotaState(snapshot=QuotaSnapshot(windows=tuple(windows), fetched_at=0.0))


def test_quota_gauges_follow_each_providers_bar_windows():
    claude, opencode = PROVIDERS
    readings = [
        (claude, _state(
            QuotaWindow("session", 38.0, "normal", NOW + timedelta(hours=2), True),
            QuotaWindow("weekly_all", 81.0, "warning", None, True),
        )),
        (opencode, QuotaState(failure="no_credentials")),
    ]
    got = quota_gauges(readings, now=NOW)
    assert got == (
        QuotaGauge(label="cc 5h", percent=38.0, severity="normal", resets_in_s=7200.0),
        QuotaGauge(label="cc wk", percent=81.0, severity="warning", resets_in_s=None),
    )


def test_a_card_defaults_to_no_detail():
    c = CardView(handle="a")
    assert c.plan_tasks == () and c.monitors == () and c.attention == ""


def test_the_snapshot_carries_monitors_and_plan_tasks():
    from types import SimpleNamespace

    from aegis.fleet.snapshot import build_snapshot
    from aegis.monitor.schema import MonitorView
    from aegis.tui.metrics import SessionMetrics
    from aegis.tui.state import AgentState

    class _MM:
        def snapshot(self, *, for_handle=None):
            return [MonitorView(id="m1", description="pytest", state="watching",
                                pct=60.0, eta_s=32.0, elapsed_s=48.0)] if for_handle == "a" else []

    plan = SimpleNamespace(snapshot=lambda now: None)
    s = SimpleNamespace(handle="a", state=AgentState.ready, metrics=SessionMetrics(),
                        plan=plan, plan_state=lambda: SimpleNamespace(tasks=("t",)))
    snap = build_snapshot(SimpleNamespace(_sessions=[s], monitor_manager=_MM()), now=0.0)
    card = snap.cards[0]
    assert card.monitors == (MonitorRow(id="m1", description="pytest", pct=60.0, eta_s=32.0, elapsed_s=48.0),)
    assert card.plan_tasks == ("t",)
