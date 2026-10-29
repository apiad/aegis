import asyncio
import contextlib
import fcntl
import json
import os
import re
import signal
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
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


FAKE_OPENCODE = Path(__file__).parent / "fake_opencode.py"


@pytest.fixture
def fake_opencode(tmp_path: Path) -> str:
    """An executable that runs the fake opencode, as a session would exec it."""
    path = tmp_path / "bin-opencode" / "opencode"
    path.parent.mkdir()
    home = tmp_path / "fake-opencode-home"
    home.mkdir()
    path.write_text(
        f'#!/bin/sh\nexport FAKE_OPENCODE_HOME="{home}"\n'
        f'exec "{sys.executable}" "{FAKE_OPENCODE}" "$@"\n'
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


FAKE_CODEX = Path(__file__).parent / "fake_codex.py"


@pytest.fixture
def fake_codex(tmp_path: Path) -> str:
    """An executable that runs the fake codex app-server, as a session would."""
    path = tmp_path / "bin-codex" / "codex"
    path.parent.mkdir()
    home = tmp_path / "fake-codex-home"
    home.mkdir()
    path.write_text(
        f'#!/bin/sh\nexport FAKE_CODEX_HOME="{home}"\n'
        f'export FAKE_CODEX_LOG="{tmp_path / "fake-codex.log"}"\n'
        f'exec "{sys.executable}" "{FAKE_CODEX}" "$@"\n'
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


FAKE_CURSOR = Path(__file__).parent / "fake_cursor.py"


@pytest.fixture
def fake_cursor(tmp_path: Path) -> str:
    """An executable that runs the fake cursor-agent ACP server."""
    path = tmp_path / "bin-cursor" / "cursor-agent"
    path.parent.mkdir()
    home = tmp_path / "fake-cursor-home"
    home.mkdir()
    path.write_text(
        f'#!/bin/sh\nexport FAKE_CURSOR_HOME="{home}"\n'
        f'export FAKE_CURSOR_LOG="{tmp_path / "fake-cursor.log"}"\n'
        f'exec "{sys.executable}" "{FAKE_CURSOR}" "$@"\n'
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


async def cmdline(pid: int) -> list[bytes]:
    """A child's argv from /proc, once the wrapper script has exec'd the fake:
    during the exec the file can read empty."""
    path = Path(f"/proc/{pid}/cmdline")
    await until(lambda: b"fake_claude" in path.read_bytes(), what="the exec")
    return path.read_bytes().split(b"\0")


@pytest.fixture(autouse=True)
def _no_real_quota(tmp_path_factory, monkeypatch):
    """No test reads the real OAuth token or the machine's quota cache. Reading
    the token would let a test reach a vendor's usage endpoint, whose 429s
    starve the gauges of the aegis Alex is running (#41); writing the cache
    would overwrite what that aegis shows. Not XDG_CACHE_HOME: Playwright looks
    for its Chromium under it. The browser tests' `aegis serve` inherits these.
    Nor does any test download the dictation model into the real cache."""
    off = tmp_path_factory.mktemp("quota")
    monkeypatch.setenv("CLAUDE_CREDS", str(off / "claude-credentials.json"))
    monkeypatch.setenv("CLAUDE_CONFIG", str(off / "claude.json"))
    monkeypatch.setenv("OPENCODE_AUTH", str(off / "opencode-auth.json"))
    monkeypatch.setenv("AEGIS_QUOTA_CACHE", str(off / "cache"))
    # Nor the Claude token the machine's sessions sign in with (claude/login.py).
    monkeypatch.setenv("AEGIS_CLAUDE_TOKEN_FILE", str(off / "claude-oauth-token"))
    monkeypatch.setenv("AEGIS_DICTATION_DIR", str(off / "dictation"))
    # The network probes need no credentials, so only a switch keeps a test's
    # `aegis serve` off the network (net/service.py).
    monkeypatch.setenv("AEGIS_NET_PROBES", "offline")


@pytest.fixture(scope="session", autouse=True)
def _no_opencode_left(tmp_path_factory):
    """No OpenCode server, real or fake, outlives the run that started it
    (#620): a day of leaked ones, about 300 MB each, took a 32 GB host into
    the OOM killer. Every child this run caused, through a test or a test's
    `aegis serve`, inherits an OPENCODE_AUTH under this run's base temp
    (_no_real_quota), and nothing else does. Those left are ended by pid."""
    from aegis.opencode.sweep import SERVE_ARGS

    mark = b"OPENCODE_AUTH=" + str(tmp_path_factory.getbasetemp()).encode()
    yield

    def left() -> dict[int, int]:
        """The run's servers still running, with their RSS in kB."""
        out = {}
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            try:
                argv = (d / "cmdline").read_bytes().decode(errors="replace")
                if argv.split("\0")[-6:-1] != SERVE_ARGS:
                    continue
                env = (d / "environ").read_bytes().split(b"\0")
                status = (d / "status").read_text()
            except OSError:
                continue
            if any(e.startswith(mark) for e in env):
                rss = re.search(r"VmRSS:\s+(\d+)", status)
                out[int(d.name)] = int(rss.group(1)) if rss else 0
        return out

    deadline = time.monotonic() + 10  # a last server's children may be ending
    while (strays := left()) and time.monotonic() < deadline:
        time.sleep(0.2)
    for pid in strays:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)
    if strays:
        mb = sum(strays.values()) / 1024
        pytest.fail(
            f"{len(strays)} opencode serve process(es) outlived the run, "
            f"{mb:.0f} MB resident: {sorted(strays)}",
            pytrace=False,
        )


# A half-full disk and no memory or CPU reading: below every default line.
CALM = {
    "disk": {"pct": 50, "free_gb": 37.5, "total_gb": 75},
    "memory": None,
    "cpu": None,
}


@pytest.fixture(autouse=True)
def _calm_host(monkeypatch):
    """An app built in the test process reads this, not the machine running
    the tests: on a loaded host every session would be told about it, and a
    tool result that carries a notice no longer parses as JSON. A test that
    wants a notice sets its line low in the config (tests/test_notices.py)."""
    import aegis.app
    from aegis.notices import Notices

    monkeypatch.setattr(
        aegis.app, "Notices", lambda app: Notices(app, read=lambda paths: CALM)
    )


# -- run options, carried over from the legacy tree's conftest --------------------
def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run tests marked live (real agent CLIs and models; spends quota)",
    )
    parser.addoption(
        "--run-canary",
        action="store_true",
        default=False,
        help="run tests marked canary (the real harnesses against a fake model)",
    )
    parser.addoption(
        "--run-multiuser",
        action="store_true",
        default=False,
        help="run tests marked multiuser (creates Linux users; needs sudo and systemd)",
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
    skips = {
        "live": pytest.mark.skip(reason="live: pass --run-live (make test-live)"),
        "canary": pytest.mark.skip(reason="canary: pass --run-canary (make canary)"),
        "multiuser": pytest.mark.skip(
            reason="multiuser: pass --run-multiuser (make test-multiuser)"
        ),
    }
    for marker, skip in skips.items():
        if config.getoption(f"--run-{marker}"):
            continue
        for item in items:
            if item.get_closest_marker(marker):
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


# -- a host-wide cap on concurrent browsers (#360) -----------------------------
# Several `pytest -n auto` runs on one host each started a Chromium per worker,
# and together they ran the host out of memory. A browser now takes one of N
# slot files under flock first, so every run on the host shares N. A doc rule
# saying "don't run suites in parallel" does not stop an agent; this does.
BROWSER_SLOT_DIR = Path(tempfile.gettempdir()) / f"aegis-test-browsers-{os.getuid()}"
# A Chromium, the aegis serve it drives and that server's fake harnesses.
BROWSER_SLOT_KB = 1024 * 1024
BROWSER_SLOT_WAIT_S = 900.0
_slots: int | None = None
_held = 0


def browser_slots() -> int:
    """AEGIS_TEST_BROWSERS, or one slot per GiB of available memory, at most
    one per core and at least one. Read once per process."""
    global _slots
    if _slots is None:
        if env := os.environ.get("AEGIS_TEST_BROWSERS"):
            _slots = max(1, int(env))
        else:
            cores = os.cpu_count() or 1
            try:
                meminfo = Path("/proc/meminfo").read_text().splitlines()
                kb = next(
                    int(x.split()[1]) for x in meminfo if x.startswith("MemAvailable:")
                )
                _slots = max(1, min(cores, kb // BROWSER_SLOT_KB))
            except (OSError, StopIteration):
                _slots = cores
    return _slots


@contextmanager
def browser_slot():
    """Hold one browser slot for as long as the block runs. The lock is the
    open file, so a killed run frees its slot with its process."""
    global _held
    BROWSER_SLOT_DIR.mkdir(exist_ok=True)
    deadline = time.monotonic() + BROWSER_SLOT_WAIT_S
    fd = None
    while fd is None:
        for i in range(browser_slots()):
            f = os.open(BROWSER_SLOT_DIR / f"slot-{i}", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(f)
                continue
            fd = f
            break
        else:
            if time.monotonic() > deadline:
                pytest.fail(
                    f"no browser slot free in {BROWSER_SLOT_DIR} after {BROWSER_SLOT_WAIT_S:g}s"
                )
            time.sleep(0.2)
    _held += 1
    try:
        yield
    finally:
        _held -= 1
        os.close(fd)


try:
    from playwright.sync_api import BrowserType
except ImportError:
    pass
else:
    _launch = BrowserType.launch

    def _launch_in_a_slot(self, *args, **kwargs):
        assert _held, (
            "launch a browser inside `with browser_slot():` (tests/conftest.py)"
        )
        return _launch(self, *args, **kwargs)

    BrowserType.launch = _launch_in_a_slot
