"""A terminal's output store: asciicast v2, two segments, a memory ring."""

import json

from aegis.term_store import CastStore

HEAD = {"id": "t-20261014-101500-a1b2c3", "profile": "shell"}


def store(tmp_path, **kw):
    s = CastStore(tmp_path / "t", **kw)
    s.open(120, 40, HEAD)
    return s


def test_events_are_numbered_and_written_as_asciicast_v2(tmp_path):
    s = store(tmp_path)
    assert s.output("hi\r\n") == 1
    assert s.resize(132, 38) == 2
    assert s.marker("exit 0") == 3
    s.flush()
    head, *events = [json.loads(line) for line in s.path.read_text().splitlines()]
    assert head["version"] == 2 and (head["width"], head["height"]) == (120, 40)
    assert head["aegis"] == HEAD | {"first": 1}
    assert [e[1:] for e in events] == [
        ["o", "hi\r\n"],
        ["r", "132x38"],
        ["m", "exit 0"],
    ]
    assert all(isinstance(e[0], float) for e in events)


def test_since_returns_what_came_after_or_none_when_the_ring_lost_it(tmp_path):
    s = store(tmp_path, ring_bytes=100)
    for i in range(50):
        s.output(f"line {i:02d}\r\n")  # 9 characters each
    assert [e[0] for e in s.since(45)] == [46, 47, 48, 49, 50]
    assert s.since(50) == []
    assert s.since(3) is None
    assert s.since(51) is None


def test_the_store_rolls_and_keeps_two_segments(tmp_path):
    s = store(tmp_path, segment_bytes=4096)
    for _ in range(1000):
        s.output("x" * 30 + "\r\n")
    s.flush()
    assert sorted(p.name for p in s.dir.iterdir()) == ["cast.1.jsonl", "cast.jsonl"]
    assert all(p.stat().st_size < 4096 + 100 for p in s.dir.iterdir())
    lines = s.path.read_text().splitlines()
    first = json.loads(lines[0])["aegis"]["first"]
    assert first + len(lines) - 2 == s.rev == 1000


def test_a_reopened_store_recovers_rev_and_ring_from_the_files(tmp_path):
    s = store(tmp_path, segment_bytes=4096)
    for i in range(300):
        s.output(f"{i}\r\n")
    s.close()
    with s.path.open("a") as f:
        f.write('[1.0, "o", "cut by a cra')  # a crash in the middle of a line
    again = CastStore(tmp_path / "t", segment_bytes=4096)
    assert again.since(298) == [(299, "o", "298\r\n"), (300, "o", "299\r\n")]
    again.open(120, 40, HEAD)
    assert again.output("next\r\n") == 301
    again.flush()
    assert json.loads(again.path.read_text().splitlines()[-1])[2] == "next\r\n"


def test_the_tail_starts_at_the_last_screen_clear(tmp_path):
    s = store(tmp_path)
    s.output("old prompt\r\n")
    s.output("\x1b[?1049h\x1b[Hvim screen")
    s.output(" more")
    assert s.tail() == "\x1b[?1049h\x1b[Hvim screen more"


def test_a_long_tail_is_cut_at_a_line_start(tmp_path):
    s = store(tmp_path)
    for i in range(1000):
        s.output(f"row {i:04d}\r\n")  # 10 characters a row
    t = s.tail(limit=95)
    assert t.startswith("row ") and t.endswith("row 0999\r\n") and len(t) <= 95


def test_a_cut_with_no_newline_starts_at_the_next_escape_not_inside_one(tmp_path):
    s = store(tmp_path)
    s.output("a" * 10 + "\x1b[31m" + "b" * 10 + "\x1b[0m" + "c" * 10)
    assert s.tail(limit=27) == "\x1b[0m" + "c" * 10  # the limit cuts \x1b[31m


def test_a_full_screen_program_that_quit_does_not_clear_the_tail(tmp_path):
    s = store(tmp_path)
    s.output("$ ls\r\nREADME\r\n$ vim\r\n")
    s.output("\x1b[?1049h\x1b[2J\x1b[Hvim screen")
    s.output("\x1b[?1049l$ ")
    assert s.tail().startswith("$ ls\r\nREADME")
    s.output("\x1b[2Jcleared")
    assert s.tail() == "\x1b[2Jcleared"
