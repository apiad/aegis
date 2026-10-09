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
