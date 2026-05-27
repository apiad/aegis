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
