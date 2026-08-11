"""The envelope round-trips, and a torn ledger degrades instead of raising."""
from __future__ import annotations

from pathlib import Path

from aegis.comms.descriptors import CONVERSATION, Target
from aegis.comms.models import Envelope
from aegis.comms.persistence import CommsLedger


def _env(**over) -> Envelope:
    base = dict(call_id="01K4TZ", ts="2026-08-11T14:22:07Z",
                from_handle="aegis-call-format",
                to=Target("agent", "weary-turing"), family=CONVERSATION,
                verb="handoff", thread="01K4TZ", outcome="ok",
                duration_ms=41)
    base.update(over)
    return Envelope(**base)


def test_the_record_is_flat_json_with_a_typed_target():
    rec = _env().to_record()
    assert rec["from"] == "aegis-call-format"
    assert rec["to"] == {"kind": "agent", "id": "weary-turing"}
    assert rec["verb"] == "handoff"
    assert rec["outcome"] == "ok"
    assert rec["duration_ms"] == 41


def test_an_absent_target_serialises_as_null_not_a_missing_key():
    rec = _env(to=None).to_record()
    assert "to" in rec and rec["to"] is None


def test_an_unattributed_call_keeps_an_explicit_empty_from():
    """from_handle is a convention, not a transport fact. A call without one
    is recorded unattributed rather than guessed at."""
    rec = _env(from_handle="").to_record()
    assert rec["from"] == ""


def test_write_then_read_round_trips(tmp_path: Path):
    ledger = CommsLedger(tmp_path)
    ledger.write(_env())
    ledger.write(_env(call_id="01K4U0", verb="enqueue"))
    rows = ledger.read_all()
    assert [r["verb"] for r in rows] == ["handoff", "enqueue"]
    assert rows[0]["v"] == 1


def test_the_day_file_is_named_for_the_envelope_timestamp(tmp_path: Path):
    ledger = CommsLedger(tmp_path)
    ledger.write(_env(ts="2026-08-11T14:22:07Z"))
    assert (tmp_path / "comms" / "2026-08-11.jsonl").is_file()


def test_a_torn_trailing_line_is_skipped_not_raised(tmp_path: Path):
    ledger = CommsLedger(tmp_path)
    ledger.write(_env())
    ledger.write(_env(call_id="01K4U0", verb="enqueue"))
    path = ledger.path("2026-08-11")
    with path.open("a", encoding="utf-8") as f:
        f.write('{"v":1,"call_id":"01K4U1","verb":"cla')
    rows = ledger.read_all()
    assert [r["verb"] for r in rows] == ["handoff", "enqueue"]


def test_reading_a_ledger_that_does_not_exist_yet_is_empty(tmp_path: Path):
    assert CommsLedger(tmp_path).read_all() == []
