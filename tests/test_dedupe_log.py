"""Collapsing the records N writers left behind, without eating real ones.

Two observers wrote every event while a pane added a second writer to a
session the manager already logged, so a transcript holds the same record
two or three times and the replay renders it that many times. The fix
stops it happening; these logs already have it.

The rule has to separate that from an agent genuinely saying the same
thing twice, and a naive content match does not: a `SystemInit` reappears
whenever a session resumes, and `ContextUpdate` repeats by design. Over
Alex's 817 logs a content-only match flagged 375 repeats, of which 224
were legitimate.

What identifies the accident is that one `_fire_event` call drove every
writer, so the copies are ADJACENT and written within the same instant.
Measured across his corpus: 151 adjacent identical pairs, the widest gap
341ms, median under 1ms, none above a second. A genuine repeat costs an
agent round trip. So: adjacent, byte-identical event, inside a one-second
window, which is three times the widest gap ever seen here.

Nothing is deleted. The original is kept beside the log, the same rule
`repair_log` follows for damaged records.
"""
from __future__ import annotations

import json

from aegis.state.repair import dedupe_log


def _write(path, records):
    path.write_text("".join(
        json.dumps(r, separators=(",", ":")) + "\n" for r in records))


def _rec(ts: str, payload: dict) -> dict:
    return {"v": 1, "aegis_ts": ts, "event": payload}


def test_adjacent_copies_written_in_the_same_instant_collapse(tmp_path):
    log = tmp_path / "s.jsonl"
    _write(log, [
        _rec("2026-09-13T10:00:00.000000Z", {"t": "AssistantText", "text": "hi"}),
        _rec("2026-09-13T10:00:00.000900Z", {"t": "AssistantText", "text": "hi"}),
        _rec("2026-09-13T10:00:00.001800Z", {"t": "AssistantText", "text": "hi"}),
    ])

    report = dedupe_log(log)

    kept = [json.loads(x) for x in log.read_text().splitlines()]
    assert len(kept) == 1, f"three writers left {len(kept)} records"
    assert report.removed == 2
    assert report.backup is not None and report.backup.exists()
    assert len(report.backup.read_text().splitlines()) == 3, \
        "the original must survive intact beside the log"


def test_the_same_text_said_twice_is_left_alone(tmp_path):
    """An agent repeating itself a minute later. Identical bytes, and not
    ours to remove."""
    log = tmp_path / "s.jsonl"
    _write(log, [
        _rec("2026-09-13T10:00:00.000000Z", {"t": "AssistantText", "text": "ok"}),
        _rec("2026-09-13T10:01:00.000000Z", {"t": "AssistantText", "text": "ok"}),
    ])

    report = dedupe_log(log)

    assert report.removed == 0
    assert len(log.read_text().splitlines()) == 2
    assert report.backup is None, "a clean log must not be rewritten"


def test_a_repeat_separated_by_another_event_is_left_alone(tmp_path):
    """`SystemInit` on resume, the case that made a content-only match
    wrong. Adjacency is what distinguishes them."""
    log = tmp_path / "s.jsonl"
    init = {"t": "SystemInit", "session_id": "s"}
    _write(log, [
        _rec("2026-09-13T10:00:00.000000Z", init),
        _rec("2026-09-13T10:00:00.000500Z", {"t": "AssistantText", "text": "x"}),
        _rec("2026-09-13T10:00:00.001000Z", init),
    ])

    report = dedupe_log(log)

    assert report.removed == 0
    assert len(log.read_text().splitlines()) == 3


def test_an_unparseable_record_breaks_the_run(tmp_path):
    """Damage is repair_log's job, not this one's. A record we cannot read
    is not a record we may declare equal to its neighbour."""
    log = tmp_path / "s.jsonl"
    payload = {"t": "AssistantText", "text": "hi"}
    log.write_text(
        json.dumps(_rec("2026-09-13T10:00:00.000000Z", payload)) + "\n"
        + "{ not json\n"
        + json.dumps(_rec("2026-09-13T10:00:00.000400Z", payload)) + "\n")

    report = dedupe_log(log)

    assert report.removed == 0
    assert len(log.read_text().splitlines()) == 3, \
        "a line we could not parse was dropped"


def test_a_clean_log_is_not_touched(tmp_path):
    log = tmp_path / "s.jsonl"
    _write(log, [_rec("2026-09-13T10:00:00.000000Z",
                      {"t": "AssistantText", "text": f"line {i}"})
                 for i in range(5)])
    before = log.read_text()

    report = dedupe_log(log)

    assert report.removed == 0
    assert log.read_text() == before
    assert list(tmp_path.glob("*.dup*")) == []


def test_running_it_twice_changes_nothing_the_second_time(tmp_path):
    log = tmp_path / "s.jsonl"
    _write(log, [
        _rec("2026-09-13T10:00:00.000000Z", {"t": "AssistantText", "text": "hi"}),
        _rec("2026-09-13T10:00:00.000900Z", {"t": "AssistantText", "text": "hi"}),
    ])

    assert dedupe_log(log).removed == 1
    after = log.read_text()
    assert dedupe_log(log).removed == 0
    assert log.read_text() == after


def test_an_event_with_no_content_is_never_collapsed(tmp_path):
    """`ContextUpdate` is written as a bare `{"t": ...}`. Two of them in
    the same millisecond are indistinguishable from one emitted twice, and
    a log predating this bug had ten such pairs. What cannot be shown to be
    a copy stays, and nothing is lost by that: an event with no content
    renders to nothing."""
    log = tmp_path / "s.jsonl"
    _write(log, [
        _rec("2026-09-13T10:00:00.000000Z", {"t": "ContextUpdate"}),
        _rec("2026-09-13T10:00:00.002200Z", {"t": "ContextUpdate"}),
    ])

    report = dedupe_log(log)

    assert report.removed == 0
    assert len(log.read_text().splitlines()) == 2
