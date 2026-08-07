"""Ctrl+R must not re-read the whole corpus every time.

Folding a log to a history row reads and decodes every record in it. On
the operator's state dir (232 logs, 615 MB) that measured 25s warm and 60s
cold. Logs are append-only, so a sidecar index keyed on (size, mtime) lets
a listing re-fold only what actually changed — in practice the handful of
live tabs.
"""
from __future__ import annotations

from pathlib import Path

from aegis.events import AssistantText, SessionMeta
from aegis.state.history import list_history
from aegis.state.session_log import append_event


def _meta(handle: str, preview: str = "hello",
          title: str = "", title_source: str = "") -> SessionMeta:
    return SessionMeta(handle=handle, profile="default",
                       provider="claude-code", cwd="/tmp",
                       created_at="2026-07-29T00:00:00Z", origin="user",
                       preview=preview, title=title,
                       title_source=title_source)


def _log(state_dir: Path, log_id: str, handle: str, n: int = 5) -> None:
    append_event(state_dir, log_id, _meta(handle))
    for i in range(n):
        append_event(state_dir, log_id, AssistantText(f"line {i}"))


def _rows(state_dir: Path):
    return list_history(state_dir, live_handles=set())


def test_second_listing_refolds_nothing(tmp_path, monkeypatch):
    from aegis.state import history as hist

    for i in range(5):
        _log(tmp_path, f"log-{i}", f"handle-{i}")

    first = _rows(tmp_path)
    assert len(first) == 5

    folded: list[Path] = []
    real = hist._fold_file
    monkeypatch.setattr(hist, "_fold_file",
                        lambda p: (folded.append(p), real(p))[1])

    second = _rows(tmp_path)
    assert folded == [], "re-read logs that had not changed"
    assert [r.log_id for r in second] == [r.log_id for r in first]
    assert [r.preview for r in second] == [r.preview for r in first]


def test_a_grown_log_is_refolded(tmp_path, monkeypatch):
    from aegis.state import history as hist

    _log(tmp_path, "log-a", "handle-a")
    _log(tmp_path, "log-b", "handle-b")
    _rows(tmp_path)

    append_event(tmp_path, "log-b", AssistantText("something new"))

    folded: list[Path] = []
    real = hist._fold_file
    monkeypatch.setattr(hist, "_fold_file",
                        lambda p: (folded.append(p), real(p))[1])
    rows = _rows(tmp_path)
    assert [p.stem for p in folded] == ["log-b"]
    assert len(rows) == 2


def test_a_new_log_appears(tmp_path):
    _log(tmp_path, "log-a", "handle-a")
    assert len(_rows(tmp_path)) == 1
    _log(tmp_path, "log-b", "handle-b")
    assert len(_rows(tmp_path)) == 2


def test_a_deleted_log_drops_out(tmp_path):
    _log(tmp_path, "log-a", "handle-a")
    _log(tmp_path, "log-b", "handle-b")
    assert len(_rows(tmp_path)) == 2
    (tmp_path / "sessions" / "log-b.jsonl").unlink()
    rows = _rows(tmp_path)
    assert [r.log_id for r in rows] == ["log-a"]


def test_a_corrupt_index_falls_back_to_a_full_scan(tmp_path):
    """The index is a cache. Losing it costs time, never correctness."""
    _log(tmp_path, "log-a", "handle-a")
    _rows(tmp_path)
    (tmp_path / "history_index.json").write_text("{not json at all")
    rows = _rows(tmp_path)
    assert [r.log_id for r in rows] == ["log-a"]


def test_an_unwritable_state_dir_still_lists(tmp_path, monkeypatch):
    """A read-only state dir must not break Ctrl+R."""
    from aegis.state import history as hist
    _log(tmp_path, "log-a", "handle-a")

    def boom(*a, **kw):
        raise OSError("read-only")

    monkeypatch.setattr(hist, "_save_index", boom)
    assert len(_rows(tmp_path)) == 1


def test_rows_match_what_a_cold_scan_would_produce(tmp_path):
    """The whole point: identical output, less reading."""
    for i in range(4):
        _log(tmp_path, f"log-{i}", f"handle-{i}")
    warm = _rows(tmp_path)          # builds the index
    cached = _rows(tmp_path)        # served from it
    (tmp_path / "history_index.json").unlink()
    cold = _rows(tmp_path)          # full scan again

    def shape(rows):
        return [(r.log_id, r.handle, r.preview, r.last_activity_at,
                 r.closed_at, r.session_id, r.crash_inferred) for r in rows]

    assert shape(cached) == shape(warm) == shape(cold)


def test_title_survives_the_fold_cache_round_trip(tmp_path):
    append_event(tmp_path, "20260807T100000000000Z-lucid-knuth",
                 _meta("lucid-knuth", title="eviction race",
                       title_source="human"))
    append_event(tmp_path, "20260807T100000000000Z-lucid-knuth",
                 AssistantText("hi"))
    first, = _rows(tmp_path)    # cold: folds the log and saves the index
    second, = _rows(tmp_path)   # warm: served straight from that index
    assert second.title == first.title == "eviction race"
    assert second.title_source == "human"


def test_a_stale_index_version_is_discarded(tmp_path):
    from aegis.state.history import INDEX_NAME, INDEX_VERSION, _load_index
    (tmp_path / INDEX_NAME).write_text(
        '{"version": 1, "entries": {"x.jsonl": {"stamp": [0, 0]}}}',
        encoding="utf-8")
    assert INDEX_VERSION > 1
    assert _load_index(tmp_path) == {}
