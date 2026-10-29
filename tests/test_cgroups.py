"""One cgroup per session (#360): detection, the tree, the limits, the join,
the cleanup. All but the last test run against a fake cgroup tree in a tmp
dir; the last needs a delegated unit and is skipped without one."""

import asyncio
import logging
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from aegis import cgroups
from aegis.cgroups import SessionCgroups, own_cgroup, parse_size, wrap
from aegis.claude.harness import ClaudeCode
from aegis.codex.process import CodexProcess
from aegis.config import session_memory_max_from
from aegis.harness import Launch
from aegis.opencode.process import OpenCodeProcess
from aegis.session import Host

from .conftest import until
from .test_session import Harness

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="cgroups are a Linux feature"
)

GB = 1024**3
UNIT = "system.slice/aegis.service"


class Tree:
    """A fake /sys/fs/cgroup and /proc: the server is pid 100, started by
    ``uv`` (pid 90), both in the unit's cgroup."""

    def __init__(self, tmp: Path, rel: str = UNIT, procs: str = "100\n90\n") -> None:
        self.mount, self.proc = tmp / "cg", tmp / "proc"
        self.unit = self.mount / rel
        self.unit.mkdir(parents=True)
        (self.unit / "cgroup.controllers").write_text("cpu io memory pids\n")
        (self.unit / "cgroup.procs").write_text(procs)
        (self.unit / "cgroup.subtree_control").write_text("")
        self.pid(100, 90, f"0::/{rel}\n")
        self.pid(90, 1, f"0::/{rel}\n")

    def pid(self, pid: int, ppid: int, cgroup: str) -> None:
        d = self.proc / str(pid)
        d.mkdir(parents=True)
        (d / "cgroup").write_text(cgroup)
        (d / "stat").write_text(f"{pid} (python3) S {ppid} {pid} {pid} 0 -1\n")

    def check(self):
        return SessionCgroups.check(self.mount, self.proc, 100)

    def setup(self, memory_max="40%"):
        return SessionCgroups.setup(lambda: memory_max, self.mount, self.proc, 100)


@pytest.fixture(autouse=True)
def fake_rmdir(monkeypatch):
    # A fake cgroup holds the files the test wrote; a real one is rmdir'd.
    monkeypatch.setattr(SessionCgroups, "remove", staticmethod(shutil.rmtree))


def test_sizes_read_like_systemd_memory_max():
    assert parse_size(3 * GB, 8 * GB) == 3 * GB
    assert parse_size("3G", 8 * GB) == 3 * GB
    assert parse_size("512M", 8 * GB) == 512 * 1024**2
    assert parse_size("1.5g", 8 * GB) == int(1.5 * GB)
    assert parse_size("40%", 10 * GB) == 4 * GB
    assert parse_size("max", 8 * GB) is None
    for bad in ("lots", "0", "-1G", "0%", "120%", True, 0, -5, None, 3.5):
        with pytest.raises(ValueError):
            parse_size(bad, 8 * GB)


def test_a_bad_cap_in_the_config_keeps_the_default_and_says_why():
    assert session_memory_max_from(None) == ("40%", None)
    assert session_memory_max_from("2G") == ("2G", None)
    value, error = session_memory_max_from("plenty")
    assert value == "40%" and error and "session_memory_max" in error


def test_only_a_pure_v2_hierarchy_names_the_cgroup():
    assert (
        own_cgroup("0::/system.slice/aegis.service\n") == "/system.slice/aegis.service"
    )
    hybrid = "12:memory:/system.slice/aegis.service\n0::/system.slice/aegis.service\n"
    assert own_cgroup(hybrid) is None
    assert own_cgroup("4:memory:/x\n") is None


def test_a_delegated_service_cgroup_is_found(tmp_path):
    t = Tree(tmp_path)
    assert t.check() == t.unit


@pytest.mark.parametrize(
    "rel",
    [
        # A server started from a terminal.
        "user.slice/user-1000.slice/user@1000.service/app.slice/vte-spawn-1.scope",
        # A test run inside an agent's session: its cgroup is writable once the
        # unit is delegated, and must never be carved up.
        f"{UNIT}/sessions/log-abc",
    ],
)
def test_a_cgroup_that_is_not_a_service_is_left_alone(tmp_path, rel):
    t = Tree(tmp_path, rel)
    assert "is not a service's cgroup" in t.check()
    assert t.setup() is None
    assert not (t.unit / "server").exists()


def test_a_unit_that_shares_its_cgroup_is_left_alone(tmp_path):
    t = Tree(tmp_path, procs="100\n90\n4242\n")
    assert "4242" in t.check()
    assert t.setup() is None
    assert (t.unit / "cgroup.subtree_control").read_text() == ""


@pytest.mark.skipif(os.geteuid() == 0, reason="root may write anything")
def test_a_unit_without_delegation_is_left_alone(tmp_path, caplog, monkeypatch):
    t = Tree(tmp_path)
    (t.unit / "cgroup.subtree_control").chmod(0o444)
    monkeypatch.setenv("INVOCATION_ID", "abc")  # as under systemd
    with caplog.at_level(logging.INFO, "aegis.cgroups"):
        assert t.setup() is None
    (rec,) = caplog.records
    assert rec.levelno == logging.WARNING and "Delegate=yes" in rec.getMessage()


def test_without_the_memory_controller_nothing_is_set_up(tmp_path):
    t = Tree(tmp_path)
    (t.unit / "cgroup.controllers").write_text("cpu pids\n")
    assert "memory controller" in t.check()


def test_a_terminal_start_is_not_warned_about(tmp_path, caplog, monkeypatch):
    t = Tree(tmp_path, "user.slice/x.scope")
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    with caplog.at_level(logging.INFO, "aegis.cgroups"):
        assert t.setup() is None
    assert [r.levelno for r in caplog.records] == [logging.INFO]


def test_setup_leaves_the_unit_root_and_enables_the_controllers(tmp_path):
    t = Tree(tmp_path)
    cg = t.setup()
    assert cg is not None and cg.sessions == t.unit / "work" / "sessions"
    # The server and the uv that started it, so the unit root holds nothing.
    assert (t.unit / "server" / "cgroup.procs").read_text() == "90\n"  # the last write
    assert (t.unit / "cgroup.subtree_control").read_text() == "+io"  # the last write
    assert (cg.sessions / "cgroup.subtree_control").read_text() == "+memory +pids"


def test_setup_moves_every_process_of_the_unit(tmp_path, monkeypatch):
    t = Tree(tmp_path)
    moved = []
    real = Path.write_text

    def spy(self, text, *a, **k):
        if self.parent.name == "server" and self.name == "cgroup.procs":
            moved.append(int(text))
        return real(self, text, *a, **k)

    monkeypatch.setattr(Path, "write_text", spy)
    t.setup()
    assert moved == [100, 90]


def test_sessions_are_capped_together_below_the_unit_limit(tmp_path, monkeypatch):
    # #782: fifteen sessions under their own caps filled the unit to its
    # MemoryHigh, and the unit's throttle stalled the server with them.
    monkeypatch.setattr(cgroups, "total_ram", lambda: 10 * GB)
    t = Tree(tmp_path)
    (t.unit / "memory.high").write_text(str(6 * GB))
    (t.unit / "memory.max").write_text(str(7 * GB))
    cg = t.setup()
    assert (cg.work / "memory.max").read_text() == str(6 * GB - cgroups.SERVER_RESERVE)
    assert (t.unit / "server" / "memory.min").read_text() == str(cgroups.SERVER_RESERVE)


def test_a_unit_without_limits_caps_the_sessions_below_the_host(tmp_path, monkeypatch):
    monkeypatch.setattr(cgroups, "total_ram", lambda: 10 * GB)
    t = Tree(tmp_path)
    (t.unit / "memory.high").write_text("max")
    cg = t.setup()
    assert (cg.work / "memory.max").read_text() == str(10 * GB - cgroups.SERVER_RESERVE)


def test_a_unit_too_small_for_the_reserve_leaves_the_sessions_uncapped(tmp_path):
    t = Tree(tmp_path)
    (t.unit / "memory.max").write_text(str(cgroups.SERVER_RESERVE + 1))
    cg = t.setup()
    assert not (cg.work / "memory.max").exists()


def test_the_server_runs_first_when_every_cpu_is_busy(tmp_path, monkeypatch):
    t = Tree(tmp_path)
    enabled = []
    real = Path.write_text

    def spy(self, text, *a, **k):
        if self == t.unit / "cgroup.subtree_control":
            enabled.append(text)
        return real(self, text, *a, **k)

    monkeypatch.setattr(Path, "write_text", spy)
    cg = t.setup()
    # cpu in a write of its own: a kernel that refuses it keeps the memory caps.
    assert enabled == ["+memory +pids", "+cpu", "+io"]
    assert (t.unit / "server" / "cpu.weight").read_text() == str(
        cgroups.SERVER_CPU_WEIGHT
    )
    # Only below the unit: sessions share one weight, as one group.
    assert "cpu" not in (cg.sessions / "cgroup.subtree_control").read_text()


def test_a_refused_cpu_controller_keeps_the_memory_caps(tmp_path, monkeypatch):
    t = Tree(tmp_path)
    real = Path.write_text

    def refuse(self, text, *a, **k):
        if text == "+cpu":
            raise OSError("EBUSY")
        return real(self, text, *a, **k)

    monkeypatch.setattr(Path, "write_text", refuse)
    cg = t.setup()
    assert cg is not None
    assert (t.unit / "server" / "memory.min").exists()
    assert not (t.unit / "server" / "cpu.weight").exists()


def test_boot_sweeps_empty_session_cgroups_and_keeps_busy_ones(tmp_path):
    t = Tree(tmp_path)
    (t.unit / "work" / "sessions" / "gone").mkdir(parents=True)
    busy = t.unit / "work" / "sessions" / "busy"
    busy.mkdir()
    (busy / "cgroup.procs").write_text("5\n")
    cg = t.setup()
    assert sorted(p.name for p in cg.sessions.iterdir() if p.is_dir()) == ["busy"]


def test_a_session_cgroup_carries_its_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(cgroups, "total_ram", lambda: 10 * GB)
    cg = Tree(tmp_path).setup("40%")
    d = cg.create("log-abc")
    assert d == cg.sessions / "log-abc"
    assert (d / "memory.max").read_text() == str(4 * GB)
    # An OOM kills the runaway process, not the whole session.
    assert (d / "memory.oom.group").read_text() == "0"
    # memory.max counts RAM alone: without this a runaway pages into swap.
    assert (d / "memory.swap.max").read_text() == "0"


def test_max_means_no_cap_and_a_bad_value_means_the_default(tmp_path, monkeypatch):
    monkeypatch.setattr(cgroups, "total_ram", lambda: 10 * GB)
    t = Tree(tmp_path)
    d = t.setup("max").create("a")
    assert (d / "memory.max").read_text() == "max"
    assert (d / "memory.swap.max").read_text() == "max"
    d = SessionCgroups(t.unit, lambda: "plenty").create("b")
    assert (d / "memory.max").read_text() == str(4 * GB)


def test_release_kills_through_cgroup_kill_then_removes(tmp_path, monkeypatch):
    cg = Tree(tmp_path).setup()
    d = cg.create("log-abc")
    (d / "cgroup.kill").write_text("")
    seen = []
    monkeypatch.setattr(
        SessionCgroups,
        "remove",
        staticmethod(
            lambda p: (seen.append((p / "cgroup.kill").read_text()), shutil.rmtree(p))
        ),
    )
    cg.release("log-abc")
    assert seen == ["1"] and not d.exists()
    cg.release("log-abc")  # already gone: nothing to do


def test_release_without_cgroup_kill_signals_each_process(tmp_path):
    cg = Tree(tmp_path).setup()
    d = cg.create("log-abc")
    left = subprocess.Popen(["sleep", "30"])
    (d / "cgroup.procs").write_text(f"{left.pid}\n")
    cg.release("log-abc")
    assert left.wait(5) == -signal.SIGKILL and not d.exists()


def test_release_without_cgroup_kill_signals_as_the_member(tmp_path, monkeypatch):
    from aegis import launch

    cg = Tree(tmp_path).setup()
    d = cg.create("log-abc")
    (d / "cgroup.procs").write_text("4321\n")
    seen = []
    monkeypatch.setattr(
        launch,
        "signal_process",
        lambda pid, sig, *, user, group=False: seen.append((pid, sig, user)),
    )
    monkeypatch.setattr(
        SessionCgroups, "remove", staticmethod(lambda p: shutil.rmtree(p))
    )

    cg.release("log-abc", user="ben")

    assert seen == [(4321, signal.SIGKILL, "ben")]
    assert not d.exists()


async def test_a_busy_cgroup_is_removed_on_a_later_try(tmp_path, monkeypatch):
    monkeypatch.setattr(cgroups, "RETRY_RMDIR_S", 0.01)
    cg = Tree(tmp_path).setup()
    d = cg.create("log-abc")
    tries = []

    def busy_once(p):
        tries.append(p)
        if len(tries) == 1:
            raise OSError(16, "Device or resource busy")
        shutil.rmtree(p)

    monkeypatch.setattr(SessionCgroups, "remove", staticmethod(busy_once))
    cg.release("log-abc")
    await until(lambda: not d.exists(), timeout=2, what="the retried rmdir")
    assert len(tries) == 2


def test_the_child_joins_before_it_execs_and_keeps_its_pid(tmp_path):
    d = tmp_path / "cg"
    d.mkdir()
    argv = wrap(d, [sys.executable, "-c", "import os; print(os.getpid())"])
    out = subprocess.run(argv, capture_output=True, text=True, check=True)
    assert (d / "cgroup.procs").read_text().strip() == out.stdout.strip()


def test_a_failed_join_still_starts_the_harness_and_says_so(tmp_path):
    argv = wrap(tmp_path / "missing", [sys.executable, "-c", "print('ran')"])
    out = subprocess.run(argv, capture_output=True, text=True, check=True)
    assert out.stdout == "ran\n" and "without its memory cap" in out.stderr


def test_without_a_cgroup_the_argv_is_untouched_and_a_missing_binary_still_raises(
    tmp_path,
):
    assert wrap(None, ["claude", "-p"]) == ["claude", "-p"]
    with pytest.raises(FileNotFoundError):
        wrap(tmp_path, [str(tmp_path / "no-such-claude")])


def launch(tmp_path: Path, cgroup: Path) -> Launch:
    return Launch(
        cwd=tmp_path,
        model="fake",
        effort="high",
        permission="full",
        resume_id=None,
        mcp=None,
        system_prompt=None,
        stderr_path=tmp_path / "stderr.log",
        on_line=lambda line: None,
        on_exit=lambda code, tail: None,
        cgroup=cgroup,
    )


@pytest.mark.parametrize("which", ["claude", "opencode", "codex"])
async def test_every_harness_starts_its_child_in_the_cgroup(
    tmp_path, which, fake_claude, fake_opencode, fake_codex
):
    d = tmp_path / "cg"
    d.mkdir()
    lc = launch(tmp_path, d)
    p = {
        "claude": lambda: ClaudeCode(fake_claude).process(lc),
        "opencode": lambda: OpenCodeProcess(fake_opencode, lc),
        "codex": lambda: CodexProcess(fake_codex, lc),
    }[which]()
    await p.start()
    try:
        procs = d / "cgroup.procs"
        await until(procs.exists, timeout=5, what="the child joined")
        assert procs.read_text().strip() == str(p.pid)
    finally:
        await p.terminate()


class CgHost(Host):
    def __init__(self, d: Path) -> None:
        self.d, self.released_n = d, 0

    def cgroup(self, session):
        return self.d

    def released(self, session):
        self.released_n += 1


async def test_a_session_asks_for_a_cgroup_and_releases_it_on_stop_and_exit(
    tmp_path, fake_claude
):
    d = tmp_path / "cg"
    d.mkdir()
    host = CgHost(d)
    h = Harness(tmp_path, fake_claude, host=host)
    await h.session.start()
    procs = d / "cgroup.procs"
    await until(procs.exists, timeout=5, what="the child joined")
    assert procs.read_text().strip() == str(h.session.pid)
    await h.session.stop()
    assert host.released_n == 1
    await h.session.send("/exit 3")
    await until(lambda: h.session.status == "asleep", timeout=5, what="stopped")
    assert host.released_n == 2


# -- the real thing: needs a unit with Delegate=yes -------------------------

DELEGATED = SessionCgroups.check()


@pytest.mark.skipif(
    not isinstance(DELEGATED, Path), reason=f"no delegated cgroup: {DELEGATED}"
)
async def test_a_real_session_cgroup_caps_its_child(tmp_path, fake_claude, monkeypatch):
    """Run under a unit with Delegate=yes (the PR body has the command). It
    moves this pytest into <unit>/server, as the server would move itself."""
    # A real cgroup is removed by rmdir; the autouse fake's rmtree gets EPERM.
    monkeypatch.setattr(SessionCgroups, "remove", staticmethod(os.rmdir))
    cg = SessionCgroups.setup(lambda: "64M")
    assert cg is not None
    me = Path("/proc/self/cgroup").read_text()
    assert me.strip().endswith("/server")
    d = cg.create("log-real")
    assert (d / "memory.max").read_text().strip() == str(64 * 1024**2)
    assert (d / "memory.oom.group").read_text().strip() == "0"
    assert (d / "memory.swap.max").read_text().strip() == "0"

    p = ClaudeCode(fake_claude).process(launch(tmp_path, d))
    await p.start()
    try:
        # The wrapper joins before it execs, which can be after start returns.
        cgroup_file = Path(f"/proc/{p.pid}/cgroup")
        await until(
            lambda: cgroup_file.read_text().strip().endswith("/sessions/log-real"),
            timeout=3,
            what="the harness in its session cgroup",
        )
        # A runaway in the session is killed by a memcg OOM; this process,
        # the "server", and the harness itself live on.
        hog = await asyncio.create_subprocess_exec(
            *wrap(
                d,
                [
                    sys.executable,
                    "-c",
                    "b = []\nwhile True: b.append(bytearray(1 << 20))",
                ],
            )
        )
        assert await asyncio.wait_for(hog.wait(), 30) == -signal.SIGKILL
        events = dict(
            line.split() for line in (d / "memory.events").read_text().splitlines()
        )
        assert int(events["oom_kill"]) >= 1
        assert p.running
    finally:
        await p.terminate()
    cg.release("log-real")
    await until(lambda: not d.exists(), timeout=3, what="the session cgroup removed")


def test_all_the_work_lives_in_one_tree_beside_the_server(tmp_path):
    # #782: terminals and browsers count against the same cap as sessions.
    t = Tree(tmp_path)
    cg = t.setup()
    term = cg.sibling("terminals", lambda: "max")
    assert cg.work == t.unit / "work"
    assert cg.sessions.parent == term.sessions.parent == cg.work
    assert (cg.work / "cgroup.subtree_control").read_text() == "+memory +pids"


def test_the_work_leaves_the_server_tasks_and_each_session_has_a_ceiling(tmp_path):
    # A fork bomb in one session would use up the unit's TasksMax, and the
    # server could not start a thread.
    t = Tree(tmp_path)
    (t.unit / "pids.max").write_text("9255")
    cg = t.setup()
    assert (cg.work / "pids.max").read_text() == str(9255 - cgroups.SERVER_PIDS_RESERVE)
    d = cg.create("a")
    assert (d / "pids.max").read_text() == str(cgroups.SESSION_PIDS_MAX)


def test_the_servers_disk_requests_go_first(tmp_path):
    t = Tree(tmp_path)
    t.setup()
    assert (t.unit / "server" / "io.prio.class").read_text() == "promote-to-rt"


def test_without_the_io_controller_the_server_has_no_io_class(tmp_path):
    t = Tree(tmp_path)
    (t.unit / "cgroup.controllers").write_text("cpu memory pids\n")
    t.setup()
    assert not (t.unit / "server" / "io.prio.class").exists()


def test_chromium_joins_the_browsers_cgroup_before_it_runs(tmp_path):
    from aegis.browsers import Browsers

    cg = tmp_path / "work" / "browsers" / "chromium"
    cg.mkdir(parents=True)
    fake = tmp_path / "chrome"
    fake.write_text('#!/bin/sh\necho "$$ $*"\n')
    fake.chmod(0o755)
    b = Browsers(tmp_path / "state", lambda *a: None, lambda c: 0, None)
    assert b._launcher() is None  # no cgroup: Playwright's own Chromium
    b.cgroup = cg

    class Pw:
        class chromium:
            executable_path = str(fake)

    b._pw = Pw
    out = subprocess.run(
        [b._launcher(), "--headless", "x y"], capture_output=True, text=True
    )
    pid, args = out.stdout.split(" ", 1)
    # The shell execs Chromium, so the pid it wrote is Chromium's own.
    assert (cg / "cgroup.procs").read_text().strip() == pid
    assert args.strip() == "--headless x y"
