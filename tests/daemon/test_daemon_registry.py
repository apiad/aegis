"""Which daemons are running, across roots."""
import os
import time

import pytest

from aegis.daemon import registry as reg


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))


def _rec(root, pid=None, socket=None):
    return reg.DaemonRecord(
        root=root, pid=pid if pid is not None else os.getpid(),
        socket=socket or (root / ".aegis" / "state" / "daemon.sock"),
        started=time.time(), version="test")


def _a_dead_pid() -> int:
    """A pid that has certainly exited: fork a child and reap it."""
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    return pid


def test_a_recorded_daemon_comes_back(tmp_path):
    reg.record(_rec(tmp_path))
    found = reg.daemon_for(tmp_path)
    assert found is not None and found.root == tmp_path.resolve()


def test_a_dead_pid_is_not_live_and_is_pruned(tmp_path):
    """A record file outlives SIGKILL; a pid does not. The pid is the
    truth, and a stale file must not make `aegis` refuse to autostart."""
    reg.record(_rec(tmp_path, pid=_a_dead_pid()))
    assert reg.daemon_for(tmp_path) is None
    assert reg.live_daemons() == []
    assert list((tmp_path / "daemons").glob("*.json")) == []


def test_two_roots_are_two_daemons(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    reg.record(_rec(a))
    reg.record(_rec(b))
    assert {d.root for d in reg.live_daemons()} == {a.resolve(), b.resolve()}


def test_recording_the_same_root_twice_replaces_it(tmp_path):
    reg.record(_rec(tmp_path))
    reg.record(_rec(tmp_path))
    assert len(reg.live_daemons()) == 1


def test_forget_removes_the_record(tmp_path):
    reg.record(_rec(tmp_path))
    reg.forget(tmp_path)
    assert reg.daemon_for(tmp_path) is None


def test_forget_of_an_unknown_root_is_a_no_op(tmp_path):
    """The exit path runs from a finally block that may never have
    recorded — a daemon that died during boot."""
    reg.forget(tmp_path)


def test_a_damaged_record_is_ignored_not_raised(tmp_path):
    """One corrupt file must not make `aegis ls` unusable for every root."""
    reg.record(_rec(tmp_path))
    d = tmp_path / "daemons"
    for f in d.glob("*.json"):
        f.write_text("{{{", encoding="utf-8")
    assert reg.live_daemons() == []


def test_a_root_whose_path_holds_a_separator_still_names_one_file(tmp_path):
    """The record is keyed by a hash, not by the path — a project at
    /home/a/b must not try to write ~/.aegis/daemons/home/a/b.json."""
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    reg.record(_rec(deep))
    files = list((tmp_path / "daemons").glob("*.json"))
    assert len(files) == 1
    assert reg.daemon_for(deep).root == deep.resolve()


def test_kill_of_an_already_dead_daemon_reports_false_and_forgets(tmp_path):
    reg.record(_rec(tmp_path, pid=_a_dead_pid()))
    rec = reg.DaemonRecord(root=tmp_path, pid=_a_dead_pid(),
                           socket=tmp_path / "d.sock", started=time.time())
    assert reg.kill(rec) is False
    assert reg.daemon_for(tmp_path) is None
