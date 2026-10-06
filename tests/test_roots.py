from pathlib import Path

from aegis.roots import make_roots


def test_nearest_ancestor_with_config_is_the_root(tmp_path: Path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    roots = make_roots(start=deep, root=None)
    assert roots.config_root == tmp_path
    assert roots.state_root == tmp_path / ".aegis2" / "state"
    assert roots.harness_cwd == tmp_path


def test_explicit_root_wins(tmp_path: Path):
    (tmp_path / ".aegis.yaml").write_text("")
    other = tmp_path / "other"
    other.mkdir()
    assert make_roots(start=tmp_path, root=other).config_root == other


def test_no_config_anywhere_means_start(tmp_path: Path):
    start = tmp_path / "x"
    start.mkdir()
    assert make_roots(start=start, root=None).config_root == start
