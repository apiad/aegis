"""The file index starts on first use and stops watching when idle.

Every view started one at mount and kept a recursive watchdog observer on
cwd for the life of the daemon, picker opened or not: 100,953 inotify
watches on the Workspace, and about 11% of a core of event processing in
the daemon's process with 12 agents working (#21).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from aegis.tui.file_index import FileIndexer


def _wait(pred, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _indexer(root: Path, idle_s: float = 600.0) -> FileIndexer:
    idx = FileIndexer()
    idx.IDLE_STOP_S = idle_s
    idx.set_root(root)
    return idx


def test_setting_the_root_walks_and_watches_nothing(tmp_path):
    (tmp_path / "a.py").write_text("x")
    before = threading.active_count()
    idx = _indexer(tmp_path)
    time.sleep(0.3)
    assert not idx.ready
    assert idx.paths == []
    assert idx._observer is None
    assert threading.active_count() == before


def test_first_use_walks_and_watches(tmp_path):
    (tmp_path / "a.py").write_text("x")
    idx = _indexer(tmp_path)
    try:
        idx.use()
        assert _wait(lambda: idx.ready)
        assert idx.paths == ["a.py"]
        (tmp_path / "b.py").write_text("x")
        assert _wait(lambda: "b.py" in idx.paths)
    finally:
        idx.stop()


def test_idle_stops_the_watcher_and_keeps_the_index(tmp_path):
    (tmp_path / "a.py").write_text("x")
    idx = _indexer(tmp_path, idle_s=0.3)
    try:
        idx.use()
        assert _wait(lambda: idx.ready)
        assert _wait(lambda: idx._observer is None), "still watching when idle"
        assert idx.ready  # a stale index is still an answer
        assert idx.paths == ["a.py"]
    finally:
        idx.stop()


def test_use_after_idle_refreshes_without_emptying_the_index(tmp_path):
    (tmp_path / "a.py").write_text("x")
    idx = _indexer(tmp_path, idle_s=0.3)
    try:
        idx.use()
        assert _wait(lambda: idx.ready)
        assert _wait(lambda: idx._observer is None)
        (tmp_path / "while-dormant.py").write_text("x")
        time.sleep(0.2)
        assert "while-dormant.py" not in idx.paths  # nobody was watching
        seen: list[list[str]] = []
        idx.IDLE_STOP_S = 600.0
        idx.use()
        assert _wait(lambda: seen.append(idx.paths) or "while-dormant.py" in idx.paths)
        assert all("a.py" in s for s in seen), "the index went empty mid-refresh"
        assert _wait(lambda: idx._observer is not None)
    finally:
        idx.stop()


def test_use_keeps_a_busy_index_awake(tmp_path):
    idx = _indexer(tmp_path, idle_s=0.4)
    try:
        idx.use()
        assert _wait(lambda: idx.ready)
        for _ in range(8):
            time.sleep(0.1)
            idx.use()
        assert idx._observer is not None
    finally:
        idx.stop()


def test_stop_ends_every_thread_it_started(tmp_path):
    before = threading.active_count()
    idx = _indexer(tmp_path)
    idx.use()
    assert _wait(lambda: idx.ready)
    idx.stop()
    assert _wait(lambda: threading.active_count() <= before), "a thread outlived stop()"
