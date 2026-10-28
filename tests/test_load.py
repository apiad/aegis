"""Each session's CPU and RAM (load.py, #762), over a fake /proc."""

from __future__ import annotations

from pathlib import Path

from aegis import load
from aegis.host import GB
from aegis.load import LoadSampler, Proc, attribute, env_session, parse_stat

import pytest

from .test_agents import CONFIG, World, mcp, turn
from .test_artifacts_e2e import ok


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


TCK = load.CLK_TCK
PAGE = load.PAGE


class FakeProc:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, pid, ppid, ticks=0, rss_pages=0, start=100, env=None, cticks=0):
        d = self.root / str(pid)
        d.mkdir(parents=True, exist_ok=True)
        # pid (comm) state ppid ... utime(14) stime(15) cutime(16) cstime(17) ... starttime(22) vsize rss(24)
        fields = ["S", str(ppid)] + ["0"] * 9 + [str(ticks), "0", str(cticks), "0"]
        fields += ["0"] * 4 + [str(start), "0", str(rss_pages)]
        (d / "stat").write_text(f"{pid} (a (b) c) " + " ".join(fields) + "\n")
        (d / "environ").write_bytes(
            b"PATH=/bin\0" + (f"AEGIS_SESSION={env}".encode() if env else b"") + b"\0"
        )

    def drop(self, pid):
        d = self.root / str(pid)
        for f in d.iterdir():
            f.unlink()
        d.rmdir()


class Clock:
    t = 0.0

    def __call__(self) -> float:
        return self.t


def sampler(tmp_path, own=999):
    clock = Clock()
    s = LoadSampler(lambda: {}, lambda _: None, proc=tmp_path, clock=clock, own_pid=own)
    return s, clock


def test_parse_stat_reads_past_a_command_with_parentheses():
    p = parse_stat(
        7, "7 (a (b) c) S 3 " + " ".join(["0"] * 9) + " 5 6 7 8 0 0 0 0 42 0 10"
    )
    assert p == Proc(pid=7, ppid=3, start=42, ticks=26, rss=10 * PAGE)


def test_env_session_finds_the_variable():
    assert env_session(b"A=1\0AEGIS_SESSION=s1\0B=2\0") == "s1"
    assert env_session(b"A=1\0") is None


def test_attribute_by_ancestor_then_by_environment():
    procs = {
        10: Proc(10, 1, 0, 0, 0),  # s1's harness
        11: Proc(11, 10, 0, 0, 0),  # its shell
        12: Proc(12, 11, 0, 0, 0),  # a test run under it
        20: Proc(20, 1, 0, 0, 0),  # detached, carries s1
        21: Proc(21, 1, 0, 0, 0),  # a test server's fake session
        30: Proc(30, 999, 0, 0, 0),  # the server's own child
        40: Proc(40, 1, 0, 0, 0),  # nobody's
    }
    env = {20: "s1", 21: "fake", 30: None, 40: None}
    owner = attribute(
        procs, {10: "s1"}, lambda p: env.get(p.pid), {"s1", "s2"}, skip=999
    )
    assert owner == {10: "s1", 11: "s1", 12: "s1", 20: "s1"}


def test_a_nested_server_under_a_session_counts_for_that_session():
    procs = {
        10: Proc(10, 1, 0, 0, 0),  # s1's harness
        11: Proc(11, 10, 0, 0, 0),  # an aegis serve it started
        12: Proc(12, 11, 0, 0, 0),  # that server's fake harness, naming its own session
    }
    owner = attribute(procs, {10: "s1"}, lambda p: "fake", {"s1"}, skip=999)
    assert owner == {10: "s1", 11: "s1", 12: "s1"}


def test_cores_over_the_window_and_rss(tmp_path):
    fp = FakeProc(tmp_path)
    fp.put(10, 1, ticks=0, rss_pages=1000)
    fp.put(11, 10, ticks=0, rss_pages=2000)
    s, clock = sampler(tmp_path)
    live = {"s1": (10, None)}
    assert s.sample(live) == ["s1"]
    assert s.of("s1") == {"cpu": 0.0, "rss_gb": round(3000 * PAGE / GB, 1)}
    clock.t = 10
    fp.put(10, 1, ticks=5 * TCK, rss_pages=1000)
    fp.put(11, 10, ticks=15 * TCK, rss_pages=2000)
    s.sample(live)
    assert s.cores("s1") == 2.0  # 20 s of CPU in 10 s


def test_a_new_process_counts_its_time_but_the_first_sample_counts_none(tmp_path):
    fp = FakeProc(tmp_path)
    fp.put(10, 1, ticks=100 * TCK)  # a long history before the server looked
    s, clock = sampler(tmp_path)
    live = {"s1": (10, None)}
    s.sample(live)
    clock.t = 10
    fp.put(12, 10, ticks=10 * TCK, start=500)  # born since
    s.sample(live)
    assert s.cores("s1") == 1.0


def test_a_reaped_child_is_not_counted_twice(tmp_path):
    fp = FakeProc(tmp_path)
    fp.put(10, 1)
    fp.put(11, 10, ticks=4 * TCK, start=200)
    s, clock = sampler(tmp_path)
    live = {"s1": (10, None)}
    s.sample(live)
    clock.t = 10
    # The child ran 6 s more, ended, and the harness waited for it: its whole
    # 10 s moves to the harness's cutime.
    fp.drop(11)
    fp.put(10, 1, cticks=10 * TCK)
    s.sample(live)
    assert s.cores("s1") == 0.6


def test_a_detached_run_counts_by_its_environment(tmp_path):
    fp = FakeProc(tmp_path)
    fp.put(10, 1)
    fp.put(50, 1, ticks=0, env="s1", start=300)
    fp.put(51, 1, ticks=0, env="someone-else", start=300)
    s, clock = sampler(tmp_path)
    live = {"s1": (10, None)}
    s.sample(live)
    clock.t = 5
    fp.put(50, 1, ticks=5 * TCK, env="s1", start=300)
    fp.put(51, 1, ticks=50 * TCK, env="someone-else", start=300)
    s.sample(live)
    assert s.cores("s1") == 1.0


def test_a_session_cgroup_is_read_instead_of_the_tree(tmp_path):
    cg = tmp_path / "cg"
    cg.mkdir()
    (cg / "cpu.stat").write_text("usage_usec 1000000\nuser_usec 1\n")
    (cg / "memory.current").write_text(str(2 * GB))
    s, clock = sampler(tmp_path / "proc")
    live = {"s1": (10, cg)}
    s.sample(live)
    clock.t = 4
    (cg / "cpu.stat").write_text("usage_usec 9000000\n")
    s.sample(live)
    assert s.of("s1") == {"cpu": 2.0, "rss_gb": 2.0}


def test_a_session_that_stops_loses_its_reading(tmp_path):
    fp = FakeProc(tmp_path)
    fp.put(10, 1, rss_pages=10)
    s, _ = sampler(tmp_path)
    s.sample({"s1": (10, None)})
    assert s.of("s1") is not None
    assert s.sample({"s1": (None, None)}) == ["s1"]
    assert s.of("s1") is None


def test_an_unchanged_reading_is_not_reported_again(tmp_path):
    fp = FakeProc(tmp_path)
    fp.put(10, 1, rss_pages=10)
    s, clock = sampler(tmp_path)
    live = {"s1": (10, None)}
    assert s.sample(live) == ["s1"]
    clock.t = 5
    assert s.sample(live) == []


async def test_session_list_gives_each_session_its_load(world):
    a = await world.spawn()
    app = world.app
    await app.load.stop()  # sampled here, not by the loop's thread
    live = {
        s.log_id: (s.pid, app.sessions.cgroup_dir(s))
        for s in app.sessions.open_sessions()
    }
    for s in app.load.sample(live):
        app.sessions.load_changed(s)
    listed = ok(await turn(a, mcp("session_list")))
    (me,) = [r for r in listed if r["you"]]
    assert me["load"] is not None and me["load"]["cpu"] == 0.0
    assert me["load"]["rss_gb"] >= 0
    assert a.wire()["load"] == me["load"]
