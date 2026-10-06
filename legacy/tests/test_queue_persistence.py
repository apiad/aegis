from __future__ import annotations

import json

from aegis.queue.jsonl import append_record, read_records


def test_append_and_read_records_roundtrip(tmp_path):
    p = tmp_path / "q.jsonl"
    append_record(p, {"event": "enqueued", "task_id": "01J42"})
    append_record(p, {"event": "dispatched", "task_id": "01J42"})
    records = read_records(p)
    assert len(records) == 2
    for r in records:
        assert r["v"] == 1
    assert records[0]["event"] == "enqueued"
    assert records[1]["event"] == "dispatched"


def test_read_records_missing_file_returns_empty(tmp_path):
    assert read_records(tmp_path / "nope.jsonl") == []


def test_v2_records_pass_through_for_forward_compat(tmp_path):
    p = tmp_path / "q.jsonl"
    # Hand-write a v=2 record with an extra field
    p.write_text(json.dumps(
        {"v": 2, "event": "enqueued", "task_id": "01J99",
         "future_field": "ignored-by-v1"}) + "\n")
    records = read_records(p)
    assert len(records) == 1 and records[0]["v"] == 2
    assert records[0]["future_field"] == "ignored-by-v1"


def test_append_creates_parent_dir(tmp_path):
    p = tmp_path / "deeper" / "x.jsonl"
    append_record(p, {"event": "ok"})
    assert p.exists()
