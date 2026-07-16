"""OSC 133 escape-sequence parser for live terminals.

Pure byte-level. No I/O, no asyncio. Yields output with *all* OSC
sequences stripped, plus prompt/command-boundary events for the OSC 133
subset. Both OSC terminators are handled: BEL (``\\a``) and ST
(``ESC \\``) — modern shell integrations (VTE, starship, Ghostty) mix
them, and mishandling ST swallows the very ``D`` marker that reports the
exit code.
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
class CommandOutputStart:
    pass


@dataclass(frozen=True)
class CommandEnd:
    exit_code: int | None


Event = Union[PromptStart, CommandStart, CommandOutputStart, CommandEnd]

_ESC = 0x1B
_BEL = 0x07
_RBRACKET = 0x5D  # ']'
_ST = b"\x1b\\"


Segment = Union[bytes, Event]


class OSC133Parser:
    """Stateful parser. Strips every OSC sequence from the output and
    emits events for the OSC 133 markers (A/B/C/D). Holds back a trailing
    partial escape sequence so a split-across-chunks marker is safe.

    ``feed`` returns an *ordered* list of segments — output ``bytes`` and
    ``Event`` objects interleaved in stream order. Ordering matters: a
    reset event (B/C) must be applied relative to the output around it, or
    output arriving in the same read as its trailing markers gets wiped.
    Use :func:`split_segments` for the bulk ``(bytes, events)`` view."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[Segment]:
        self._buf.extend(chunk)
        segments: list[Segment] = []
        out = bytearray()

        def flush() -> None:
            if out:
                segments.append(bytes(out))
                out.clear()

        i = 0
        n = len(self._buf)
        while i < n:
            b = self._buf[i]
            if b != _ESC:
                out.append(b)
                i += 1
                continue
            # An ESC: might introduce an OSC (ESC ]). Need the next byte
            # to tell; if it's not here yet, hold back from this ESC.
            if i + 1 >= n:
                break
            if self._buf[i + 1] != _RBRACKET:
                # Not an OSC (CSI colours, cursor moves, …). Pass the ESC
                # through verbatim — downstream renders ANSI itself.
                out.append(b)
                i += 1
                continue
            # OSC: ESC ] <body> (BEL | ESC \). Find the nearest terminator.
            bel = self._buf.find(_BEL, i + 2)
            st = self._buf.find(_ST, i + 2)
            ends = [t for t in (bel, st) if t != -1]
            if not ends:
                break  # incomplete OSC — hold the whole thing back
            end = min(ends)
            body = bytes(self._buf[i + 2:end])
            ev = _parse_osc_body(body)
            if ev is not None:
                flush()
                segments.append(ev)
            i = end + (2 if end == st and (bel == -1 or st < bel) else 1)
        flush()
        self._buf = bytearray(self._buf[i:])
        return segments


def split_segments(segments: list[Segment]) -> tuple[bytes, list[Event]]:
    """Bulk view of a :meth:`OSC133Parser.feed` result: concatenated
    output bytes and the events in order (discarding their interleaving)."""
    out = bytearray()
    events: list[Event] = []
    for seg in segments:
        if isinstance(seg, (bytes, bytearray)):
            out.extend(seg)
        else:
            events.append(seg)
    return bytes(out), events


def _parse_osc_body(body: bytes) -> Event | None:
    if not body.startswith(b"133;"):
        return None  # some other OSC (title, cwd, VTE) — stripped, no event
    rest = body[4:]
    if rest == b"A":
        return PromptStart()
    if rest == b"B":
        return CommandStart()
    if rest == b"C":
        return CommandOutputStart()
    if rest.startswith(b"D"):
        tail = rest[1:]
        if tail.startswith(b";"):
            payload = tail[1:]
            if not payload:
                return CommandEnd(exit_code=None)
            try:
                return CommandEnd(exit_code=int(payload))
            except ValueError:
                return CommandEnd(exit_code=None)
        return CommandEnd(exit_code=None)
    return None
