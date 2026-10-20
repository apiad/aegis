"""The ETA and the check verdicts a monitor's card carries (#174)."""

from dataclasses import asdict

import pytest

from aegis.monitors import Monitor, classify, eta, verdict


def test_no_reading_or_no_move_has_no_eta():
    assert eta(0.0, []) is None
    assert eta(0.0, [[0.0, 0]]) is None
    assert eta(0.0, [[0.0, 40]]) is None, "a start above 0 is not progress yet"


def test_one_move_is_a_straight_line_from_the_start():
    at, basis = eta(100.0, [[110.0, 0], [200.0, 25]])
    assert at == pytest.approx(200.0 + 75 * (100 / 25))
    assert basis == "25 points in 1m 40s, since the start"


def test_later_moves_use_the_rate_since_progress_first_moved():
    """A CI wait sits at 0 while runners set up; that wait is not the rate."""
    start = 1000.0
    readings = [[start + 30, 0], [start + 240, 33], [start + 450, 66]]
    at, basis = eta(start, readings)
    assert at == pytest.approx(start + 450 + 34 * (210 / 33))
    assert basis == "33 points in 3m 30s, since progress first moved"


def test_progress_going_backwards_has_no_eta():
    assert eta(0.0, [[0.0, 0], [10.0, 50], [20.0, 40]]) is None


def test_a_finished_reading_is_due_now():
    at, _ = eta(0.0, [[0.0, 0], [10.0, 50], [20.0, 100]])
    assert at == 20.0


def test_readings_keep_the_first_move_when_they_are_trimmed():
    m = Monitor(id="m", owner="o", description="d", done="false", cwd="/")
    for i in range(300):
        m.read(float(i), i % 100)
    assert len(m.readings) <= 100
    assert m.readings[:2] == [[0.0, 0], [1.0, 1]]
    assert m.readings[-1] == [299.0, 99]


@pytest.mark.parametrize(
    ("kind", "rc", "out", "said", "bad"),
    [
        ("done", 1, "", "not yet", False),
        ("done", 0, "", "passed", False),
        ("fail", 1, "", "not failing", False),
        ("fail", 0, "", "failed", False),
        ("progress", 0, "66", "printed 66", False),
        ("progress", 0, "about half", "printed no number", False),
        ("progress", 1, "", "exit 1, no reading", False),
        ("done", 127, "", "command not found", True),
        ("done", 126, "", "cannot execute", True),
        ("done", None, "", "took over 30s", True),
    ],
)
def test_each_result_reads_as_a_verdict(kind, rc, out, said, bad):
    assert verdict(kind, rc, out, "took over 30s" if rc is None else "") == (said, bad)


def test_a_missing_command_inside_a_pipeline_is_still_broken():
    """`twinctl status | jq -e .done` exits with jq's code, not 127."""
    err = "bash: line 1: twinctl: command not found\n"
    assert verdict("done", 4, "", err) == ("command not found", True)
    assert verdict("done", 1, "", "grep: x.log: No such file or directory") == (
        "not yet",
        False,
    )


@pytest.mark.parametrize(
    ("attention", "state"),
    [
        ("done", "finished"),
        ("review", "finished"),
        ("closed", "finished"),
        ("needs_you", "blocked"),
        ("error", "blocked"),
        ("working", "running"),
        ("waiting", "running"),
    ],
)
def test_each_attention_classifies_for_a_session_wait(attention, state):
    assert classify(attention) == state


def test_a_session_monitors_card_lists_its_sessions_and_no_checks():
    row = {
        "handle": "ada-lovelace",
        "attention": "working",
        "state": "running",
        "line": "",
    }
    m = Monitor(
        id="m",
        owner="o",
        description="d",
        done="",
        cwd="/",
        sessions=[{"log_id": "x", **row}],
    )
    c = m.card()
    assert c["checks"] == [] and c["sessions"] == [row] and not c["broken"]
    bash = Monitor(id="m", owner="o", description="d", done="false", cwd="/")
    assert bash.card()["sessions"] is None


def test_a_session_monitor_round_trips_through_its_saved_form():
    m = Monitor(
        id="m",
        owner="o",
        description="d",
        done="",
        cwd="/",
        sessions=[{"log_id": "x", "handle": "ada-lovelace"}],
    )
    assert Monitor(**asdict(m)) == m


def test_a_session_monitor_has_no_eta():
    """Sessions of different sizes give finished-over-listed no rate (review M2)."""
    m = Monitor(
        id="m",
        owner="o",
        description="d",
        done="",
        cwd="/",
        started_at=0.0,
        sessions=[{"log_id": "x", "handle": "ada-lovelace"}],
        readings=[[0.0, 0], [10.0, 50]],
    )
    assert m.card()["eta_at"] is None and m.card()["eta_basis"] is None


# -- the monitors notice source (notices.py, per-session items) ---------------------
from aegis.monitors import monitor_items  # noqa: E402

CFG = {"stall_min": 15.0, "broken_checks": 3, "overdue_min": 10.0, "wake": True}
IDS = {"m2", "m5"}  # checked at least once by this process
NOW = 100_000.0


def mon(
    id="m5", desc="tests", progress="cat p", interval=10.0, started=NOW - 3600, **kw
):
    return Monitor(
        id=id, owner="a", description=desc, done="false", cwd="/",
        progress=progress, interval_s=interval, started_at=started, **kw,
    )  # fmt: skip


def by_key(items):
    return {i.key: i for i in items}


def test_live_monitors_make_one_info_line_each():
    a = mon("m2", "build", readings=[[NOW - 600, 10], [NOW - 60, 60]], last_progress=60)
    b = mon("m5", "tests", readings=[[NOW - 30, 40]], last_progress=40)
    live = [i for i in monitor_items([a, b], CFG, NOW, IDS) if i.level == "info"]
    assert [i.key for i in live] == ["m2:live", "m5:live"]
    assert live[0].text.startswith("monitor m2 build 60% eta ")
    assert live[1].text == "monitor m5 tests 40%"


def test_a_progress_unchanged_for_stall_min_is_stalled():
    m = mon(readings=[[NOW - 901, 40]], last_progress=40)
    st = by_key(monitor_items([m], CFG, NOW, IDS))["m5:stalled"]
    assert (st.level, st.wake) == ("warn", True)
    assert (
        st.text == 'monitor m5 "tests" at 40% for 15 min · look, or monitor_cancel it'
    )
    assert st.clear == "monitor m5 moves again, or has ended"


def test_stall_needs_three_intervals_too():
    m = mon(interval=600.0, readings=[[NOW - 901, 40]], last_progress=40)
    assert "m5:stalled" not in by_key(monitor_items([m], CFG, NOW, IDS))


def test_a_monitor_without_progress_is_never_stalled():
    m = mon(progress=None, started=NOW - 99_999)
    assert "m5:stalled" not in by_key(monitor_items([m], CFG, NOW, IDS))


def test_no_reading_yet_counts_from_the_start():
    assert "m5:stalled" in by_key(
        monitor_items([mon(started=NOW - 901)], CFG, NOW, IDS)
    )
    assert "m5:stalled" not in by_key(
        monitor_items([mon(started=NOW - 60)], CFG, NOW, IDS)
    )


def test_a_check_bad_for_broken_checks_intervals_is_broken():
    bad = {"rc": 127, "verdict": "command not found", "bad": True, "out": "", "err": ""}
    m = mon(checks={"progress": bad | {"since": NOW - 31}}, readings=[[NOW - 5, 1]])
    br = by_key(monitor_items([m], CFG, NOW, IDS))["m5:broken"]
    assert br.text == 'monitor m5 "tests" progress check cannot run: command not found'
    assert br.wake and br.level == "warn"
    m = mon(checks={"progress": bad | {"since": NOW - 29}}, readings=[[NOW - 5, 1]])
    assert "m5:broken" not in by_key(monitor_items([m], CFG, NOW, IDS))


def test_past_its_eta_by_half_and_by_overdue_min_is_overdue():
    # 0 -> 50 in 1000 s: eta at started + 2000; overdue once now - eta >= max(1000, 600)
    started = NOW - 3100
    m = mon(
        started=started, readings=[[started, 0], [started + 1000, 50]], last_progress=50
    )
    ov = by_key(monitor_items([m], {**CFG, "stall_min": 999.0}, NOW, IDS))["m5:overdue"]
    assert ov.text == 'monitor m5 "tests" past its eta by 18 min at 50%'
    m.started_at, m.readings = NOW - 2900, [[NOW - 2900, 0], [NOW - 1900, 50]]
    assert "m5:overdue" not in by_key(
        monitor_items([m], {**CFG, "stall_min": 999.0}, NOW, IDS)
    )


def test_a_session_watcher_gets_only_the_info_line():
    m = mon(
        progress=None, started=NOW - 99_999, sessions=[{"log_id": "b", "handle": "b"}]
    )
    assert [i.level for i in monitor_items([m], CFG, NOW, IDS)] == ["info"]


def test_wake_false_in_config_sets_items_without_wake():
    m = mon(readings=[[NOW - 901, 40]], last_progress=40)
    st = by_key(monitor_items([m], {**CFG, "wake": False}, NOW, IDS))["m5:stalled"]
    assert st.wake is False


def test_no_monitors_no_items():
    assert monitor_items([], CFG, NOW, IDS) == []


async def _stall_world(tmp_path, fake_claude, stall_min):
    from .test_agents import CONFIG, World

    (tmp_path / ".aegis.yaml").write_text(
        CONFIG + f"notices:\n  monitors: {{stall_min: {stall_min}}}\n"
    )
    return await World(tmp_path, fake_claude).start()


def _stall_wakes(s) -> list[dict]:
    from .test_agents import inbox

    return [e for e in inbox(s) if ":stalled · warn" in e["title"]]


async def test_a_stalled_monitor_wakes_its_idle_owner_once(tmp_path, fake_claude):
    import asyncio

    from .conftest import until
    from .test_agents import turn

    w = await _stall_world(tmp_path, fake_claude, 0.005)  # 0.3 s; 3 intervals 0.6 s
    try:
        a = await w.spawn()
        await turn(a, "arm it")
        mons = w.app.sessions.monitors
        m = mons.start(a.log_id, str(tmp_path), description="tests", done="false",
                       progress="echo 40", interval_s=0.2)  # fmt: skip
        await until(lambda: _stall_wakes(a), timeout=5, what="the stall wake")
        assert f'monitor {m.id} "tests" at 40%' in _stall_wakes(a)[0]["md"]
        await until(lambda: a.status == "idle", timeout=5, what="the woken turn")
        await asyncio.sleep(0.6)
        assert len(_stall_wakes(a)) == 1 and m.id in mons.items  # still live
    finally:
        await w.stop()


async def test_a_lowered_stall_min_applies_in_a_running_server(tmp_path, fake_claude):
    from .conftest import until
    from .test_agents import CONFIG, turn

    w = await _stall_world(tmp_path, fake_claude, 60)
    try:
        a = await w.spawn()
        await turn(a, "arm it")
        w.app.sessions.monitors.start(a.log_id, str(tmp_path), description="tests",
                                      done="false", progress="echo 40", interval_s=0.2)  # fmt: skip
        (tmp_path / ".aegis.yaml").write_text(
            CONFIG + "notices:\n  monitors: {stall_min: 0.005}\n"
        )
        await until(lambda: _stall_wakes(a), timeout=5, what="the wake after the edit")
    finally:
        await w.stop()


def test_no_warning_before_the_monitors_first_check_in_this_process():
    m = mon(
        readings=[[NOW - 9_999, 40]], last_progress=40
    )  # stale only across a restart
    assert [i.level for i in monitor_items([m], CFG, NOW, checked=set())] == ["info"]
    assert "m5:stalled" in by_key(monitor_items([m], CFG, NOW, checked={"m5"}))


def test_each_live_monitor_is_its_own_status_line():
    ms = [
        mon(f"m{k}", f"job {k}", readings=[[NOW - 5, 10]], last_progress=10)
        for k in range(4)
    ]
    info = [i for i in monitor_items(ms, CFG, NOW, checked=set()) if i.level == "info"]
    assert [i.key for i in info] == ["m0:live", "m1:live", "m2:live", "m3:live"]
    assert info[0].text == "monitor m0 job 0 10%"
