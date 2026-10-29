import os
import subprocess

import pytest

from aegis.knowledge import history


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_commit_returns_a_sha_and_log_lists_it(tmp_path):
    history.ensure_repo(tmp_path)
    _write(tmp_path, "memories/a.md", "one\n")
    sha = history.commit(tmp_path, ["memories/a.md"], "save a", "s1")
    assert len(sha) == 40
    entries = history.log(tmp_path, "memories/a.md")
    assert [e["sha"] for e in entries] == [sha]
    assert entries[0]["message"].startswith("save a") and entries[0]["at"] > 0


def test_an_unchanged_file_commits_nothing(tmp_path):
    history.ensure_repo(tmp_path)
    _write(tmp_path, "memories/a.md", "one\n")
    history.commit(tmp_path, ["memories/a.md"], "save a", "s1")
    assert history.commit(tmp_path, ["memories/a.md"], "save a again", "s1") == ""


def test_revert_restores_a_deleted_memory(tmp_path):
    history.ensure_repo(tmp_path)
    _write(tmp_path, "memories/a.md", "one\n")
    history.commit(tmp_path, ["memories/a.md"], "save a", "s1")
    (tmp_path / "memories" / "a.md").unlink()
    sha = history.commit(tmp_path, ["memories/a.md"], "delete a", "s1")
    assert sha
    assert history.revert(tmp_path, sha, "ana")
    assert (tmp_path / "memories" / "a.md").read_text() == "one\n"


def test_show_returns_an_old_version(tmp_path):
    history.ensure_repo(tmp_path)
    _write(tmp_path, "memories/a.md", "one\n")
    first = history.commit(tmp_path, ["memories/a.md"], "save a", "s1")
    _write(tmp_path, "memories/a.md", "two\n")
    history.commit(tmp_path, ["memories/a.md"], "edit a", "s1")
    assert history.show(tmp_path, "memories/a.md", first) == "one\n"
    assert history.show(tmp_path, "memories/nope.md", first) is None


def test_host_git_config_is_ignored(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        "[commit]\n\tgpgsign = true\n[user]\n\tsigningkey = nothing\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    root = tmp_path / "store"
    history.ensure_repo(root)
    _write(root, "memories/a.md", "one\n")
    assert history.commit(root, ["memories/a.md"], "save a", "s1")


def test_git_variables_in_the_server_env_have_no_effect(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "someone else")
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "stray-index"))
    root = tmp_path / "k"
    history.ensure_repo(root)
    (root / "a.md").write_text("a\n")
    sha = history.commit(root, ["a.md"], "save a", "s1")
    assert sha and not (tmp_path / "stray-index").exists()
    author = subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%an"],
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    ).stdout.strip()
    assert author == "aegis"


def test_a_failed_revert_is_aborted_and_raises(tmp_path):
    history.ensure_repo(tmp_path)
    for text in ("one\n", "two\n", "three\n"):
        _write(tmp_path, "a.md", text)
        sha = history.commit(tmp_path, ["a.md"], "edit a", "s1")
        if text == "two\n":
            middle = sha
    with pytest.raises(Exception):
        history.revert(tmp_path, middle, "s1")  # conflicts with "three"
    assert (tmp_path / "a.md").read_text() == "three\n"
    assert not (tmp_path / ".git" / "REVERT_HEAD").exists()
    status = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain"],
        capture_output=True,
        text=True,
    ).stdout
    assert status == ""


def test_revert_commits_only_what_the_reverted_commit_touched(tmp_path):
    history.ensure_repo(tmp_path)
    _write(tmp_path, "memories/a.md", "one\n")
    _write(tmp_path, "memories/b.md", "bee\n")
    history.commit(tmp_path, ["memories/a.md", "memories/b.md"], "save", "s1")
    _write(tmp_path, "memories/a.md", "two\n")
    sha = history.commit(tmp_path, ["memories/a.md"], "update a", "s1")
    _write(tmp_path, "memories/b.md", "a hand edit\n")  # not through aegis
    assert history.revert(tmp_path, sha, "ana")
    assert (tmp_path / "memories" / "a.md").read_text() == "one\n"
    dirty = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert dirty.split() == ["M", "memories/b.md"]
