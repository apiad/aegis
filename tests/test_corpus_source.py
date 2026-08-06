"""Reading a session log merged with its backfill sidecar.

The sidecar exists because the ledger was blind to operator turns for
months: `UserMessage` is claude's `--replay-user-messages` echo, and that
flag landed only weeks ago. The merge happens here, at read time, so an
existing log — the only copy of that conversation — is never rewritten.
"""
import json
from pathlib import Path

from aegis.corpus.source import derive_source, exchanges_for_log, read_log


def _write(p: Path, records):
    p.write_text("".join(json.dumps(r) + "\n" for r in records))


def test_derive_source_classifies_substrate_headers():
    assert derive_source("> from monitor:ABC · ok") == ("monitor", "ABC")
    assert derive_source("> from agent:peer-x · hi") == ("agent", "peer-x")
    assert derive_source("> from queue:build · done") == ("queue", "build")
    assert derive_source("> from loop · iteration 3/20") == ("loop", None)
    assert derive_source("<task-notification>x</task-notification>") == ("harness", None)
    assert derive_source("fix the geocoder") == ("operator", None)


def test_derive_source_mutation_guard():
    """A classifier that returned 'operator' unconditionally must fail here."""
    header = "> from monitor:ABC · ok"
    assert derive_source(header)[0] == "monitor"
    assert derive_source(header.replace("> from ", ""))[0] == "operator"


def test_sidecar_records_are_merged_in_timestamp_order(tmp_path):
    sessions = tmp_path / "sessions"; sessions.mkdir()
    backfill = tmp_path / "backfill"; backfill.mkdir()
    log = sessions / "s1.jsonl"
    _write(log, [
        {"v": 1, "aegis_ts": "2026-07-01T10:00:00Z",
         "event": {"t": "SessionMeta", "handle": "h1", "cwd": "/w"}},
        {"v": 1, "aegis_ts": "2026-07-01T10:00:10Z",
         "event": {"t": "AssistantText", "text": "answer"}},
    ])
    _write(backfill / "s1.jsonl", [
        {"v": 1, "aegis_ts": "2026-07-01T10:00:05Z",
         "event": {"t": "UserMessage", "text": "question", "source": "operator"}},
    ])
    events, meta = read_log(log, backfill)
    kinds = [e["t"] for e, _ in events]
    assert kinds == ["SessionMeta", "UserMessage", "AssistantText"]
    assert meta["handle"] == "h1"


def test_exchanges_for_log_pairs_sidecar_question_with_log_answer(tmp_path):
    sessions = tmp_path / "sessions"; sessions.mkdir()
    backfill = tmp_path / "backfill"; backfill.mkdir()
    log = sessions / "s1.jsonl"
    _write(log, [
        {"v": 1, "aegis_ts": "2026-07-01T10:00:00Z",
         "event": {"t": "SessionMeta", "handle": "h1", "cwd": "/w"}},
        {"v": 1, "aegis_ts": "2026-07-01T10:00:10Z",
         "event": {"t": "AssistantText", "text": "answer"}},
    ])
    _write(backfill / "s1.jsonl", [
        {"v": 1, "aegis_ts": "2026-07-01T10:00:05Z",
         "event": {"t": "UserMessage", "text": "question", "source": "operator"}},
    ])
    ex = exchanges_for_log(log, backfill)
    assert len(ex) == 1
    assert ex[0].operator_text == "question"
    assert ex[0].assistant_text == "answer"


def test_handle_falls_back_to_the_log_filename(tmp_path):
    """Only 28 of 60 real logs carry a SessionMeta record, so half the
    corpus would index with handle=None and an `?@<ts>` key. The log id
    *is* `<birthtime>-<handle>`, so the name answers it — and `parse_log_id`
    already tolerates a legacy bare-handle filename.
    """
    sessions = tmp_path / "sessions"; sessions.mkdir()
    log = sessions / "20260701T100000123456Z-lucid-knuth.jsonl"
    _write(log, [{"v": 1, "aegis_ts": "2026-07-01T10:00:00Z",
                  "event": {"t": "UserMessage", "text": "hi",
                            "source": "operator"}}])
    _events, meta = read_log(log, None)
    assert meta["handle"] == "lucid-knuth"
    assert exchanges_for_log(log, None)[0].handle == "lucid-knuth"


def test_a_session_meta_record_still_wins_over_the_filename(tmp_path):
    """The record is the authority when present — a rename appends one, so
    it is more current than the birth handle baked into the name."""
    sessions = tmp_path / "sessions"; sessions.mkdir()
    log = sessions / "20260701T100000123456Z-birth-name.jsonl"
    _write(log, [{"v": 1, "aegis_ts": "2026-07-01T10:00:00Z",
                  "event": {"t": "SessionMeta", "handle": "renamed",
                            "cwd": "/w"}}])
    _events, meta = read_log(log, None)
    assert meta["handle"] == "renamed"


def test_missing_sidecar_is_not_an_error(tmp_path):
    sessions = tmp_path / "sessions"; sessions.mkdir()
    log = sessions / "s2.jsonl"
    _write(log, [{"v": 1, "aegis_ts": "2026-07-01T10:00:00Z",
                  "event": {"t": "UserMessage", "text": "hi", "source": "operator"}}])
    assert len(exchanges_for_log(log, tmp_path / "nope")) == 1
