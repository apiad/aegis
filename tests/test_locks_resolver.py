from aegis.locks.resolver import resolve_paths


def test_prefix_and_file_passthrough(tmp_path):
    prefixes, files = resolve_paths(
        ["src/aegis/tui/", "src/aegis/mcp/server.py"], tmp_path)
    assert prefixes == frozenset({"src/aegis/tui/"})
    assert files == frozenset({"src/aegis/mcp/server.py"})


def test_glob_expands_to_concrete_files(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("")
    (tmp_path / "pkg" / "b.py").write_text("")
    (tmp_path / "pkg" / "c.txt").write_text("")
    prefixes, files = resolve_paths(["pkg/*.py"], tmp_path)
    assert files == frozenset({"pkg/a.py", "pkg/b.py"})
    assert prefixes == frozenset()


def test_blank_entries_ignored(tmp_path):
    prefixes, files = resolve_paths(["", "  ", "x.py"], tmp_path)
    assert files == frozenset({"x.py"})
    assert prefixes == frozenset()
