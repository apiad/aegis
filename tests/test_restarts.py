import signal
import subprocess
import sys

import pytest

from aegis import restarts
from aegis.restarts import Previous, verdict


def test_only_a_finished_stop_reads_back_as_clean(tmp_path):
    # The first boot after an upgrade finds no record: a crash.
    assert restarts.started(tmp_path) == Previous(False)
    restarts.stopping(tmp_path)
    # Died during its own shutdown: a crash.
    assert restarts.started(tmp_path) == Previous(False)
    t = restarts.stopping(tmp_path)
    restarts.stopped(tmp_path, t)
    assert restarts.started(tmp_path) == Previous(True, t)
    # Started and never stopped: a crash.
    assert restarts.started(tmp_path) == Previous(False)


def test_a_damaged_record_is_a_crash(tmp_path):
    (tmp_path / restarts.RECORD).write_text("{not json")
    assert restarts.started(tmp_path) == Previous(False)


NOW = 1000.0
CLEAN = Previous(True, NOW)
CRASH = Previous(False)


@pytest.mark.parametrize(
    "meta,prev,want",
    [
        ({"last_status": "working"}, CLEAN, (True, False)),
        ({"last_status": "working", "restart_continues": 1}, CLEAN, (True, False)),
        ({"last_status": "working", "restart_continues": 2}, CLEAN, (False, False)),
        ({"last_status": "working"}, CRASH, (True, True)),
        ({"last_status": "working", "restart_continues": 1}, CRASH, (False, True)),
        (
            {
                "last_status": "stopped",
                "last_exit": {"code": 143, "at": NOW - 1, "status": "working"},
            },
            CLEAN,
            (True, False),
        ),
        (
            {
                "last_status": "stopped",
                "last_exit": {"code": -15, "at": NOW + 2, "status": "working"},
            },
            CLEAN,
            (True, False),
        ),
        (
            {"last_status": "stopped", "last_exit": {"code": 143, "at": NOW - 31}},
            CLEAN,
            None,
        ),
        ({"last_status": "stopped", "last_exit": {"code": 1, "at": NOW}}, CLEAN, None),
        ({"last_status": "stopped", "last_exit": {"code": -9, "at": NOW}}, CLEAN, None),
        (
            {"last_status": "stopped", "last_exit": {"code": 143, "at": NOW}},
            CRASH,
            None,
        ),
        ({"last_status": "idle"}, CLEAN, None),
        # An idle harness killed by the unit's SIGTERM had no turn to cut.
        (
            {
                "last_status": "stopped",
                "last_exit": {"code": 143, "at": NOW, "status": "idle"},
            },
            CLEAN,
            None,
        ),
        # A meta from before the exit's status was kept: not a cut turn.
        (
            {"last_status": "stopped", "last_exit": {"code": 143, "at": NOW}},
            CLEAN,
            None,
        ),
        ({}, CRASH, None),
    ],
)
def test_the_verdict(meta, prev, want):
    v = verdict(meta, prev)
    assert (None if v is None else (v.go, v.crash)) == want


def test_a_leftover_harness_is_ended_and_a_reused_pid_is_not():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        st = restarts.started_at(p.pid)
        assert st is not None
        # Same pid, another start time: another process, never signalled.
        assert restarts.end_leftover(p.pid, st + 1) is False
        assert p.poll() is None
        assert restarts.end_leftover(p.pid, st) is True
        assert p.wait(timeout=5) == -signal.SIGTERM
    finally:
        if p.poll() is None:
            p.kill()


def test_a_leftover_that_ignores_sigterm_is_killed():
    code = (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(60)"
    )
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
    try:
        p.stdout.readline()
        st = restarts.started_at(p.pid)
        assert restarts.end_leftover(p.pid, st, wait_s=0.3) is True
        assert p.wait(timeout=5) == -signal.SIGKILL
    finally:
        if p.poll() is None:
            p.kill()


def test_no_pid_or_no_process_ends_nothing():
    assert restarts.end_leftover(None, None) is False
    assert restarts.end_leftover(2**22 + 7, 1) is False


def test_the_headers_name_aegis_as_the_sender():
    assert restarts.header(False).startswith("> from aegis:restart · ")
    assert restarts.header(True).startswith("> from aegis:crash · ")


def test_end_group_spares_a_reused_pid_and_ends_a_live_group():
    r = restarts
    p = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        start = r.started_at(p.pid)
        assert start is not None
        assert r.end_group(p.pid, start + 1) is False  # another process's start
        assert r.started_at(p.pid) == start
        assert r.end_group(p.pid, start, wait_s=1) is True
        p.wait(timeout=5)
    finally:
        p.kill()


def test_end_group_signals_as_the_recorded_member(monkeypatch):
    seen = []
    monkeypatch.setattr(restarts, "started_at", lambda pid: 123)
    monkeypatch.setattr(restarts, "group_alive", lambda pgid: True)
    monkeypatch.setattr(
        restarts.launch,
        "signal_process",
        lambda pid, sig, *, user, group: seen.append((pid, sig, user, group)),
    )

    assert restarts.end_group(4321, 123, wait_s=0, user="ben")

    assert seen == [
        (4321, signal.SIGTERM, "ben", True),
        (4321, signal.SIGKILL, "ben", True),
    ]
