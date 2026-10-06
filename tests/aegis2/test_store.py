from pathlib import Path

from aegis2.transcript.store import Store, read_store


def test_append_numbers_records(tmp_path: Path):
    s = Store(tmp_path / "t" / "log.jsonl")
    assert [s.append({"src": "aegis2", "kind": "x"})["i"] for _ in range(3)] == [
        0,
        1,
        2,
    ]
    s.close()
    records, damaged = read_store(tmp_path / "t" / "log.jsonl")
    assert [r["i"] for r in records] == [0, 1, 2] and damaged == 0


def test_damaged_lines_are_skipped_and_counted(tmp_path: Path):
    p = tmp_path / "log.jsonl"
    p.write_text(
        '{"i": 0, "src": "aegis2", "kind": "spawn"}\n'
        "{not json\n"
        '{"i": 2, "src": "claude", "line": "{}"}\n'
        '["a list"]\n'
        '{"i": 4, "src": "claude", "li'
    )
    records, damaged = read_store(p)
    assert [r["i"] for r in records] == [0, 2]
    assert damaged == 3
