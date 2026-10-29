"""A repo's owner, from its remote, cached and reread when the remote moves."""

import os
import subprocess

from aegis.journal import db
from aegis.usage import tables
from aegis.usage.owners import owner_of


def git(*args, cwd):
    subprocess.run(("git", *args), cwd=cwd, check=True, capture_output=True)


def repo(path, origin=None):
    path.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=path)
    if origin:
        git("remote", "add", "origin", origin, cwd=path)
    return str(path.resolve())


def _con(tmp_path):
    con, _ = db.connect(tmp_path / "journal.db")
    tables.ensure(con)
    return con


def test_the_owner_is_the_account_in_the_remote_or_local(tmp_path):
    con = _con(tmp_path)
    atlas = repo(tmp_path / "atlas", "git@github.com:acme-co/atlas.git")
    notes = repo(tmp_path / "notes")
    assert owner_of(con, atlas, atlas, None, {}) == "acme-co"
    assert owner_of(con, notes, notes, None, {}) == "local"
    assert con.execute("SELECT owner, remote FROM repo_owners WHERE repo = ?",
                       (atlas,)).fetchone() == ("acme-co", "acme-co/atlas")  # fmt: skip


def test_the_config_names_the_owner_of_a_local_repo(tmp_path):
    con = _con(tmp_path)
    site = repo(tmp_path / "site")
    assert owner_of(con, site, site, None, {"owners": {"site": "acme-co"}}) == "acme-co"


def test_the_answer_is_cached_until_the_git_config_changes(tmp_path):
    con = _con(tmp_path)
    atlas = repo(tmp_path / "atlas", "https://github.com/acme-co/atlas")
    assert owner_of(con, atlas, atlas, None, {}) == "acme-co"
    con.execute("UPDATE repo_owners SET owner = 'cached'")
    assert owner_of(con, atlas, atlas, None, {}) == "cached"
    git("remote", "set-url", "origin", "https://github.com/ben/atlas", cwd=atlas)
    cfg = os.path.join(atlas, ".git", "config")
    later = con.execute("SELECT checked_at FROM repo_owners").fetchone()[0] + 5
    os.utime(cfg, (later, later))
    assert owner_of(con, atlas, atlas, None, {}) == "ben"
