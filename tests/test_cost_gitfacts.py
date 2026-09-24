import subprocess
from pathlib import Path

import pytest

from aegis.cost.gitfacts import coverage, git_facts
from aegis.cost.locality import SPLIT_DIRS


def _git(repo: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "sample"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Tester")
    return r


def _commit(repo: Path, message: str, files: dict[str, str], date: str) -> None:
    for name, body in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        _git(repo, "add", "--", name)
    stamp = f"{date}T12:00:00+00:00"
    subprocess.run(
        ("git", "commit", "-q", "-m", message),
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_DATE": stamp,
            "PATH": "/usr/bin:/bin",
            "HOME": str(repo),
            "GIT_AUTHOR_NAME": "Tester",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "Tester",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


def test_record_separator_in_a_subject_does_not_swallow_commits(repo):
    """Trap 2. Parsing with \\x1e and str.splitlines() returns zero commits,
    silently, because Python also breaks lines on \\x1c-\\x1e."""
    _commit(repo, "feat: first", {"a.py": "x = 1\n"}, "2026-06-01")
    _commit(repo, "fix: a \x1e b \x1c c", {"b.py": "y = 2\n"}, "2026-06-02")
    _commit(repo, "docs: third", {"c.md": "hello\n"}, "2026-06-03")

    facts = git_facts(repo, since=None, until=None, split_dirs=SPLIT_DIRS, exclude=())

    assert facts.n_commits == 3


def test_a_binary_never_counts_as_a_line_of_text(repo):
    """Trap 3. Two PDFs in docs/ were 62,074 "lines added" in one week, which
    made that week the most productive of the project at $0.49 per 1k lines."""
    _commit(repo, "feat: code", {"src/main.py": "a = 1\nb = 2\n"}, "2026-06-01")
    _commit(
        repo,
        "docs: report",
        {"docs/report.pdf": "\n".join("xxxx" for _ in range(500))},
        "2026-06-02",
    )

    facts = git_facts(repo, since=None, until=None, split_dirs=SPLIT_DIRS, exclude=())
    week = facts.weeks["2026-W23"]

    # Both commits land in the same ISO week, so `text` is the 2 lines of
    # code and nothing else: the 500-line PDF did not enter it.
    assert week["bin"] >= 500
    assert week["text"] == 2
    assert week["code"] == 2
    assert week.get("prose", 0) == 0
    assert facts.loc["docs"]["binaries"] == 1
    assert facts.loc["docs"].get("prose", 0) == 0


def test_exclude_drops_a_path_from_churn_and_from_the_tree(repo):
    """Trap 5. A report that lives in the repo counts itself on the next run:
    its own 498 lines of prose enter the module table."""
    _commit(repo, "feat: code", {"src/main.py": "a = 1\n"}, "2026-06-01")
    _commit(
        repo,
        "docs: cost report",
        {"docs/cost-report.md": "\n".join(f"line {i}" for i in range(498))},
        "2026-06-02",
    )

    with_report = git_facts(
        repo, since=None, until=None, split_dirs=SPLIT_DIRS, exclude=()
    )
    without = git_facts(
        repo,
        since=None,
        until=None,
        split_dirs=SPLIT_DIRS,
        exclude=("docs/cost-report.md",),
    )

    assert with_report.loc["docs"]["prose"] == 498
    assert "docs" not in without.loc
    assert "docs" not in without.churn
    assert without.excluded == ("docs/cost-report.md",)
    # The commit itself still exists; only its lines are dropped.
    assert without.n_commits == 2


def test_coverage_is_the_share_of_commits_inside_the_transcript_window():
    """Trap 4. 93% of beaver's commits fell outside the 2026-05-29 transcript
    floor and the repo came out looking free."""
    dates = ["2026-01-01", "2026-02-01", "2026-06-01", "2026-06-02"]

    assert coverage(dates, "2026-05-29T00:00:00Z") == 0.5
    assert coverage(dates, None) == 0.0
    assert coverage([], "2026-05-29T00:00:00Z") == 1.0
    assert coverage(dates, "2020-01-01T00:00:00Z") == 1.0


def test_a_window_after_every_commit_yields_no_commits_and_full_coverage(repo):
    _commit(repo, "feat: code", {"src/main.py": "a = 1\n"}, "2026-06-01")

    facts = git_facts(
        repo, since="2027-01-01", until=None, split_dirs=SPLIT_DIRS, exclude=()
    )

    assert facts.n_commits == 0
    assert facts.dates == []
    assert facts.first is None and facts.last is None
    assert coverage(facts.dates, "2026-05-29T00:00:00Z") == 1.0
