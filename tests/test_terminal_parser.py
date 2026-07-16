from aegis.terminal.parser import (
    OSC133Parser, PromptStart, CommandStart, CommandOutputStart, CommandEnd,
    split_segments,
)


def _feed(parser, chunk):
    """feed() now returns ordered segments; most tests want the bulk view."""
    return split_segments(parser.feed(chunk))


def test_strips_prompt_start_marker():
    parser = OSC133Parser()
    stripped, events = _feed(parser, b"\x1b]133;A\x07$ ")
    assert stripped == b"$ "
    assert events == [PromptStart()]


def test_strips_command_start_marker():
    parser = OSC133Parser()
    stripped, events = _feed(parser, b"\x1b]133;B\x07")
    assert stripped == b""
    assert events == [CommandStart()]


def test_command_end_with_exit_code():
    parser = OSC133Parser()
    stripped, events = _feed(parser, b"hello\n\x1b]133;D;0\x07")
    assert stripped == b"hello\n"
    assert events == [CommandEnd(exit_code=0)]


def test_command_end_nonzero_exit():
    parser = OSC133Parser()
    stripped, events = _feed(parser, b"\x1b]133;D;130\x07")
    assert events == [CommandEnd(exit_code=130)]


def test_command_end_missing_exit():
    parser = OSC133Parser()
    stripped, events = _feed(parser, b"\x1b]133;D;\x07")
    assert events == [CommandEnd(exit_code=None)]


def test_sequence_split_across_chunks():
    parser = OSC133Parser()
    stripped1, events1 = _feed(parser, b"hello\x1b]133;")
    stripped2, events2 = _feed(parser, b"A\x07world")
    assert stripped1 == b"hello"
    assert events1 == []
    assert stripped2 == b"world"
    assert events2 == [PromptStart()]


def test_multibyte_utf8_split_mid_chunk():
    # "é" is 0xC3 0xA9; split between chunks. Parser must not corrupt it.
    parser = OSC133Parser()
    s1, _ = _feed(parser, b"caf\xc3")
    s2, _ = _feed(parser, b"\xa9\n")
    assert (s1 + s2).decode("utf-8") == "café\n"


def test_multiple_events_one_chunk():
    parser = OSC133Parser()
    stripped, events = _feed(
        parser,
        b"\x1b]133;A\x07$ pytest\n\x1b]133;B\x07ok\n\x1b]133;D;0\x07",
    )
    assert stripped == b"$ pytest\nok\n"
    assert events == [PromptStart(), CommandStart(), CommandEnd(exit_code=0)]


def test_marker_at_buffer_boundary_preserved():
    # If chunk ends mid-marker, parser holds bytes back until completion.
    parser = OSC133Parser()
    s1, e1 = _feed(parser, b"\x1b]13")
    s2, e2 = _feed(parser, b"3;A\x07")
    assert s1 == b""
    assert e1 == []
    assert s2 == b""
    assert e2 == [PromptStart()]


def test_bytes_resembling_marker_in_output_passed_through():
    parser = OSC133Parser()
    s, e = _feed(parser, b"hello world\n")
    assert s == b"hello world\n"
    assert e == []


# --- ST (ESC \) terminator, C marker, and ordered interleaving --------


def test_st_terminated_osc_and_c_marker():
    # starship/VTE terminate ]133;C with ST, not BEL. The parser must not
    # swallow the following D marker (the bug that broke run() on zion).
    parser = OSC133Parser()
    raw = (b"\x1b]133;C\x1b\\hi\r\n\x1b]133;D;0\x07")
    stripped, events = _feed(parser, raw)
    assert stripped == b"hi\r\n"
    assert events == [CommandOutputStart(), CommandEnd(exit_code=0)]


def test_non_133_osc_is_stripped_without_event():
    # VTE ]666 / title ]0 / cwd ]7 sequences must be stripped, not leaked.
    parser = OSC133Parser()
    raw = b"\x1b]666;vte.x\x1b\\out\x1b]0;title\x07more"
    stripped, events = _feed(parser, raw)
    assert stripped == b"outmore"
    assert events == []


def test_segments_preserve_output_event_ordering():
    # The whole tail in one chunk: B, redraw, C, output, D. Ordering must
    # be preserved so a consumer can reset on C without losing the output.
    parser = OSC133Parser()
    raw = (b"\x1b]133;B\x07echo\x1b]133;C\x1b\\hi\r\n\x1b]133;D;0\x07")
    segs = parser.feed(raw)
    kinds = [type(s).__name__ if not isinstance(s, (bytes, bytearray))
             else s for s in segs]
    assert kinds == [
        CommandStart.__name__, b"echo", CommandOutputStart.__name__,
        b"hi\r\n", CommandEnd.__name__,
    ]
