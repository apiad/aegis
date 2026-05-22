"""OSC 133 escape-sequence parser for live terminals.

Pure byte-level. No I/O, no asyncio. Yields stripped output chunks and
prompt/command-boundary events.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class PromptStart:
    pass


@dataclass(frozen=True)
class CommandStart:
    pass


@dataclass(frozen=True)
class CommandEnd:
    exit_code: int | None


Event = Union[PromptStart, CommandStart, CommandEnd]

_ESC = 0x1B
_BEL = 0x07
_OSC133_PREFIX = b"\x1b]133;"


class OSC133Parser:
    """Stateful parser. Holds back trailing bytes that might be the start
    of an incomplete OSC sequence, so split-across-chunks is safe."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> tuple[bytes, list[Event]]:
        self._buf.extend(chunk)
        out = bytearray()
        events: list[Event] = []
        i = 0
        while i < len(self._buf):
            b = self._buf[i]
            if b != _ESC:
                out.append(b)
                i += 1
                continue
            # Possible OSC 133 sequence. Need at least len(_OSC133_PREFIX)
            # bytes to confirm; otherwise hold back.
            if i + len(_OSC133_PREFIX) > len(self._buf):
                break
            if bytes(self._buf[i:i + len(_OSC133_PREFIX)]) != _OSC133_PREFIX:
                # ESC followed by something else (e.g. another OSC, CSI).
                out.append(b)
                i += 1
                continue
            end = self._buf.find(_BEL, i + len(_OSC133_PREFIX))
            if end < 0:
                # Incomplete; hold from i.
                break
            body = bytes(self._buf[i + len(_OSC133_PREFIX):end])
            ev = _parse_body(body)
            if ev is not None:
                events.append(ev)
            i = end + 1
        remainder = bytes(self._buf[i:])
        self._buf.clear()
        self._buf.extend(remainder)
        return bytes(out), events


def _parse_body(body: bytes) -> Event | None:
    if body == b"A":
        return PromptStart()
    if body == b"B":
        return CommandStart()
    if body.startswith(b"D"):
        rest = body[1:]
        if rest.startswith(b";"):
            payload = rest[1:]
            if not payload:
                return CommandEnd(exit_code=None)
            try:
                return CommandEnd(exit_code=int(payload))
            except ValueError:
                return CommandEnd(exit_code=None)
        return CommandEnd(exit_code=None)
    return None
