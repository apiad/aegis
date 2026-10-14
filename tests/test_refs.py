"""What a reply's references name on this disk (#393)."""

from pathlib import Path

from aegis import refs


def test_kind_reads_the_disk_then_the_shape(tmp_path):
    root, cwd = tmp_path, tmp_path / "sub"
    (root / "src").mkdir()
    (root / "src" / "x.py").write_text("x = 1\n")
    (cwd / "octo" / "aegis").mkdir(parents=True)

    def k(text):
        return refs.kind(text, cwd, root)

    assert k("src/x.py") == "file"
    assert k("src/x.py:12") == "file", "the :line is not part of the name"
    assert k("src") == "dir" and k("src/") == "dir"
    assert k("octo/aegis") == "dir", "a folder under the cwd wins over a repo"
    assert k("other/aegis") == "repo"
    assert k("octo/aegis#3") == "repo", "an issue ref is a repo whatever the disk holds"
    assert k("github.com/octo/aegis/pull/3") == "repo"
    assert k("src/gone.py") == "missing"
    assert k("DESIGN.md") == "missing"
    assert k("a/b/c") == "code", "a missing folder shape is plain code"
    assert k("octo/aegis.py") == "missing", "an extension makes it a file, not a repo"


def test_kind_expands_home_and_takes_absolute_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "notes.md").write_text("# n\n")
    elsewhere = tmp_path / "elsewhere"
    assert refs.kind("~/notes.md", elsewhere, elsewhere) == "file"
    assert refs.kind(str(tmp_path / "notes.md"), elsewhere, elsewhere) == "file"
    assert refs.tried("/etc/x.conf:3", elsewhere, elsewhere) == {
        "absolute": Path("/etc/x.conf")
    }


def test_an_unreadable_path_is_missing_not_an_error(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "a.py").write_text("a = 1\n")
    locked.chmod(0)
    try:
        assert refs.kind("locked/a.py", tmp_path, tmp_path) == "missing"
        assert refs.kind("src/x.py", tmp_path, tmp_path) == "missing"
    finally:
        locked.chmod(0o755)
