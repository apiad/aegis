"""The wire between a view and its client.

Deliberately Textual's own packet format rather than one of ours: one byte
of type, four bytes of big-endian length, then the payload. ``WebDriver``
emits it on the way out (`web_driver.py:80-86`) and consumes it on the way
in (`:193-199`), so a transport that speaks it needs no translation layer
in either direction -- and a translation layer is precisely where
``--remote``'s protocol rot started.

Both transports import this module. That is what makes them byte-identical
rather than intended to be.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator


class ProtocolError(Exception):
    """A client sent something malformed. Always fatal to that connection,
    never to the daemon."""


def encode_data(payload: bytes) -> bytes:
    return b"D" + len(payload).to_bytes(4, "big") + payload


def encode_meta(obj: dict) -> bytes:
    raw = json.dumps(obj).encode("utf-8")
    return b"M" + len(raw).to_bytes(4, "big") + raw


def hello(view_id: str, width: int, height: int) -> bytes:
    """The client's first frame: which view, at what size.

    A meta frame rather than a bespoke preamble, so the stream has exactly
    one shape from the first byte. 5b's auth frame is the same trick.
    """
    return encode_meta({"type": "hello", "view_id": view_id,
                        "width": width, "height": height})


def resize(width: int, height: int) -> bytes:
    return encode_meta({"type": "resize", "width": width, "height": height})


def parse_hello(payload: bytes) -> tuple[str, int, int]:
    try:
        obj = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ProtocolError(f"hello is not json: {e}") from e
    if not isinstance(obj, dict) or obj.get("type") != "hello":
        raise ProtocolError("first frame is not a hello")
    view_id = obj.get("view_id")
    width = obj.get("width")
    height = obj.get("height")
    if not isinstance(view_id, str) or not view_id:
        raise ProtocolError("hello has no view_id")
    # The id names a file under state_dir/views (views/state.py:_path). A
    # bool is an int in Python, so check the type before the range.
    if view_id != Path(view_id).name or view_id in (".", ".."):
        raise ProtocolError(f"invalid view id: {view_id!r}")
    for name, value in (("width", width), ("height", height)):
        if type(value) is not int or value < 1:
            raise ProtocolError(f"hello has a bad {name}: {value!r}")
    return view_id, width, height


class FrameDecoder:
    """Reassembles frames from a stream that splits wherever it likes.

    Textual's own ``ByteStream`` does this, but it is a private module
    (`textual.drivers._byte_stream`) and the client is a standalone process
    that should not import a TUI framework to pipe bytes. Twelve lines
    is the cheaper dependency.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> Iterator[tuple[str, bytes]]:
        self._buf.extend(data)
        while len(self._buf) >= 5:
            size = int.from_bytes(self._buf[1:5], "big")
            if len(self._buf) < 5 + size:
                return
            kind = chr(self._buf[0])
            payload = bytes(self._buf[5:5 + size])
            del self._buf[:5 + size]
            yield kind, payload
