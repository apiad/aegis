"""The file indexer's walk and its incremental updates.

Indexing gated readiness on the *complete* walk, which on a 60k-file tree
took 17-43s — the picker was unusable for that whole window, and the walk
holds the GIL in bursts, so the event loop took a 6x p50 latency hit right
when boot is also replaying transcripts. And every filesystem event
re-sorted the entire path list: 9.6ms per created file at 60k paths, under
a lock the picker's own filter() takes.
"""
from __future__ import annotations

import time

from aegis.tui.file_index import FileIndexer


def _tree(root, dirs: int, per_dir: int) -> int:
    total = 0
    for d in range(dirs):
        sub = root / f"pkg{d:03d}"
        sub.mkdir()
        for f in range(per_dir):
            (sub / f"mod{f:03d}.py").write_text("x")
            total += 1
    return total


def _wait(pred, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_paths_are_published_during_the_walk_not_only_at_the_end(tmp_path):
    """The picker gets something to work with early, instead of nothing
    until the last directory is visited. On the operator's tree that wait
    was 17-43 seconds."""
    total = _tree(tmp_path, dirs=40, per_dir=60)      # 2400 files

    publishes: list[int] = []

    class Counting(FileIndexer):
        def _publish(self, paths, mtimes=None):
            publishes.append(len(paths))
            super()._publish(paths, mtimes)

    idx = Counting()
    idx.start(tmp_path)
    try:
        assert _wait(lambda: idx.ready)
        assert len(idx.paths) == total
        assert len(publishes) > 1, (
            f"published once ({publishes}) — the picker stays empty for the "
            f"whole walk")
        assert publishes[0] < total          # partial results came first
    finally:
        idx.stop()


def test_the_index_stays_sorted_and_deduped_as_files_appear(tmp_path):
    idx = FileIndexer()
    idx.start(tmp_path)
    try:
        assert _wait(lambda: idx.ready)
        for name in ("zeta.py", "alpha.py", "mid.py", "alpha.py"):
            idx._add(str(tmp_path / name))
        paths = idx.paths
        assert paths == sorted(paths)
        assert paths.count("alpha.py") <= 1
    finally:
        idx.stop()


def test_added_paths_are_findable(tmp_path):
    idx = FileIndexer()
    idx.start(tmp_path)
    try:
        assert _wait(lambda: idx.ready)
        (tmp_path / "needle.py").write_text("x")
        idx._add(str(tmp_path / "needle.py"))
        assert "needle.py" in idx.filter("needle")
        idx._remove(str(tmp_path / "needle.py"))
        assert "needle.py" not in idx.filter("needle")
    finally:
        idx.stop()


def test_ignored_directories_stay_out_of_the_index(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    try:
        assert _wait(lambda: idx.ready)
        assert idx.paths == ["src/app.py"]
    finally:
        idx.stop()


def _indexer_over(paths: list[str], cwd="/ws") -> FileIndexer:
    """An indexer holding ``paths`` as if a walk of ``cwd`` had found them."""
    idx = FileIndexer()
    idx._cwd = __import__("pathlib").Path(cwd)
    idx._publish(paths)
    return idx


def test_a_delete_outside_the_index_does_not_scan_the_index():
    """Every delete and every rename called ``list.remove`` over the whole
    index, even for a path that was never in it. On the operator's 61k-path
    Workspace that was 3.6ms per event with the GIL held; py-spy found the
    daemon's watchdog thread in that one line on 6 of 6 samples, pinning a
    core while test runs and git in other worktrees churned files."""
    paths = [f"repos/proj{d:03d}/src/module_{f:05d}.py"
             for d in range(60) for f in range(1000)]
    idx = _indexer_over(paths)
    misses = [f"/ws/repos/proj{d:03d}/src/module_{f:05d}.pyi"
              for d in range(60) for f in range(0, 1000, 120)]

    t = time.perf_counter()
    for p in misses:
        idx._remove(p)
    elapsed = time.perf_counter() - t

    assert len(idx.paths) == len(paths)
    assert elapsed < 0.05, (
        f"{1e3 * elapsed / len(misses):.2f}ms per miss — the index is "
        f"being scanned linearly")


def test_removing_a_path_keeps_the_index_sorted():
    idx = _indexer_over(["a.py", "b.py", "c.py"])
    idx._remove("/ws/b.py")
    idx._remove("/ws/b.py")
    idx._remove("/ws/zz.py")
    assert idx.paths == ["a.py", "c.py"]


def test_events_under_ignored_paths_touch_no_file(monkeypatch):
    """Most of the events a busy tree fires are in .git, .venv and
    __pycache__. The ignore rules are pure string checks, so such an event
    must be dropped before it costs a stat."""
    from pathlib import Path

    stats: list[str] = []
    real_stat = Path.stat

    def counting_stat(self, *a, **k):
        stats.append(str(self))
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", counting_stat)
    idx = _indexer_over(["src/app.py"])
    for p in ("/ws/repo/.git/index.lock",
              "/ws/repo/.venv/lib/site.py",
              "/ws/src/__pycache__/app.cpython-313.pyc.4242",
              "/ws/src/app.cpython-313.pyc",
              "/ws/node_modules/x/index.js"):
        idx._add(p)
        idx._remove(p)
    assert stats == []
    assert idx.paths == ["src/app.py"]
