"""``aegis usage quota`` — live subscription quota from a shell."""
import pytest
from typer.testing import CliRunner

from aegis.usage.quota import QuotaSnapshot, QuotaState, QuotaWindow


def _state(pct=64.0, kind="session"):
    return QuotaState(snapshot=QuotaSnapshot(
        windows=(QuotaWindow(kind, pct, "normal", None, True),),
        fetched_at=0.0), age_s=3.0)


class FakeService:
    def __init__(self, state):
        self._state = state
        self.refreshed = False

    async def refresh(self, **kw):
        self.refreshed = True

    def current(self):
        return self._state


@pytest.mark.asyncio
async def test_read_all_refreshes_every_service_and_returns_readings():
    from aegis.usage.quota_providers import read_all
    services = {"claude": FakeService(_state()),
                "opencode-go": FakeService(_state(14.0, "rolling"))}
    readings = await read_all(services)
    assert all(s.refreshed for s in services.values())
    assert [p.name for p, _ in readings] == ["claude", "opencode-go"]


@pytest.mark.asyncio
async def test_read_all_skips_providers_with_no_service():
    from aegis.usage.quota_providers import read_all
    readings = await read_all({"claude": FakeService(_state())})
    assert [p.name for p, _ in readings] == ["claude"]


def test_cli_quota_prints_the_report(monkeypatch):
    from aegis import cli_usage
    monkeypatch.setattr(
        cli_usage, "build_services",
        lambda: {"claude": FakeService(_state()),
                 "opencode-go": FakeService(_state(14.0, "rolling"))})
    res = CliRunner().invoke(cli_usage.app, ["quota"])
    assert res.exit_code == 0
    assert "claude" in res.stdout
    assert "opencode-go" in res.stdout
    assert "64%" in res.stdout


def test_cli_quota_does_not_also_run_the_dashboard(monkeypatch):
    # The callback is invoke_without_command=True, so without a guard on
    # ctx.invoked_subcommand it runs the cost dashboard *as well as* the
    # subcommand — two reports in one invocation.
    from aegis import cli_usage
    monkeypatch.setattr(
        cli_usage, "build_services",
        lambda: {"claude": FakeService(_state())})

    def _boom(*a, **kw):
        raise AssertionError("the dashboard ran for a subcommand invocation")
    monkeypatch.setattr(cli_usage, "build_report", _boom)

    res = CliRunner().invoke(cli_usage.app, ["quota"])
    assert res.exit_code == 0
    assert "64%" in res.stdout


def test_bare_usage_still_runs_the_dashboard(monkeypatch):
    from aegis import cli_usage
    called = {}

    def _report(*a, **kw):
        called["yes"] = True
        class R:
            sessions = []
        return R()
    monkeypatch.setattr(cli_usage, "build_report", _report)
    res = CliRunner().invoke(cli_usage.app, [])
    assert called.get("yes") is True
    assert res.exit_code == 0
