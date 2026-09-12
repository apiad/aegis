"""The wire. One module both ends import, so they cannot disagree."""
import json

import pytest

from aegis.daemon.protocol import (
    FrameDecoder, ProtocolError, encode_data, encode_meta, hello,
    parse_hello, resize,
)


def test_a_data_frame_is_textuals_own_shape():
    """Not our codec. WebDriver.write emits exactly this (web_driver.py:85),
    and the whole design rests on the two being the same bytes."""
    assert encode_data(b"hi") == b"D" + (2).to_bytes(4, "big") + b"hi"


def test_a_meta_frame_is_json_under_an_M():
    frame = encode_meta({"type": "resize", "width": 80, "height": 24})
    assert frame[:1] == b"M"
    assert int.from_bytes(frame[1:5], "big") == len(frame) - 5
    assert json.loads(frame[5:]) == {"type": "resize", "width": 80,
                                     "height": 24}


def test_round_trip():
    d = FrameDecoder()
    stream = encode_data(b"abc") + encode_meta({"type": "blur"})
    assert list(d.feed(stream)) == [("D", b"abc"),
                                    ("M", b'{"type": "blur"}')]


def test_a_frame_split_across_chunks_is_reassembled():
    d = FrameDecoder()
    stream = encode_data(b"abcdef")
    out = []
    for i in range(len(stream)):
        out.extend(d.feed(stream[i:i + 1]))
    assert out == [("D", b"abcdef")]


def test_two_frames_in_one_chunk_both_come_out():
    d = FrameDecoder()
    out = list(d.feed(encode_data(b"a") + encode_data(b"b")))
    assert out == [("D", b"a"), ("D", b"b")]


def test_hello_round_trips():
    assert parse_hello(hello("tty-dev-pts-3", 120, 40)[5:]) == (
        "tty-dev-pts-3", 120, 40)


def test_resize_is_the_meta_shape_textual_already_understands():
    """WebDriver.on_meta reads payload['width'] / ['height']
    (web_driver.py:243-245). A different key name silently never resizes."""
    assert json.loads(resize(120, 40)[5:]) == {
        "type": "resize", "width": 120, "height": 40}


@pytest.mark.parametrize("payload", [
    b"not json",
    b'{"type": "hello"}',                        # no view_id
    b'{"type": "hello", "view_id": "a"}',        # no geometry
    b'{"type": "resize", "view_id": "a", "width": 1, "height": 1}',
    b'{"type": "hello", "view_id": "../etc", "width": 1, "height": 1}',
    b'{"type": "hello", "view_id": "a", "width": 0, "height": 1}',
    b'{"type": "hello", "view_id": "a", "width": "80", "height": 24}',
])
def test_a_malformed_hello_raises(payload):
    """The first frame is the only one a client can send before it owns a
    view, so it is the one that gets read adversarially."""
    with pytest.raises(ProtocolError):
        parse_hello(payload)
