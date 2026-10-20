"""The Repos service on real git repos and a real journal file."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from aegis.journal import db
from aegis.repos import gh, probe, service
from aegis.repos.service import Repos

G = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def run(cwd, *args):
    return subprocess.run(
        [*G, *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture(autouse=True)
def no_gh(tmp_path, monkeypatch):
    """No gh on PATH unless a test puts one there."""
    monkeypatch.setattr(gh.shutil, "which", lambda name: None)
    gh._cache.clear()


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / "remote.git"
    run(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    r = tmp_path / "repo"
    r.mkdir()
    run(r, "init", "-q", "-b", "main")
    run(r, "remote", "add", "origin", str(remote))
    (r / "a.py").write_text("x\n")
    run(r, "add", "a.py")
    run(r, "commit", "-q", "-m", "first")
    run(r, "push", "-q", "-u", "origin", "main")
    return r.resolve()


class World:
    def __init__(self, tmp: Path):
        self.path = tmp / "journal.db"
        self.con, _ = db.connect(self.path)
        self.rec = 0
        self.published: list[tuple[str, list]] = []
        self.open = {"L1": "ana"}
        self.repos = Repos(
            self.path,
            lambda ch, ops: self.published.append((ch, ops)),
            lambda: self.open,
            lambda name: 1,
            tmp,
        )

    def wrote(self, log_id, top: Path, rel: str, main: Path | None = None, ts=1.0):
        main = main or top
        self.rec += 1
        db.insert(
            self.con,
            db.Entry(
                log_id,
                self.rec,
                0,
                ts,
                "h",
                str(main),
                "turn",
                "",
                "t",
                None,
                False,
                [("write", str(main), rel, str(main / rel), str(top))],
                top=str(top),
            ),
        )

    def committed(
        self, log_id, top: Path, sha: str, subject: str, branch="main", ts=2.0
    ):
        self.rec += 1
        db.insert(
            self.con,
            db.Entry(
                log_id,
                self.rec,
                0,
                ts,
                "h",
                str(top),
                "commit",
                "",
                f"{sha[:7]} {subject} · {branch}",
                None,
                False,
                [],
                top=str(top),
                sha=sha,
            ),
        )

    def pr(self, log_id, top: Path, text: str, ts=3.0):
        self.rec += 1
        db.insert(
            self.con,
            db.Entry(
                log_id,
                self.rec,
                0,
                ts,
                "h",
                str(top),
                "pr",
                "",
                text,
                None,
                False,
                [],
                top=str(top),
            ),
        )


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_shared_checkout_counts_only_own_files(world, repo):
    (repo / "a.py").write_text("x\nmine\n")
    (repo / "z.py").write_text("theirs\n")
    world.wrote("L1", repo, "a.py")
    [line] = world.repos.build("L1")["repos"]
    assert (line["name"], line["path"], line["worktree"]) == ("repo", "repo", False)
    assert line["dirty"] == [{"path": "a.py", "stat": "+1 −0"}]
    assert (line["dirty_n"], line["others_n"], line["clean"]) == (1, 1, False)


def test_worktree_is_its_own_line_newest_first(world, repo, tmp_path):
    wt = tmp_path / "wt"
    run(repo, "worktree", "add", "-q", "-b", "topic", str(wt))
    wt = wt.resolve()
    (wt / "b.py").write_text("b\n")
    world.wrote("L1", repo, "a.py", ts=1.0)
    world.wrote("L1", wt, "b.py", main=repo, ts=5.0)
    lines = world.repos.build("L1")["repos"]
    assert [(ln["name"], ln["worktree"], ln["branch"]) for ln in lines] == [
        ("repo", True, "topic"),
        ("repo", False, "main"),
    ]
    assert lines[0]["dirty_n"] == 1 and lines[1]["clean"] is True


def test_push_turns_the_line_clean(world, repo):
    (repo / "c.py").write_text("c\n")
    run(repo, "add", "c.py")
    run(repo, "commit", "-q", "-m", "add c")
    sha = run(repo, "rev-parse", "HEAD")
    world.wrote("L1", repo, "c.py")
    world.committed("L1", repo, sha, "add c")
    [line] = world.repos.build("L1")["repos"]
    assert (line["unpushed"], line["unpushed_n"], line["clean"]) == (
        ["add c"],
        1,
        False,
    )
    assert (
        line["left"] == [{"hash": sha[:7], "subject": "add c"}] and line["left_n"] == 1
    )
    run(repo, "push", "-q")
    world.repos._probes.clear()
    [line] = world.repos.build("L1")["repos"]
    assert (line["unpushed_n"], line["clean"]) == (0, True)


def test_a_removed_worktree_stays_listed_and_is_not_probed(world, repo, tmp_path):
    gone = tmp_path / "gone"
    world.wrote("L1", gone, "x.py", main=repo)
    [line] = world.repos.build("L1")["repos"]
    assert (line["removed"], line["stale"], line["clean"]) == (True, False, False)


def test_prs_come_from_the_journal_when_gh_is_missing(world, repo):
    world.pr("L1", repo, "opened https://github.com/o/repo/pull/12 · feat: x")
    world.pr("L1", repo, "merged #9")
    data = world.repos.build("L1")
    [line] = data["repos"]
    assert [(p["n"], p["state"]) for p in line["prs"]] == [(12, "open"), (9, "merged")]
    assert line["prs"][0]["url"] == "https://github.com/o/repo/pull/12"
    assert data["gh"] is False


def test_a_peer_in_the_same_repo_is_named(world, repo):
    world.open = {"L1": "ana", "L2": "ben"}
    world.wrote("L1", repo, "a.py")
    world.wrote("L2", repo, "a.py")
    world.wrote("L3", repo, "a.py")  # not open: not a peer
    [line] = world.repos.build("L1")["repos"]
    assert line["peers"] == [{"handle": "ben", "where": "main checkout, main"}]


def test_a_session_outside_any_repo_has_no_lines(world):
    assert world.repos.build("L9") == {"repos": [], "gh": True}


async def test_a_live_write_shows_before_the_journal_has_it(world, repo):
    world.repos.start()
    try:
        world.repos.live("L1", "ana", [str(repo / "n.py")], [])
        (repo / "n.py").write_text("n\n")
        await asyncio.sleep(0.05)
        [line] = world.repos.build("L1")["repos"]
        assert line["top"] == str(repo)
        world.repos._probes.clear()
        [line] = world.repos.build("L1")["repos"]
        assert line["dirty"] == [{"path": "n.py", "stat": "new"}]
    finally:
        await world.repos.stop()


async def test_twenty_writes_in_the_window_probe_at_most_twice(
    world, repo, monkeypatch
):
    monkeypatch.setattr(service, "PROBE_EVERY", 0.3)
    calls = []
    real = probe.probe
    monkeypatch.setattr(probe, "probe", lambda top: calls.append(top) or real(top))
    world.repos.start()
    try:
        for k in range(20):
            world.repos.live("L1", "ana", [str(repo / f"f{k}.py")], [])
        await asyncio.sleep(0.05)
        assert len(calls) == 1
        await asyncio.sleep(0.5)
        assert len(calls) <= 2
    finally:
        await world.repos.stop()


async def test_an_unchanged_snapshot_is_not_published_again(world, repo, monkeypatch):
    monkeypatch.setattr(service, "PUBLISH_EVERY", 0.1)
    world.wrote("L1", repo, "a.py")
    world.repos.start()
    try:
        assert world.repos.snapshot("L1") == {"repos": None}
        for _ in range(50):
            await asyncio.sleep(0.02)
            if world.published:
                break
        assert [ch for ch, _ in world.published] == ["repos:L1"]
        await asyncio.sleep(0.15)
        world.repos.refresh("L1")
        await asyncio.sleep(0.3)
        assert len(world.published) == 1
        (repo / "a.py").write_text("changed\n")
        world.repos._probes.clear()
        world.repos.refresh("L1")
        await asyncio.sleep(0.3)
        assert len(world.published) == 2
        assert world.published[-1][1][0]["set"]["repos"][0]["dirty_n"] == 1
    finally:
        await world.repos.stop()
