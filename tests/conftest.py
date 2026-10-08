import asyncio
import json
import stat
import sys
from pathlib import Path

import pytest

FAKE = Path(__file__).parent / "fake_claude.py"


@pytest.fixture
def fake_claude(tmp_path: Path) -> str:
    """An executable that runs the fake claude, as a session would exec it."""
    path = tmp_path / "bin" / "claude"
    path.parent.mkdir()
    home = tmp_path / "fake-home"
    home.mkdir()
    path.write_text(
        f'#!/bin/sh\nexport FAKE_CLAUDE_HOME="{home}"\nexec "{sys.executable}" "{FAKE}" "$@"\n'
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


async def until(pred, timeout: float = 3.0, what: str = "condition") -> None:
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.01)


async def argv_of(s) -> list[str]:
    """The argv the session's current claude process was started with, as the
    fake reports it for ``/argv``."""

    def prose() -> list[str]:
        return [e["md"] for e in s.entries() if e["kind"] == "prose"]

    before = len(prose())
    await s.send("/argv")
    await until(
        lambda: s.status == "idle" and len(prose()) > before,
        timeout=8,
        what="the /argv turn",
    )
    return json.loads(prose()[-1].removeprefix("argv: "))


@pytest.fixture(autouse=True)
def _no_real_quota(tmp_path_factory, monkeypatch):
    """No test reads the real OAuth token or the machine's quota cache. Reading
    the token would let a test reach a vendor's usage endpoint, whose 429s
    starve the gauges of the aegis Alex is running (#41); writing the cache
    would overwrite what that aegis shows. Not XDG_CACHE_HOME: Playwright looks
    for its Chromium under it. The browser tests' `aegis serve` inherits these."""
    off = tmp_path_factory.mktemp("quota")
    monkeypatch.setenv("CLAUDE_CREDS", str(off / "claude-credentials.json"))
    monkeypatch.setenv("OPENCODE_AUTH", str(off / "opencode-auth.json"))
    monkeypatch.setenv("AEGIS_QUOTA_CACHE", str(off / "cache"))


# -- run options, carried over from the legacy tree's conftest --------------------
def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run tests marked live (real agent CLIs and models; spends quota)",
    )
    parser.addoption(
        "--max-unmarked-duration",
        type=float,
        default=None,
        metavar="SECONDS",
        help="fail the run when a test not marked slow takes longer than this, setup and teardown included",
    )


def pytest_collection_modifyitems(config, items):
    """Live tests are opt-in: on a dev machine every CLI is installed, and a bare
    `pytest` would spend real quota."""
    if config.getoption("--run-live"):
        return
    skip = pytest.mark.skip(reason="live: pass --run-live (make test-live)")
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(skip)


_durations: dict[str, float] = {}
_marked_slow: set[str] = set()


def pytest_runtest_logreport(report):
    _durations[report.nodeid] = _durations.get(report.nodeid, 0.0) + report.duration
    if "slow" in report.keywords:
        _marked_slow.add(report.nodeid)


def _over_budget(config) -> list[tuple[float, str]]:
    budget = config.getoption("--max-unmarked-duration")
    if budget is None:
        return []
    return sorted(
        ((d, n) for n, d in _durations.items() if n not in _marked_slow and d > budget),
        reverse=True,
    )


def pytest_sessionfinish(session, exitstatus):
    """The slow marker is worth something only while it stays true: a test that
    grows past the budget unmarked fails the run instead of slowing the fast lane."""
    if hasattr(session.config, "workerinput"):
        return
    if _over_budget(session.config) and session.exitstatus == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    over = _over_budget(config)
    if not over:
        return
    budget = config.getoption("--max-unmarked-duration")
    terminalreporter.section(f"unmarked tests over {budget:g}s", sep="=", red=True)
    for seconds, nodeid in over:
        terminalreporter.line(f"{seconds:6.2f}s  {nodeid}")
    terminalreporter.line("make them faster, or mark them @pytest.mark.slow")
