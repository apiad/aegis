"""RepoTracker — membership, attribution, recency, and the TTL probe."""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from aegis.repos.models import RepoState
from aegis.repos.probe import Baseline
from aegis.repos.tracker import RepoTracker


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@pytest.fixture
def repos(tmp_path):
    """Two bare-minimum repo roots — a `.git` dir is all the tracker needs."""
    made = {}
    for name in ("aegis", "warden"):
        root = tmp_path / name
        (root / ".git").mkdir(parents=True)
        (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        made[name] = root
    return made


def _tracker(clock=None, probe=None, capture=None):
    return RepoTracker(
        clock=clock or FakeClock(),
        probe=probe or (lambda root, base: RepoState(root=root)),
        capture=capture or (lambda root: Baseline(sha="base")))


# --- membership -------------------------------------------------------

def test_a_write_puts_its_repo_on_the_list(repos):
    t = _tracker()
    t.record("alice", repos["aegis"] / "src" / "x.py")
    views = t.snapshot()
    assert [v.state.name for v in views] == ["aegis"]
    assert views[0].writers == ("alice",)


def test_a_path_outside_any_repo_records_nothing(tmp_path):
    t = _tracker()
    t.record("alice", tmp_path / "loose" / "scratch.txt")
    assert t.snapshot() == []


def test_recording_the_same_handle_twice_does_not_duplicate_it(repos):
    t = _tracker()
    t.record("alice", repos["aegis"] / "a.py")
    t.record("alice", repos["aegis"] / "b.py")
    assert t.snapshot()[0].writers == ("alice",)


def test_rows_are_ordered_most_recently_written_first(repos):
    clock = FakeClock()
    t = _tracker(clock)
    t.record("alice", repos["aegis"] / "a.py")
    clock.advance(1)
    t.record("alice", repos["warden"] / "b.py")
    assert [v.state.name for v in t.snapshot()] == ["warden", "aegis"]
    clock.advance(1)
    t.record("alice", repos["aegis"] / "c.py")
    assert [v.state.name for v in t.snapshot()] == ["aegis", "warden"]


def test_two_agents_in_one_repo_is_the_collision(repos):
    t = _tracker()
    t.record("alice", repos["aegis"] / "a.py")
    t.record("bob", repos["aegis"] / "b.py")
    view = t.snapshot()[0]
    assert set(view.writers) == {"alice", "bob"}
    assert view.shared is True


# --- attribution ------------------------------------------------------

def test_snapshot_marks_the_asking_agents_own_repos(repos):
    t = _tracker()
    t.record("alice", repos["aegis"] / "a.py")
    t.record("bob", repos["warden"] / "b.py")
    by_name = {v.state.name: v for v in t.snapshot(for_handle="alice")}
    assert by_name["aegis"].mine is True
    assert by_name["warden"].mine is False


def test_the_asking_agent_leads_the_writer_list(repos):
    t = _tracker()
    t.record("bob", repos["aegis"] / "b.py")
    t.record("alice", repos["aegis"] / "a.py")
    assert t.snapshot(for_handle="alice")[0].writers[0] == "alice"


# --- leaving the list -------------------------------------------------

def test_dropping_a_writer_keeps_a_repo_another_agent_still_holds(repos):
    t = _tracker()
    t.record("alice", repos["aegis"] / "a.py")
    t.record("bob", repos["aegis"] / "b.py")
    t.drop("alice")
    views = t.snapshot()
    assert len(views) == 1
    assert views[0].writers == ("bob",)


def test_dropping_the_last_writer_removes_the_repo(repos):
    t = _tracker()
    t.record("alice", repos["aegis"] / "a.py")
    t.drop("alice")
    assert t.snapshot() == []


def test_dropping_an_unknown_handle_is_a_no_op(repos):
    t = _tracker()
    t.record("alice", repos["aegis"] / "a.py")
    t.drop("nobody")
    assert len(t.snapshot()) == 1


def test_a_rename_follows_the_handle(repos):
    """Otherwise the row names a handle nobody answers to, and drop() on
    close misses it — a ghost writer holding the repo forever."""
    t = _tracker()
    t.record("mild-micali", repos["aegis"] / "a.py")
    t.rename("mild-micali", "sidebar-repos")
    assert t.snapshot()[0].writers == ("sidebar-repos",)
    assert t.snapshot(for_handle="sidebar-repos")[0].mine is True
    t.drop("sidebar-repos")
    assert t.snapshot() == []


# --- subscribers ------------------------------------------------------

def test_subscribers_are_notified_on_a_new_repo(repos):
    t = _tracker()
    hits = []
    t.subscribe(lambda: hits.append(1))
    t.record("alice", repos["aegis"] / "a.py")
    assert hits == [1]


def test_a_repeat_write_to_a_known_repo_does_not_notify(repos):
    """The row did not change, and every write would otherwise repaint
    every open sidebar."""
    t = _tracker()
    t.record("alice", repos["aegis"] / "a.py")
    hits = []
    t.subscribe(lambda: hits.append(1))
    t.record("alice", repos["aegis"] / "b.py")
    assert hits == []


def test_unsubscribe_stops_the_callbacks(repos):
    t = _tracker()
    hits = []
    unsub = t.subscribe(lambda: hits.append(1))
    unsub()
    t.record("alice", repos["aegis"] / "a.py")
    assert hits == []


# --- the free path and the probe --------------------------------------

def test_a_new_repo_shows_its_branch_before_any_probe(repos):
    t = _tracker()
    t.record("alice", repos["aegis"] / "a.py")
    state = t.snapshot()[0].state
    assert state.branch == "main"     # read from .git/HEAD, no subprocess
    assert state.stale is True        # the counts are not known yet


def test_refresh_probes_and_fills_in_the_counts(repos):
    calls = []

    def probe(root, base):
        calls.append(root)
        return RepoState(root=root, branch="main", dirty=7, ahead=2)

    t = _tracker(probe=probe)
    t.record("alice", repos["aegis"] / "a.py")
    asyncio.run(t.refresh())
    state = t.snapshot()[0].state
    assert (state.dirty, state.ahead, state.stale) == (7, 2, False)
    assert calls == [repos["aegis"]]


def test_refresh_respects_the_ttl(repos):
    calls = []

    def probe(root, base):
        calls.append(root)
        return RepoState(root=root, branch="main")

    clock = FakeClock()
    t = _tracker(clock, probe)
    t.record("alice", repos["aegis"] / "a.py")
    asyncio.run(t.refresh())
    clock.advance(t.ttl / 2)
    asyncio.run(t.refresh())
    assert len(calls) == 1
    clock.advance(t.ttl)
    asyncio.run(t.refresh())
    assert len(calls) == 2


def test_a_probe_that_raises_leaves_the_row_standing(repos):
    def probe(root, base):
        raise RuntimeError("git exploded")

    t = _tracker(probe=probe)
    t.record("alice", repos["aegis"] / "a.py")
    asyncio.run(t.refresh())
    view = t.snapshot()[0]
    assert view.state.branch == "main"
    assert view.state.stale is True


def test_refresh_notifies_subscribers_once_the_state_changed(repos):
    t = _tracker(probe=lambda root, base: RepoState(
        root=root, branch="main", dirty=3))
    t.record("alice", repos["aegis"] / "a.py")
    hits = []
    t.subscribe(lambda: hits.append(1))
    asyncio.run(t.refresh())
    assert hits == [1]
    asyncio.run(t.refresh(force=True))   # same numbers → no repaint
    assert hits == [1]


# --- the session baseline ---------------------------------------------

def test_the_baseline_is_captured_at_the_first_write(repos):
    seen = []
    t = _tracker(capture=lambda root: seen.append(root) or Baseline(sha="s"))
    t.record("alice", repos["aegis"] / "a.py")
    assert seen == [repos["aegis"]]


def test_the_baseline_is_captured_once_per_repo(repos):
    """Re-capturing on a later write would move the anchor forward and
    discard everything the session had already done there."""
    seen = []
    t = _tracker(capture=lambda root: seen.append(root) or Baseline(sha="s"))
    t.record("alice", repos["aegis"] / "a.py")
    t.record("alice", repos["aegis"] / "b.py")
    t.record("bob", repos["aegis"] / "c.py")     # a second writer arrives
    assert len(seen) == 1


def test_the_baseline_reaches_the_probe(repos):
    seen = []
    t = _tracker(probe=lambda root, base: seen.append(base) or RepoState(
        root=root), capture=lambda root: Baseline(sha="deadbeef"))
    t.record("alice", repos["aegis"] / "a.py")
    asyncio.run(t.refresh())
    assert [b.sha for b in seen] == ["deadbeef"]


def test_a_capture_that_raises_leaves_the_repo_on_the_list(repos):
    """A dashboard must never take a write event down with it."""
    def capture(root):
        raise RuntimeError("git exploded")

    t = _tracker(capture=capture)
    t.record("alice", repos["aegis"] / "a.py")
    assert [v.state.name for v in t.snapshot()] == ["aegis"]


def test_the_baseline_is_forgotten_with_the_repo(repos):
    seen = []
    t = _tracker(capture=lambda root: seen.append(root) or Baseline(sha="s"))
    t.record("alice", repos["aegis"] / "a.py")
    t.drop("alice")
    t.record("alice", repos["aegis"] / "a.py")
    assert len(seen) == 2


# --- remote hosts -----------------------------------------------------

def test_an_off_host_repo_is_listed_but_never_probed(repos):
    calls, captured = [], []
    t = _tracker(
        probe=lambda root, base: calls.append(root) or RepoState(root=root),
        capture=lambda root: captured.append(root) or Baseline())
    t.record("alice", "/srv/warden/main.py", host="vps")
    asyncio.run(t.refresh())
    view = t.snapshot()[0]
    assert view.host == "vps"
    assert view.label == "warden@vps"
    assert view.state.branch == ""
    assert calls == []
    # The same path names a different tree here; capturing a baseline off
    # the local disk would anchor the churn to the wrong repo entirely.
    assert captured == []


def test_the_host_is_part_of_a_repos_identity(repos):
    """The same path string names a different tree on another machine, so
    it is a different row. Keying on the root path alone would report a
    peer as sharing a repo it has never touched — the collision warning
    firing on nothing."""
    t = _tracker()
    t.record("alice", repos["aegis"] / "a.py")
    t.record("bob", str(repos["aegis"] / "a.py"), host="vps")
    views = t.snapshot()
    assert len(views) == 2
    assert {v.host for v in views} == {"local", "vps"}
    assert all(v.shared is False for v in views)


# --- against a real repo ----------------------------------------------

@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_end_to_end_against_a_real_repo(tmp_path):
    root = tmp_path / "real"
    root.mkdir()
    for args in (("init", "-q", "-b", "main"),
                 ("config", "user.email", "t@e.com"),
                 ("config", "user.name", "T")):
        subprocess.run(["git", *args], cwd=root, check=True,
                       capture_output=True)
    (root / "a.txt").write_text("x")
    subprocess.run(["git", "add", "a.txt"], cwd=root, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True,
                   capture_output=True)
    t = RepoTracker(clock=FakeClock())
    t.record("alice", root / "a.txt")        # baseline captured here

    # What the session then does: one commit, one uncommitted new file.
    (root / "b.txt").write_text("one\ntwo\n")
    subprocess.run(["git", "add", "b.txt"], cwd=root, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-qm", "b"], cwd=root, check=True,
                   capture_output=True)
    (root / "c.txt").write_text("three\n")

    asyncio.run(t.refresh())
    state = t.snapshot()[0].state
    assert state.branch == "main"
    assert state.dirty == 1
    assert state.stale is False
    assert (state.added, state.deleted) == (3, 0)


def test_paths_are_accepted_as_strings(repos):
    t = _tracker()
    t.record("alice", str(repos["aegis"] / "a.py"))
    assert t.snapshot()[0].state.root == Path(repos["aegis"])
