"""Digest collection against a real git repo. Not mocks — the thing under
test is whether we read git correctly."""
import subprocess
from pathlib import Path

import pytest

from aegis.digest.collect import DigestCollector, commits_since, read_head


def _git(root: Path, *args: str) -> str:
    return subprocess.run(("git", *args), cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def _commit(root: Path, name: str, msg: str) -> None:
    (root / name).write_text("x\n")
    _git(root, "add", name)
    _git(root, "commit", "-q", "-m", msg)


def test_read_head_returns_a_sha(repo):
    assert len(read_head(repo)) >= 7


def test_read_head_of_a_non_repo_is_empty(tmp_path):
    assert read_head(tmp_path / "nope") == ""


def test_commits_since_lists_only_new_ones(repo):
    base = read_head(repo)
    _commit(repo, "b.txt", "feat: b")
    _commit(repo, "c.txt", "fix: c")
    got = commits_since(repo, base)
    assert [c.subject for c in got] == ["fix: c", "feat: b"]
    assert all(len(c.sha) >= 7 for c in got)


def test_commits_since_is_empty_when_nothing_landed(repo):
    assert commits_since(repo, read_head(repo)) == ()


def test_commits_since_survives_a_bogus_base(repo):
    """Never raise into a turn."""
    assert commits_since(repo, "notasha") == ()


@pytest.mark.asyncio
async def test_collector_reports_commits_made_after_the_first_write(repo):
    c = DigestCollector()
    c.note_write(repo)                       # base captured here
    _commit(repo, "b.txt", "feat: b")
    facts = await c.build(plan_done=0, plan_total=0, plan_done_at_start=0,
                          assistant_tail="done", duration_s=1.0)
    assert len(facts.repos) == 1
    delta = facts.repos[0]
    assert delta.name == "demo"
    assert [x.subject for x in delta.commits] == ["feat: b"]
    assert delta.files_written == 1
    assert facts.moved is True


@pytest.mark.asyncio
async def test_collector_counts_writes_without_commits(repo):
    c = DigestCollector()
    c.note_write(repo)
    c.note_write(repo)
    facts = await c.build(plan_done=0, plan_total=0, plan_done_at_start=0,
                          assistant_tail="", duration_s=0.5)
    assert facts.repos[0].files_written == 2
    assert facts.repos[0].commits == ()
    assert facts.moved is True


@pytest.mark.asyncio
async def test_collector_never_probes_an_off_host_repo(repo):
    """The identically-named local path is a different tree there."""
    c = DigestCollector()
    c.note_write(repo, host="vps")
    _commit(repo, "b.txt", "feat: b")
    facts = await c.build(plan_done=0, plan_total=0, plan_done_at_start=0,
                          assistant_tail="", duration_s=0.1)
    delta = facts.repos[0]
    assert delta.host == "vps"
    assert delta.available is False
    assert delta.commits == ()


@pytest.mark.asyncio
async def test_collector_computes_the_plan_delta(repo):
    c = DigestCollector()
    facts = await c.build(plan_done=4, plan_total=6, plan_done_at_start=2,
                          assistant_tail="", duration_s=0.1)
    assert facts.plan_done_delta == 2
    assert facts.plan_done == 4 and facts.plan_total == 6
    assert facts.moved is True


@pytest.mark.asyncio
async def test_reset_forgets_the_previous_turn(repo):
    c = DigestCollector()
    c.note_write(repo)
    c.reset()
    facts = await c.build(plan_done=0, plan_total=0, plan_done_at_start=0,
                          assistant_tail="", duration_s=0.1)
    assert facts.repos == ()
    assert facts.moved is False
