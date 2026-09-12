"""A daemon keeps the code it booted with, and nothing said so.

`ensure_daemon` returns any daemon that answers its socket. The registry
records a `version` and nobody reads it. The reaper needs zero views and
zero sessions for thirty minutes, so a daemon holding one tab never exits
on its own. Put together: once a daemon is up, no edit to the source
reaches the user until somebody runs `aegis kill`, and nothing tells them
to.

That cost Alex a morning. Three fixes landed, he restarted his terminal
each time, and the daemon from 08:58 kept serving the code from 08:58.

Comparing the recorded version would not have caught it: both sides said
0.37.0, because a dev edit does not bump a release number. What does catch
it is the source being newer than the process, which is exact for an
editable install and silent for a wheel, where nothing under the package
ever changes.

Killing is the part that needs care. A daemon with tabs, terminals or open
files has work to lose, so it is reported rather than replaced. A daemon
with none is free to replace, and replacing it silently is the whole point.
"""
from __future__ import annotations

import json
import time

from aegis.config.roots import AegisRoots
from aegis.daemon import lifecycle
from aegis.daemon.registry import DaemonRecord


def _record(tmp_path, *, started: float) -> DaemonRecord:
    return DaemonRecord(root=tmp_path, pid=1, started=started,
                        socket=tmp_path / "d.sock", version="0.37.0")


def test_a_daemon_older_than_the_source_is_stale():
    """The case the version field misses: same version, newer code."""
    rec = DaemonRecord(root=__file__, pid=1, started=0.0,
                       socket=__file__, version="0.37.0")
    assert lifecycle.is_stale(rec), (
        "a daemon started at the epoch is running code edited since")


def test_a_daemon_newer_than_the_source_is_not_stale(tmp_path):
    rec = _record(tmp_path, started=time.time() + 3600)
    assert not lifecycle.is_stale(rec)


def test_an_empty_workspace_has_nothing_to_lose(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    roots.state_dir.mkdir(parents=True)
    assert lifecycle.nothing_to_lose(roots), "no snapshot at all"

    (roots.state_dir / "workspace.json").write_text(json.dumps(
        {"version": 1, "tabs": [], "terminals": [], "files": []}))
    assert lifecycle.nothing_to_lose(roots), "an empty snapshot"


def test_a_workspace_with_anything_open_is_not_free_to_replace(tmp_path):
    """Tabs, terminals and open files each count. Erring toward keeping a
    daemon alive is the safe direction: the cost of being wrong is a stale
    daemon and a printed warning, against killing work."""
    roots = AegisRoots.for_project(tmp_path)
    roots.state_dir.mkdir(parents=True)
    snap = roots.state_dir / "workspace.json"

    for key, item in [("tabs", {"handle": "h"}),
                      ("terminals", {"name": "t"}),
                      ("files", {"path": "f"})]:
        payload = {"version": 1, "tabs": [], "terminals": [], "files": []}
        payload[key] = [item]
        snap.write_text(json.dumps(payload))
        assert not lifecycle.nothing_to_lose(roots), \
            f"a daemon with {key} open was treated as free to replace"


def test_an_unreadable_snapshot_is_not_free_to_replace(tmp_path):
    """Cannot tell means do not kill, the same rule the reaper uses."""
    roots = AegisRoots.for_project(tmp_path)
    roots.state_dir.mkdir(parents=True)
    (roots.state_dir / "workspace.json").write_text("{not json")
    assert not lifecycle.nothing_to_lose(roots)


# --- what `aegis` does about it -------------------------------------------

def _stale_daemon(monkeypatch, tmp_path, *, alive=True):
    """A live, connectable daemon whose code has been edited since."""
    import aegis.cli as cli
    from aegis.daemon import registry as dreg

    rec = _record(tmp_path, started=0.0)
    monkeypatch.setattr(dreg, "daemon_for", lambda _root: rec if alive else None)
    monkeypatch.setattr(cli, "_daemon_for", lambda _root: rec if alive else None)
    killed: list = []
    monkeypatch.setattr(dreg, "kill", lambda r, *a, **kw: killed.append(r.pid) or True)
    attached: list = []

    async def _fake_ensure(root, **kw):
        return tmp_path / "d.sock"

    async def _fake_attach(path, view_id, **kw):
        attached.append(view_id)

    monkeypatch.setattr(cli, "_ensure_daemon", _fake_ensure)
    monkeypatch.setattr(cli, "_attach", _fake_attach)
    return rec, killed, attached


def test_a_stale_daemon_with_nothing_open_is_replaced(tmp_path, monkeypatch):
    import aegis.cli as cli

    roots = AegisRoots.for_project(tmp_path)
    roots.state_dir.mkdir(parents=True)
    _rec, killed, attached = _stale_daemon(monkeypatch, tmp_path)

    cli._attach_to_daemon(tmp_path, "tty-1")

    assert killed, "a stale daemon with an empty workspace was left running"
    assert attached == ["tty-1"], "the client did not go on to attach"


def test_a_stale_daemon_with_work_open_is_reported_not_killed(
        tmp_path, monkeypatch, capsys):
    import aegis.cli as cli

    roots = AegisRoots.for_project(tmp_path)
    roots.state_dir.mkdir(parents=True)
    (roots.state_dir / "workspace.json").write_text(json.dumps(
        {"version": 1, "tabs": [{"handle": "h"}], "terminals": [],
         "files": []}))
    _rec, killed, attached = _stale_daemon(monkeypatch, tmp_path)

    cli._attach_to_daemon(tmp_path, "tty-1")

    assert not killed, "a daemon holding a tab was killed without asking"
    assert attached == ["tty-1"], (
        "the client refused instead of attaching; refusing locks the user "
        "out, which is the failure this whole check exists to prevent")
    said = capsys.readouterr().out
    assert "aegis kill" in said, (
        f"the user was not told how to pick up the new code: {said!r}")
