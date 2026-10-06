from aegis.bench.frames import (
    SYNC_BEGIN, SYNC_END, SYNC_QUERY, FrameSplitter, find_markers, locate,
    marker, render_screen, sgr_click, strip_ansi)


def test_locate_finds_row_and_column_after_cursor_move():
    # The shape Textual writes: a cursor move, styled padding, the text.
    raw = (b"\x1b[1;1Hheader\x1b[39;1H\x1b[48;2;20;20;18m  \x1b[0m"
           b"\x1b[38;2;1;1;1mtype a message\xe2\x80\xa6\x1b[0m")
    assert locate(raw, "type a message") == (39, 3)
    assert locate(raw, "header") == (1, 1)
    assert locate(raw, "absent") is None


def test_locate_prefers_the_last_draw():
    raw = b"\x1b[5;1Hxx target\x1b[9;4Htarget"
    assert locate(raw, "target") == (9, 4)


def test_render_screen_overlays_spans_in_draw_order():
    raws = [b"\x1b[1;1Hhello world",
            b"\x1b[1;7H\x1b[1mthere\x1b[0m\x1b[2;3Hbold"]
    screen = render_screen(raws, cols=20, rows=3)
    assert screen[0] == "hello there".ljust(20)
    assert screen[1] == "  bold".ljust(20)
    assert screen[2] == " " * 20


def test_render_screen_clips_to_the_grid():
    screen = render_screen([b"\x1b[2;18Hoverflow", b"\x1b[9;1Hgone"],
                           cols=20, rows=3)
    assert screen[1] == " " * 17 + "ove"
    assert all(len(line) == 20 for line in screen)


def test_sgr_click_is_press_then_release():
    assert sgr_click(39, 3) == b"\x1b[<0;3;39M\x1b[<0;3;39m"


def test_frames_keep_their_raw_bytes_for_locating():
    s = FrameSplitter()
    (f,) = s.feed(b"\x1b[7;2Hhi" + SYNC_END, 1)
    assert locate(f.raw, "hi") == (7, 2)


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
