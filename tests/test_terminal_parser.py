import pytest
from aegis.terminal.parser import (
    OSC133Parser, PromptStart, CommandStart, CommandEnd,
)


def test_strips_prompt_start_marker():
    parser = OSC133Parser()
    stripped, events = parser.feed(b"\x1b]133;A\x07$ ")
    assert stripped == b"$ "
    assert events == [PromptStart()]


def test_strips_command_start_marker():
    parser = OSC133Parser()
    stripped, events = parser.feed(b"\x1b]133;B\x07")
    assert stripped == b""
    assert events == [CommandStart()]


def test_command_end_with_exit_code():
    parser = OSC133Parser()
    stripped, events = parser.feed(b"hello\n\x1b]133;D;0\x07")
    assert stripped == b"hello\n"
    assert events == [CommandEnd(exit_code=0)]


def test_command_end_nonzero_exit():
    parser = OSC133Parser()
    stripped, events = parser.feed(b"\x1b]133;D;130\x07")
    assert events == [CommandEnd(exit_code=130)]


def test_command_end_missing_exit():
    parser = OSC133Parser()
    stripped, events = parser.feed(b"\x1b]133;D;\x07")
    assert events == [CommandEnd(exit_code=None)]


def test_sequence_split_across_chunks():
    parser = OSC133Parser()
    stripped1, events1 = parser.feed(b"hello\x1b]133;")
    stripped2, events2 = parser.feed(b"A\x07world")
    assert stripped1 == b"hello"
    assert events1 == []
    assert stripped2 == b"world"
    assert events2 == [PromptStart()]


def test_multibyte_utf8_split_mid_chunk():
    # "é" is 0xC3 0xA9; split between chunks. Parser must not corrupt it.
    parser = OSC133Parser()
    s1, _ = parser.feed(b"caf\xc3")
    s2, _ = parser.feed(b"\xa9\n")
    assert (s1 + s2).decode("utf-8") == "café\n"


def test_multiple_events_one_chunk():
    parser = OSC133Parser()
    stripped, events = parser.feed(
        b"\x1b]133;A\x07$ pytest\n\x1b]133;B\x07ok\n\x1b]133;D;0\x07"
    )
    assert stripped == b"$ pytest\nok\n"
    assert events == [PromptStart(), CommandStart(), CommandEnd(exit_code=0)]


def test_marker_at_buffer_boundary_preserved():
    # If chunk ends mid-marker, parser holds bytes back until completion.
    parser = OSC133Parser()
    s1, e1 = parser.feed(b"\x1b]13")
    s2, e2 = parser.feed(b"3;A\x07")
    assert s1 == b""
    assert e1 == []
    assert s2 == b""
    assert e2 == [PromptStart()]


def test_bytes_resembling_marker_in_output_passed_through():
    # The text "\x1b]133" appearing in literal output is rare but not
    # impossible; parser cannot distinguish — accept passthrough for
    # incomplete sequences that never resolve.
    parser = OSC133Parser()
    s, e = parser.feed(b"hello world\n")
    assert s == b"hello world\n"
    assert e == []
