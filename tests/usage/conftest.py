"""A two-repo workspace with three aegis sessions: one inside the target repo,
one mixed, one entirely elsewhere. Shared by test_cost_measure and
test_cost_cli."""

import subprocess

import pytest

from .stores import store


def cost_commit(repo, message, paths, date):
    stamp = f"{date}T12:00:00+00:00"
    subprocess.run(
        ("git", "add", "--", *paths), cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ("git", "commit", "-q", "-m", message),
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(repo),
            "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_DATE": stamp,
            "GIT_AUTHOR_NAME": "Tester",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "Tester",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


def said(mid: str, text: str, n: int, model: str = "claude-opus-4-7") -> dict:
    """An assistant line saying ``text``, with ``n`` input and output tokens
    and ten times that in cache reads."""
    return {
        "type": "assistant",
        "message": {
            "id": mid,
            "model": model,
            "content": [{"type": "text", "text": text}],
            "usage": {
                "input_tokens": n,
                "output_tokens": n,
                "cache_read_input_tokens": n * 10,
            },
        },
    }


INIT = {"type": "system", "subtype": "init", "model": "claude-opus-4-7"}


@pytest.fixture
def cost_tree(tmp_path):
    for name in ("aegis", "une-tools"):
        repo = tmp_path / "repos" / name
        repo.mkdir(parents=True)
        subprocess.run(
            ("git", "init", "-q", "-b", "main"),
            cwd=repo,
            check=True,
            capture_output=True,
        )
        for key, value in (("user.email", "t@example.com"), ("user.name", "Tester")):
            subprocess.run(
                ("git", "config", key, value),
                cwd=repo,
                check=True,
                capture_output=True,
            )
        (repo / "src").mkdir()
        (repo / "src" / "main.py").write_text("a = 1\n")
        cost_commit(repo, "feat: start", ("src/main.py",), "2026-06-01")

    state = tmp_path / ".aegis" / "state"
    # Working inside the target repo: share 1.0.
    store(
        state,
        "20260602-100000-aaaaaa",
        tmp_path / "repos" / "aegis",
        [
            ("2026-06-02T10:00:01Z", INIT),
            ("2026-06-02T10:00:02Z", said("m1", "x", 1000)),
        ],
        handle="inside",
    )
    # Two records name aegis, one names une-tools: share 2/3. Locality counts
    # records, so m2's two aegis paths are still one record.
    store(
        state,
        "20260602-110000-bbbbbb",
        tmp_path,
        [
            ("2026-06-02T11:00:01Z", INIT),
            (
                "2026-06-02T11:00:02Z",
                said("m2", "repos/aegis/src/a.py repos/aegis/src/b.py", 1000),
            ),
            ("2026-06-02T11:00:03Z", said("m3", "repos/aegis/src/c.py", 1)),
            ("2026-06-02T11:00:04Z", said("m4", "repos/une-tools/app.py", 1)),
        ],
        handle="mixed",
    )
    # Never names the target repo: share 0.
    store(
        state,
        "20260602-120000-cccccc",
        tmp_path,
        [
            ("2026-06-02T12:00:01Z", INIT),
            ("2026-06-02T12:00:02Z", said("m5", "repos/une-tools/app.py", 1000)),
        ],
        handle="elsewhere",
    )
    return tmp_path
