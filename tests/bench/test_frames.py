from aegis.bench.frames import (
    SYNC_BEGIN, SYNC_END, SYNC_QUERY, FrameSplitter, find_markers, marker,
    strip_ansi)


def test_marker_format_round_trips():
    assert marker(7) == "«b0007»"
    assert find_markers("x «b0007» y «b12345»") == ["«b0007»", "«b12345»"]


def test_strip_ansi_keeps_text_through_sgr_cursor_and_osc():
    raw = (b"\x1b[1;5H\x1b[38;2;10;20;30mline \xc2\xabb0001\xc2\xbb\x1b[0m"
           b"\x1b]0;title\x07\x1b(B\x1b7")
    assert strip_ansi(raw) == "line «b0001»"


def test_frames_split_on_sync_end_across_chunks():
    s = FrameSplitter()
    assert s.feed(SYNC_BEGIN + b"hello \xc2\xab", 1) == []
    frames = s.feed(b"b0001\xc2\xbb" + SYNC_END[:3], 2)
    assert frames == []
    frames = s.feed(SYNC_END[3:] + SYNC_BEGIN + b"next", 3)
    assert [f.text for f in frames] == ["hello «b0001»"]
    assert frames[0].t_ns == 3
    assert s.saw_sync_begin


def test_query_is_counted_even_when_split_and_removed_from_frames():
    s = FrameSplitter()
    s.feed(b"a" + SYNC_QUERY[:4], 1)
    s.feed(SYNC_QUERY[4:] + b"b" + SYNC_END, 2)
    assert s.queries == 1
    assert s.saw_sync_begin is False


def test_frame_nbytes_counts_raw_bytes():
    s = FrameSplitter()
    (f,) = s.feed(b"\x1b[1mab" + SYNC_END, 5)
    assert f.nbytes == len(b"\x1b[1mab")
