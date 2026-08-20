from __future__ import annotations

import time
from pathlib import Path

import pytest

from aegis.tui.file_index import FileIndexer


def _wait_ready(indexer: FileIndexer, timeout: float = 5.0) -> None:
    assert indexer._ready.wait(timeout), "indexer did not become ready"


def test_indexes_files(tmp_path: Path):
    (tmp_path / "foo.py").write_text("x")
    (tmp_path / "bar.md").write_text("y")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    assert "foo.py" in idx.paths
    assert "bar.md" in idx.paths
    idx.stop()


def test_ignores_pyc(tmp_path: Path):
    (tmp_path / "ok.py").write_text("x")
    (tmp_path / "bad.pyc").write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    assert "ok.py" in idx.paths
    assert "bad.pyc" not in idx.paths
    idx.stop()


def test_ignores_pycache_dir(tmp_path: Path):
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "mod.cpython-313.pyc").write_text("x")
    (tmp_path / "real.py").write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    assert "real.py" in idx.paths
    assert not any("__pycache__" in p for p in idx.paths)
    idx.stop()


def test_ignores_venv_dir(tmp_path: Path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pip.py").write_text("x")
    (tmp_path / "main.py").write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    assert "main.py" in idx.paths
    assert not any(".venv" in p for p in idx.paths)
    idx.stop()


def test_filter_substring(tmp_path: Path):
    (tmp_path / "alpha.py").write_text("x")
    (tmp_path / "beta.py").write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    result = idx.filter("alp")
    assert "alpha.py" in result
    assert "beta.py" not in result
    idx.stop()


def test_watchdog_adds_file(tmp_path: Path):
    (tmp_path / "existing.py").write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    # Create a new file after indexer is running.
    (tmp_path / "new_file.py").write_text("x")
    time.sleep(0.5)  # give watchdog time to fire
    assert "new_file.py" in idx.paths
    idx.stop()


def test_watchdog_removes_file(tmp_path: Path):
    f = tmp_path / "soon_gone.py"
    f.write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    assert "soon_gone.py" in idx.paths
    f.unlink()
    time.sleep(0.5)
    assert "soon_gone.py" not in idx.paths
    idx.stop()


def test_ready_false_before_start():
    idx = FileIndexer()
    assert not idx.ready


def test_paths_by_mtime_most_recent_first(tmp_path: Path):
    old = tmp_path / "old.py"
    old.write_text("x")
    time.sleep(0.02)
    new = tmp_path / "new.py"
    new.write_text("y")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    result = idx.paths_by_mtime()
    assert result.index("new.py") < result.index("old.py")
    idx.stop()


def test_paths_by_mtime_remove_clears_mtime(tmp_path: Path):
    f = tmp_path / "gone.py"
    f.write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    assert "gone.py" in idx.paths_by_mtime()
    idx._remove(str(f))
    assert "gone.py" not in idx.paths_by_mtime()
    idx.stop()


def test_paths_by_mtime_add_inserts_with_mtime(tmp_path: Path):
    f = tmp_path / "added.py"
    f.write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    _wait_ready(idx)
    new_f = tmp_path / "newest.py"
    new_f.write_text("y")
    idx._add(str(new_f))
    result = idx.paths_by_mtime()
    assert "newest.py" in result
    assert result.index("newest.py") == 0   # newest is first
    idx.stop()
