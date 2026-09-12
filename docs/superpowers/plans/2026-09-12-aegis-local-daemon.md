# The aegis local daemon — stage 5a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status: not started.** Written 2026-09-12 against `c88f21a`.

**Goal:** `aegis` becomes a client. A detached daemon holds the brain and every view; a terminal attaches over a unix socket, pipes bytes both ways, and detaching leaves the brain running.

**Architecture:** Stage 4 built the view seam and left it unwired — `ViewRegistry` is imported by nothing in `src/`. This plan builds the *input* half of that seam (`ViewDriver.feed`, which stage 4's plan claimed existed and does not), wraps both halves in a unix-socket transport inside `aegis serve`, and adds an ~80-line `aegis attach` client that knows nothing about sessions. `aegis` with no subcommand becomes ensure-daemon-then-attach; `aegis --foreground` keeps today's path byte-for-byte.

**Tech Stack:** Python 3.13, asyncio, Textual 8.2.6 (`WebDriver`'s `b"D"`/`b"M"` packet codec, `ByteStream`, `XTermParser`), typer, pytest.

**Spec:** `docs/superpowers/specs/2026-09-07-retire-web-ui-tui-over-web-design.md` — sections *`aegis` alone*, *`aegis attach` is a dumb pipe*, *Command surface*, *Sequencing* item 5.

**Predecessors:** `docs/superpowers/plans/2026-09-09-aegis-roots-and-embed.md` (stages 1–3, shipped `9c903ca`), `docs/superpowers/plans/2026-09-11-aegis-view-seam.md` (stage 4, shipped `5259bb3`), `docs/superpowers/plans/2026-09-11-aegis-session-propagation.md` (shipped `9e3220e`).

---

## Scope decision: why this is 5a and not all of stage 5

The spec's stage 5 is two subsystems that share only a frame format:

| | 5a — **this plan** | 5b — next plan |
|---|---|---|
| Transport | unix socket, filesystem-permission guarded | WebSocket, token handshake |
| Client | `aegis attach` on this box | `aegis attach wss://…`, and a browser xterm.js view |
| Auth | none — the socket's mode bits are the gate | one secret in the first frame; Caddy `basicauth` dropped |
| Risk | changes the daily `aegis` invocation | changes what stands between the internet and `permission: full` on the VPS |

They fail for entirely different reasons and one of them can take `dev.apiad.net` down. Splitting is not a scope cut: **5a alone is usable software** — Alex runs `aegis`, gets a daemon, detaches with `Ctrl+Q`, reattaches from another terminal and finds his agents still running. That is the whole point of the daemon, and it lands without touching auth.

5b inherits from this plan a frame codec (`aegis/daemon/protocol.py`), a view-serving coroutine that is transport-agnostic (`serve_view`), and the `aegis attach` argument surface. Its work is a second transport under the same `serve_view`, which is exactly what the spec's *transport equivalence* test asserts. That test belongs to 5b because it needs two transports to compare.

## Global Constraints

Copied from the spec and from what stages 1–4 already pinned. Every task's requirements implicitly include these.

- **Frames are Textual's, unchanged:** `b"D" + 4-byte big-endian length + utf-8 payload` for data, `b"M" + length + json` for meta. Do not invent a codec; `WebDriver.write` (`web_driver.py:80-86`) already emits exactly this and `ViewDriver` inherits it.
- **The client parses no aegis concepts.** No sessions, no agents, no queues. Bytes out, key events and a window size in. The moment it needs its own encoding it has become `RemoteSessionManager` again.
- **`web=` stays wired.** This stage deletes nothing. `aegis serve` keeps building `WebFrontend` when `web:` is configured, so `dev.apiad.net` keeps serving beside the daemon. Deletion is stage 6.
- **`--remote` is untouched.** `RemoteSessionManager`, `ws_client`, `_build_remote_manager` and the `--remote` flag all keep working and keep their tests. Stage 6 deletes them.
- **No new third-party dependency.** Everything here is stdlib asyncio plus Textual internals aegis already imports.
- **The suite baseline is 3642 passed, 1 skipped** (`-m "not live"`, at `c88f21a`). No regression against it.
- **Idle timeout, settled here** (spec open question 1): **1800 s (30 min)** with both conditions required — zero views **and** zero live sessions. Overridable per-process by `AEGIS_IDLE_TIMEOUT` in seconds; `0` disables. Rationale under Task 9.
- **Geometry with zero views, settled here** (spec open question 2): the daemon never renders without a view, because a `View` is only constructed by an attach and an attach always carries a geometry (`open_view` requires it, and `View.run` passes `size=` explicitly). There is nothing to confirm and no default to pick.

---

## File structure

**New package `src/aegis/daemon/`** — everything that is about *carrying* a view, none of which belongs in `views/` (which is about *being* a view).

| File | Responsibility |
|---|---|
| `daemon/protocol.py` | The frame codec and the meta vocabulary. Pure functions + one incremental decoder. No I/O. Shared by server, client and 5b's WS transport. |
| `daemon/server.py` | `serve_view(reader, writer, registry)` — the transport-agnostic coroutine that owns one attached client — plus `UnixSocketServer`, which is only the listener. |
| `daemon/client.py` | `aegis attach`: raw mode, two pipe loops, `SIGWINCH`. Knows the protocol module and nothing else. |
| `daemon/registry.py` | Daemons across roots: `~/.aegis/daemons/*.json`, `record`/`forget`/`live_daemons`, pid liveness. Backs `aegis ls` and `aegis kill`. |
| `daemon/lifecycle.py` | `socket_path(roots)`, `ensure_daemon(root)` (autostart + wait-for-socket), `IdleReaper`. |

**Modified:**

| File | Change |
|---|---|
| `views/driver.py` | `ViewDriver.feed(data)` — the input direction, which does not exist today. |
| `views/sink.py` *(new, in `views/`)* | `FrameSink` — a list while nobody listens, a fanout when someone does. Fixes an unbounded `list` in a process that now lives for days. |
| `views/view.py` | `View` holds a `FrameSink`; `frames` becomes a property over it. `open_view` gains `owns_brain=False`. |
| `tui/app.py` | `owns_brain: bool = True` kwarg; `action_quit` takes the detach branch when it is False. |
| `cli.py` | `_serve` builds a `ViewRegistry` + socket server when asked; `aegis` becomes ensure-then-attach; `--foreground`; `attach`, `ls`, `kill` commands. |

---

## Task 1: `ViewDriver.feed` — the input direction

Stage 4 disabled stdin (`run_input_thread` returns, `_input_reader` is a null object) and never replaced it. `tests/views/test_multi_view.py` types into a view with `pilot.press`, which posts messages straight to the app and bypasses the driver entirely — so nothing has ever driven a view through bytes. A transport has only bytes.

`WebDriver.run_input_thread` (`web_driver.py:184-212`) is the shape to copy: `ByteStream.feed(data)` yields `(packet_type, payload)` pairs; `"D"` payloads go through an incremental utf-8 decoder into `XTermParser.feed`, whose events go to `self.process_message`; anything else goes to `self._on_meta`. We keep that body and drop the thread, the `InputReader` loop and the `_ExitInput` dance.

**Files:**
- Modify: `src/aegis/views/driver.py`
- Test: `tests/views/test_view_input.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `ViewDriver.feed(self, data: bytes) -> None`. Accepts a chunk of the client's stream — framed `b"D"`/`b"M"` packets, split at arbitrary boundaries. Called from the event loop, never from a thread.

- [ ] **Step 1: Write the failing tests**

Create `tests/views/test_view_input.py`:

```python
"""The input direction of the view seam: client bytes -> app messages.

Stage 4 built frames-out and left frames-in unbuilt. These tests drive a
real view the way a transport will -- by handing it bytes -- rather than
with pilot.press, which posts straight to the app and would pass against a
`feed` that did nothing at all.
"""
import json

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.views.registry import ViewRegistry

from tests.views.conftest import FakeMCP


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _reg(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": Agent(harness="claude-code", model="opus",
                               effort="high", permission="auto")}
    mgr = SessionManager(roster, "default",
                         make_session=lambda p, u, h: _FakeHarness(),
                         mcp=None, roots=roots)
    return ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP()), mgr


def _data(payload: bytes) -> bytes:
    return b"D" + len(payload).to_bytes(4, "big") + payload


def _meta(obj: dict) -> bytes:
    raw = json.dumps(obj).encode("utf-8")
    return b"M" + len(raw).to_bytes(4, "big") + raw


async def test_a_data_frame_becomes_a_key_event(tmp_path):
    reg, _ = _reg(tmp_path)
    view = await reg.open("v1", (80, 24))
    async with view.app.run_test(headless=False, size=(80, 24)) as pilot:
        await pilot.pause()
        seen = []
        view.app._driver.process_message = lambda ev: seen.append(ev)
        view.app._driver.feed(_data(b"x"))
        assert [getattr(e, "key", None) for e in seen] == ["x"]


async def test_a_data_frame_split_across_chunks_still_arrives(tmp_path):
    """A socket splits wherever it likes; the header may arrive alone."""
    reg, _ = _reg(tmp_path)
    view = await reg.open("v1", (80, 24))
    async with view.app.run_test(headless=False, size=(80, 24)) as pilot:
        await pilot.pause()
        seen = []
        view.app._driver.process_message = lambda ev: seen.append(ev)
        frame = _data(b"hi")
        for i in range(len(frame)):
            view.app._driver.feed(frame[i:i + 1])
        assert [getattr(e, "key", None) for e in seen] == ["h", "i"]


async def test_a_resize_meta_frame_resizes_this_view(tmp_path):
    reg, _ = _reg(tmp_path)
    view = await reg.open("v1", (80, 24))
    async with view.app.run_test(headless=False, size=(80, 24)) as pilot:
        await pilot.pause()
        view.app._driver.feed(_meta({"type": "resize",
                                     "width": 120, "height": 40}))
        await pilot.pause()
        assert view.app._driver._size == (120, 40)


async def test_a_damaged_meta_frame_does_not_kill_the_view(tmp_path):
    """A client is untrusted input. A bad frame costs that frame, not the
    daemon -- every other view in the process is on this same loop."""
    reg, _ = _reg(tmp_path)
    view = await reg.open("v1", (80, 24))
    async with view.app.run_test(headless=False, size=(80, 24)) as pilot:
        await pilot.pause()
        bad = b"nope"
        view.app._driver.feed(b"M" + len(bad).to_bytes(4, "big") + bad)
        seen = []
        view.app._driver.process_message = lambda ev: seen.append(ev)
        view.app._driver.feed(_data(b"y"))
        assert [getattr(e, "key", None) for e in seen] == ["y"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/views/test_view_input.py -q`
Expected: FAIL, four times, with `AttributeError: 'BoundViewDriver' object has no attribute 'feed'`.

- [ ] **Step 3: Implement `feed`**

In `src/aegis/views/driver.py`, add to the imports:

```python
from codecs import getincrementaldecoder

from textual._xterm_parser import XTermParser
from textual.drivers._byte_stream import ByteStream
```

Add to `ViewDriver.__init__`, after `self._write = self._emit`:

```python
        # The input direction. WebDriver builds these inside
        # run_input_thread and holds them on the thread's stack
        # (`web_driver.py:186-191`); a fed driver has no thread, so they
        # are instance state. One set per view: XTermParser and the utf-8
        # decoder are both stateful across chunks, and a socket splits
        # wherever it likes -- mid-escape-sequence, mid-codepoint, and
        # mid-4-byte-length-header.
        self._byte_stream = ByteStream()
        self._parser = XTermParser(debug=self._debug)
        self._decode = getincrementaldecoder("utf-8")().decode
```

Add the method:

```python
    def feed(self, data: bytes) -> None:
        """Take a chunk of a client's stream. The input half of the seam.

        The body of ``WebDriver.run_input_thread`` (`web_driver.py:193-205`)
        without the thread: same ByteStream demux, same parser, same
        ``process_message``. Called on the event loop, so ``process_message``
        reaches ``App.post_message`` directly rather than across a thread.

        A damaged frame is swallowed per-frame rather than raised. The
        caller is a transport reading from an untrusted client, and every
        other view in this process shares one loop with it: a raise here
        would take down a socket task holding a view that did nothing
        wrong. ``_ExitInput`` is the one exception that must NOT be
        swallowed -- ``on_meta`` raises it for ``{"type": "exit"}``, which
        is the client saying it is gone.
        """
        for packet_type, payload in self._byte_stream.feed(data):
            if packet_type == "D":
                for event in self._parser.feed(self._decode(payload)):
                    self.process_message(event)
            else:
                try:
                    self._on_meta(packet_type, payload)
                except _ExitInput:
                    raise
                except Exception:  # noqa: BLE001 — see docstring
                    from textual import log
                    from traceback import format_exc
                    log(format_exc())
        for event in self._parser.tick():
            self.process_message(event)
```

and the import it needs, beside the others:

```python
from textual.drivers.web_driver import WebDriver, _ExitInput
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/views/test_view_input.py -q`
Expected: 4 passed.

- [ ] **Step 5: Mutation-check the split-chunk test**

The split test is the one that earns its keep — a naive `feed` that ignores framing would pass the single-chunk test. Confirm it fails when framing is dropped:

```bash
cd /home/apiad/Workspace/repos/aegis
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/aegis/views/driver.py"); s = p.read_text()
assert "MUTANT" not in s
p.write_text(s.replace(
    "        for packet_type, payload in self._byte_stream.feed(data):",
    "        for packet_type, payload in [('D', data[5:])]:  # MUTANT"))
PY
uv run pytest tests/views/test_view_input.py -q; echo "rc=$?"
```

Expected: the split test FAILS. Then restore and re-run:

```bash
cd /home/apiad/Workspace/repos/aegis
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/aegis/views/driver.py"); s = p.read_text()
p.write_text(s.replace(
    "        for packet_type, payload in [('D', data[5:])]:  # MUTANT",
    "        for packet_type, payload in self._byte_stream.feed(data):"))
assert "MUTANT" not in p.read_text()
print("restored")
PY
uv run pytest tests/views/test_view_input.py -q
```

Expected: 4 passed, and `grep -c MUTANT src/aegis/views/driver.py` is 0.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/views/driver.py tests/views/test_view_input.py
git commit -m "feat(views): the input direction — ViewDriver.feed

Stage 4 built frames-out and stopped. Every view test to date typed with
pilot.press, which posts to the app and never touches the driver, so the
seam has never carried a byte inward. A transport carries only bytes.

The body is WebDriver.run_input_thread's without the thread: same
ByteStream demux, same XTermParser, same process_message. The three
stateful pieces move onto the instance, because a socket splits mid-escape
-sequence and mid-codepoint and one parser per view is the only correct
place for them."
```

---

## Task 2: `FrameSink` — frames out, without a leak

`open_view` today binds `frames.append` on a plain `list` that nothing ever drains. That was right for a test and is wrong for a process that now runs for days: a TUI redrawing a status bar emits frames continuously, and an attached view would grow that list without bound.

`FrameSink` keeps the list for the detached case — capped — and hands bytes straight to a consumer when one is attached, so an attached view buffers nothing at all. No replay buffer is needed: a reattaching client gets a full frame from `View.repaint()` (Task 6), not a replay.

**Files:**
- Create: `src/aegis/views/sink.py`
- Modify: `src/aegis/views/view.py`, `src/aegis/views/__init__.py`
- Test: `tests/views/test_frame_sink.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `FrameSink(cap: int = 4096)`; `__call__(data: bytes) -> None`; `.frames -> list[bytes]`; `.attach(fn: Callable[[bytes], None]) -> None`; `.detach(fn) -> None`; `.consumers -> int`.
  - `View.sink: FrameSink`, and `View.frames` as a read-only property returning `self.sink.frames` (so `view.frames`, `len()`, `[:]  =` and `.clear()` in existing tests keep working against the real list).

- [ ] **Step 1: Write the failing test**

Create `tests/views/test_frame_sink.py`:

```python
"""FrameSink: a list while nobody listens, a fanout when someone does."""
from aegis.views.sink import FrameSink


def test_with_no_consumer_frames_accumulate():
    s = FrameSink()
    s(b"a")
    s(b"b")
    assert s.frames == [b"a", b"b"]


def test_a_consumer_receives_every_frame():
    s = FrameSink()
    got = []
    s.attach(got.append)
    s(b"a")
    s(b"b")
    assert got == [b"a", b"b"]


def test_an_attached_view_buffers_nothing():
    """The leak this class exists to close. A view attached for a day
    emits frames continuously; none of them are ours to keep."""
    s = FrameSink()
    s.attach(lambda _b: None)
    for _ in range(10_000):
        s(b"x")
    assert s.frames == []


def test_the_detached_buffer_is_capped():
    s = FrameSink(cap=8)
    for i in range(100):
        s(bytes([i]))
    assert len(s.frames) == 8
    assert s.frames[-1] == bytes([99])


def test_detaching_restores_buffering():
    s = FrameSink()
    fn = []
    s.attach(fn.append)
    s(b"live")
    s.detach(fn.append)
    assert s.consumers == 0
    s(b"buffered")
    assert s.frames == [b"buffered"]


def test_detach_of_an_unattached_consumer_is_a_no_op():
    """A client that dies mid-handshake detaches in a finally block that
    may never have attached."""
    s = FrameSink()
    s.detach(print)
    assert s.consumers == 0


def test_one_slow_consumer_does_not_stop_the_others():
    s = FrameSink()
    got = []
    def boom(_b):
        raise RuntimeError("client went away")
    s.attach(boom)
    s.attach(got.append)
    s(b"a")
    assert got == [b"a"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/views/test_frame_sink.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'aegis.views.sink'`.

- [ ] **Step 3: Write `FrameSink`**

Create `src/aegis/views/sink.py`:

```python
"""Where one view's frames go.

Stage 4 bound ``list.append`` directly, which is right for a test and wrong
for a daemon: a view attached for a day emits frames continuously and
nothing drained that list. So: bytes go straight to a live consumer and are
not kept, and are buffered (capped) only while nobody is listening.

There is deliberately no replay buffer. A reattaching client is given a
full frame by ``View.repaint()``, which is correct against a screen the
client has never seen; replaying deltas against one is not.
"""
from __future__ import annotations

from typing import Callable


class FrameSink:
    def __init__(self, cap: int = 4096) -> None:
        self.frames: list[bytes] = []
        self._cap = cap
        self._consumers: list[Callable[[bytes], None]] = []

    @property
    def consumers(self) -> int:
        return len(self._consumers)

    def attach(self, fn: Callable[[bytes], None]) -> None:
        self._consumers.append(fn)

    def detach(self, fn: Callable[[bytes], None]) -> None:
        # A client that dies mid-handshake runs its finally block without
        # ever having attached. Missing is the normal case, not an error.
        try:
            self._consumers.remove(fn)
        except ValueError:
            pass

    def __call__(self, data: bytes) -> None:
        if self._consumers:
            for fn in list(self._consumers):
                try:
                    fn(data)
                except Exception:  # noqa: BLE001
                    # One dead socket must not blind the view's other
                    # consumers, nor propagate into Textual's render path,
                    # which is what calls this.
                    from textual import log
                    from traceback import format_exc
                    log(format_exc())
            return
        self.frames.append(data)
        if len(self.frames) > self._cap:
            del self.frames[:len(self.frames) - self._cap]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/views/test_frame_sink.py -q`
Expected: 7 passed.

- [ ] **Step 5: Thread it through `View`**

In `src/aegis/views/view.py`, replace the `frames` field on the dataclass:

```python
@dataclass
class View:
    view_id: str
    app: AegisApp
    state: ViewState
    sink: FrameSink = field(default_factory=FrameSink)
    _task: asyncio.Task | None = None

    @property
    def frames(self) -> list[bytes]:
        """The detached buffer. A property, not a field, so the existing
        in-process assertions (`len(v.frames)`, `v.frames[:] = …`,
        `v.frames.clear()`) keep operating on the real list."""
        return self.sink.frames
```

add the import:

```python
from aegis.views.sink import FrameSink
```

and in `open_view`, replace `frames: list[bytes] = []` / `driver_class=view_driver_for(frames.append)` / `frames=frames` with:

```python
    sink = FrameSink()
```
```python
        driver_class=view_driver_for(sink),
```
```python
    return View(view_id=view_id, app=app, state=state, sink=sink)
```

In `src/aegis/views/__init__.py`, export it:

```python
from aegis.views.sink import FrameSink
```
and add `"FrameSink"` to `__all__`.

- [ ] **Step 6: Run the whole views suite**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/views -q`
Expected: all pass, including the stage-4 gate `test_multi_view.py` untouched.

- [ ] **Step 7: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/views/sink.py src/aegis/views/view.py \
        src/aegis/views/__init__.py tests/views/test_frame_sink.py
git commit -m "feat(views): FrameSink — a list while nobody listens

open_view bound list.append on a list nothing drained. Correct for a test,
a leak in a process that now runs for days: an attached view emits frames
continuously. A live consumer takes the bytes and we keep none; the
buffer exists only while detached, and is capped.

No replay buffer, deliberately — a reattaching client gets a full frame
from repaint(), and replaying deltas against a screen it has never seen is
the bug repaint() exists to avoid."
```

---

## Task 3: `daemon/protocol.py` — the frame codec

One module both sides import, so the client and the server cannot disagree about the wire. 5b's WebSocket transport imports this same module — that is what keeps the two transports byte-identical rather than merely intended to be.

**Files:**
- Create: `src/aegis/daemon/__init__.py`, `src/aegis/daemon/protocol.py`
- Test: `tests/daemon/__init__.py`, `tests/daemon/test_protocol.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `encode_data(payload: bytes) -> bytes`
  - `encode_meta(obj: dict) -> bytes`
  - `FrameDecoder()`, with `feed(data: bytes) -> Iterator[tuple[str, bytes]]` yielding `("D", payload)` / `("M", payload)`
  - `hello(view_id: str, width: int, height: int) -> bytes`
  - `resize(width: int, height: int) -> bytes`
  - `parse_hello(payload: bytes) -> tuple[str, int, int]`, raising `ProtocolError` on anything malformed
  - `ProtocolError(Exception)`

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/__init__.py` (empty) and `tests/daemon/test_protocol.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/daemon/test_protocol.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'aegis.daemon'`.

- [ ] **Step 3: Write the module**

Create `src/aegis/daemon/__init__.py`:

```python
"""Carrying a view to a client. `aegis/views/` is about being one."""
```

Create `src/aegis/daemon/protocol.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/daemon/test_protocol.py -q`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/daemon/__init__.py src/aegis/daemon/protocol.py \
        tests/daemon/__init__.py tests/daemon/test_protocol.py
git commit -m "feat(daemon): the wire — Textual's packet format, not ours

One byte of type, four of big-endian length, payload. WebDriver already
emits and consumes exactly this, so a transport speaking it needs no
translation in either direction — and a translation layer is where
--remote's protocol rot began.

parse_hello is read adversarially because the first frame is the only one
a client can send before it owns a view: the view id becomes a filename,
so it is validated as a single path component rather than sanitised."
```

---

## Task 4: the detach branch — `Ctrl+Q` must not kill the brain

`AegisApp.action_quit` (`app.py:1859-1885`) tears down the brain: it closes every `ConversationPane` (which closes the harness subprocess behind it), stops the `QueueManager`, stops the MCP server and stops the file indexer. That is correct for `LocalTuiAttachment`, where the app owns the process. It is fatal in a daemon, where those panes wrap the *brain's* sessions (`ConversationPane(core=session)`, shipped `c511bc0`) and the MCP plane belongs to `_serve`.

So `AegisApp` learns whether it owns the brain. Every existing caller keeps today's behaviour by default; only `open_view` says otherwise.

**Files:**
- Modify: `src/aegis/tui/app.py:342-344` (signature), `:1859` (`action_quit`), `src/aegis/views/view.py` (`open_view`)
- Test: `tests/views/test_detach_does_not_kill_the_brain.py` (create)

**Interfaces:**
- Consumes: `open_view` from Task 2.
- Produces: `AegisApp(..., owns_brain: bool = True)`, stored as `self._owns_brain`. `open_view` passes `owns_brain=False`.

- [ ] **Step 1: Write the failing test**

Create `tests/views/test_detach_does_not_kill_the_brain.py`:

```python
"""Ctrl+Q in a daemon view detaches. The brain outlives it.

action_quit closes every pane, and a bridged app's panes wrap the BRAIN's
sessions — so the unguarded path kills the agents of every other view too.
This asserts on the substrate (the manager still holds its session, the
harness was never closed), not on the app's exit code.
"""
from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.views.registry import ViewRegistry

from tests.views.conftest import FakeMCP


class _FakeHarness:
    def __init__(self):
        self.closed = False

    async def start(self): ...
    async def send(self, t): ...

    async def close(self):
        self.closed = True

    async def events(self):
        if False:
            yield


def _reg(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": Agent(harness="claude-code", model="opus",
                               effort="high", permission="auto")}
    harnesses = []

    def _make(prompt, agent, host):
        h = _FakeHarness()
        harnesses.append(h)
        return h

    mgr = SessionManager(roster, "default", make_session=_make,
                         mcp=None, roots=roots)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(),
                       agents=roster, default_agent="default",
                       make_session=_make)
    return reg, mgr, harnesses


async def test_quitting_a_view_leaves_the_brains_session_running(tmp_path):
    reg, mgr, harnesses = _reg(tmp_path)
    await mgr.spawn("default")
    view = await reg.open("v1", (80, 24))
    async with view.app.run_test(headless=False, size=(80, 24)) as pilot:
        await pilot.pause()
        await view.app.action_quit()
        await pilot.pause()
    assert mgr.list_sessions(), "the brain lost its session when a view quit"
    assert not any(h.closed for h in harnesses), \
        "a view quitting closed the brain's harness subprocess"


async def test_quitting_a_view_leaves_the_brains_mcp_plane_up(tmp_path):
    """The MCP server is _serve's, shared by every view. One view exiting
    must not take the agent plane down under the others."""
    reg, mgr, _ = _reg(tmp_path)
    view = await reg.open("v1", (80, 24))
    mcp = view.app._mcp
    stopped = []
    mcp.stop = lambda: stopped.append(True) or _noop()
    async with view.app.run_test(headless=False, size=(80, 24)) as pilot:
        await pilot.pause()
        await view.app.action_quit()
        await pilot.pause()
    assert not stopped, "a view quitting stopped the shared MCP plane"


async def _noop():
    return None


async def test_the_local_tui_still_owns_its_brain(tmp_path):
    """The guard is opt-out, not a behaviour change for every caller.
    LocalTuiAttachment and the bootstrap TUI still tear down on quit."""
    from aegis.tui.app import AegisApp
    app = AegisApp({}, "", None, FakeMCP())
    assert app._owns_brain is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/views/test_detach_does_not_kill_the_brain.py -q`
Expected: FAIL — `AttributeError: 'AegisApp' object has no attribute '_owns_brain'` on the third, and the first two failing on a closed harness / stopped plane.

- [ ] **Step 3: Add the flag and the branch**

In `src/aegis/tui/app.py`, add to `__init__`'s keyword-only arguments (after `view_state`):

```python
                 owns_brain: bool = True) -> None:
```

and store it beside `self._view_state`:

```python
        # Whether quitting this app tears the brain down with it. True for
        # every caller that IS the process (LocalTuiAttachment, the
        # bootstrap TUI, --remote); False for a daemon view, whose panes
        # wrap the brain's sessions and whose MCP plane belongs to _serve.
        # Quitting one view there must cost that view and nothing else.
        self._owns_brain = owns_brain
```

In `action_quit`, immediately after the `_remote_manager` branch returns, insert:

```python
        if not self._owns_brain:
            # Detach. The roster is brain state and is persisted by the
            # brain; the ViewState is persisted by the View on stop. What
            # we must NOT do is the teardown below — closing panes closes
            # the harness subprocesses of sessions other views are looking
            # at, and stopping the MCP plane takes the agent surface down
            # for all of them.
            self._file_indexer.stop()
            self.exit()
            return
```

In `src/aegis/views/view.py`, inside `open_view`'s `AegisApp(...)` call, add:

```python
        # A daemon view detaches on Ctrl+Q; it does not own the brain.
        owns_brain=False,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/views/test_detach_does_not_kill_the_brain.py -q`
Expected: 3 passed.

- [ ] **Step 5: Run the TUI and views suites for regressions**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/tui tests/views -q`
Expected: all pass. The default is `True`, so nothing existing changes.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/tui/app.py src/aegis/views/view.py \
        tests/views/test_detach_does_not_kill_the_brain.py
git commit -m "feat(tui): a daemon view detaches on quit, it does not tear down

action_quit closes every pane and stops the MCP plane. Right when the app
IS the process; fatal in a daemon, where a bridged app's panes wrap the
brain's sessions (c511bc0) and the plane is _serve's. Quitting one view
would have closed the harness subprocesses of every session the other
views were looking at.

owns_brain defaults True, so every existing caller is unchanged; only
open_view opts out."
```

---

## Task 5: `serve_view` — one attached client, transport-agnostic

The coroutine that owns a connection. It is deliberately written against `asyncio.StreamReader`/`StreamWriter`-shaped duck types rather than a socket, because 5b hands it a WebSocket adapter and the spec's *transport equivalence* assertion is only meaningful if both go through this same function.

Sequence: read the hello → open (or restore) the view from the registry → start the app if it is new → attach the sink → `repaint()` → pipe inbound frames into `driver.feed` until EOF → detach → close the view.

**Files:**
- Create: `src/aegis/daemon/server.py`
- Test: `tests/daemon/test_serve_view.py` (create)

**Interfaces:**
- Consumes: `FrameDecoder`, `parse_hello`, `ProtocolError` (Task 3); `ViewRegistry.open/close` (stage 4); `View.sink`, `View.repaint`, `View.run` (Tasks 2 and stage 4); `ViewDriver.feed` (Task 1).
- Produces: `async def serve_view(reader, writer, registry, *, on_close=None) -> None`. `reader` needs `read(n) -> bytes` (b"" at EOF); `writer` needs `write(bytes)`, `drain()`, `close()`, `wait_closed()`.

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/test_serve_view.py`:

```python
"""One attached client, over a pair of in-memory pipes.

No socket here on purpose: everything this file asserts is about the
connection's *logic*, and a test mediated by a socket fails for two
reasons. The real socket gets its own test in test_unix_socket.py.
"""
import asyncio

import pytest

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.daemon.protocol import FrameDecoder, encode_data, hello
from aegis.daemon.server import serve_view
from aegis.views.registry import ViewRegistry

from tests.views.conftest import FakeMCP


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


class _Pipe:
    """A reader the test pushes into and a writer it reads out of."""

    def __init__(self):
        self._q: asyncio.Queue = asyncio.Queue()
        self.sent = bytearray()
        self.closed = False

    # reader side
    async def read(self, n: int = -1) -> bytes:
        return await self._q.get()

    def push(self, data: bytes) -> None:
        self._q.put_nowait(data)

    def eof(self) -> None:
        self._q.put_nowait(b"")

    # writer side
    def write(self, data: bytes) -> None:
        self.sent.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _reg(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": Agent(harness="claude-code", model="opus",
                               effort="high", permission="auto")}
    mgr = SessionManager(roster, "default",
                         make_session=lambda p, u, h: _FakeHarness(),
                         mcp=None, roots=roots)
    return ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP()), mgr


async def _until(predicate, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def test_frames_reach_the_client(tmp_path):
    reg, _ = _reg(tmp_path)
    pipe = _Pipe()
    pipe.push(hello("v1", 80, 24))
    task = asyncio.create_task(serve_view(pipe, pipe, reg))
    try:
        await _until(lambda: b"\x1b[?1049h" in bytes(pipe.sent))
    finally:
        pipe.eof()
        await asyncio.wait_for(task, timeout=10)


async def test_the_client_gets_whole_frames(tmp_path):
    """Every byte the client receives must decode as a frame. A transport
    that wrote a partial or a doubled header would still show a plausible
    screen in a terminal and be unusable to a browser."""
    reg, _ = _reg(tmp_path)
    pipe = _Pipe()
    pipe.push(hello("v1", 80, 24))
    task = asyncio.create_task(serve_view(pipe, pipe, reg))
    try:
        await _until(lambda: len(pipe.sent) > 1000)
    finally:
        pipe.eof()
        await asyncio.wait_for(task, timeout=10)
    dec = FrameDecoder()
    frames = list(dec.feed(bytes(pipe.sent)))
    assert frames, "nothing decoded"
    assert all(k in ("D", "M") for k, _ in frames)
    assert dec._buf == b"" or len(dec._buf) < 5 + 2 ** 20


async def test_the_hello_geometry_is_the_views_geometry(tmp_path):
    reg, _ = _reg(tmp_path)
    pipe = _Pipe()
    pipe.push(hello("v1", 120, 40))
    task = asyncio.create_task(serve_view(pipe, pipe, reg))
    try:
        await _until(lambda: reg.get("v1") is not None)
        await _until(lambda: reg.get("v1").state.geometry == (120, 40))
    finally:
        pipe.eof()
        await asyncio.wait_for(task, timeout=10)


async def test_client_bytes_reach_the_views_driver(tmp_path):
    reg, _ = _reg(tmp_path)
    pipe = _Pipe()
    pipe.push(hello("v1", 80, 24))
    task = asyncio.create_task(serve_view(pipe, pipe, reg))
    try:
        await _until(lambda: reg.get("v1") is not None
                     and reg.get("v1").app._driver is not None)
        seen = []
        reg.get("v1").app._driver.process_message = lambda ev: seen.append(ev)
        pipe.push(encode_data(b"q"))
        await _until(lambda: seen)
        assert [getattr(e, "key", None) for e in seen] == ["q"]
    finally:
        pipe.eof()
        await asyncio.wait_for(task, timeout=10)


async def test_eof_closes_the_view(tmp_path):
    reg, _ = _reg(tmp_path)
    pipe = _Pipe()
    pipe.push(hello("v1", 80, 24))
    task = asyncio.create_task(serve_view(pipe, pipe, reg))
    await _until(lambda: reg.get("v1") is not None)
    pipe.eof()
    await asyncio.wait_for(task, timeout=10)
    assert reg.get("v1") is None, "the view outlived its only client"


async def test_the_view_state_survives_the_disconnect(tmp_path):
    """Reattach restores what this terminal was looking at."""
    reg, _ = _reg(tmp_path)
    pipe = _Pipe()
    pipe.push(hello("v1", 80, 24))
    task = asyncio.create_task(serve_view(pipe, pipe, reg))
    await _until(lambda: reg.get("v1") is not None)
    reg.get("v1").state.active_handle = "some-handle"
    pipe.eof()
    await asyncio.wait_for(task, timeout=10)

    from aegis.views.state import load_view
    restored = load_view(reg._roots.state_dir, "v1")
    assert restored is not None and restored.active_handle == "some-handle"


async def test_a_malformed_hello_closes_before_a_view_is_built(tmp_path):
    """Assert on the substrate — no view in the registry — not on an error
    frame coming back. A daemon that built the view and *then* rejected the
    client has already run the expensive, stateful half."""
    reg, _ = _reg(tmp_path)
    pipe = _Pipe()
    pipe.push(b"M" + (4).to_bytes(4, "big") + b"nope")
    await asyncio.wait_for(serve_view(pipe, pipe, reg), timeout=10)
    assert reg.list() == []
    assert pipe.closed


async def test_a_second_client_for_a_live_view_is_refused(tmp_path):
    """One client per view id. ViewRegistry.open returns the EXISTING view
    for a live id, so without this guard two sockets would both attach to
    one app, and the second's repaint would clear the first's screen."""
    reg, _ = _reg(tmp_path)
    a, b = _Pipe(), _Pipe()
    a.push(hello("v1", 80, 24))
    ta = asyncio.create_task(serve_view(a, a, reg))
    await _until(lambda: reg.get("v1") is not None)
    b.push(hello("v1", 80, 24))
    await asyncio.wait_for(serve_view(b, b, reg), timeout=10)
    assert b.closed
    assert reg.get("v1") is not None, "the refusal took the first client's view"
    a.eof()
    await asyncio.wait_for(ta, timeout=10)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/daemon/test_serve_view.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'aegis.daemon.server'`.

- [ ] **Step 3: Write `serve_view`**

Create `src/aegis/daemon/server.py`:

```python
"""One attached client, and the listener that accepts them.

``serve_view`` takes duck-typed reader/writer rather than a socket, because
stage 5b hands it a WebSocket adapter and the spec's transport-equivalence
assertion is only meaningful if both transports run this same code. A
second implementation for the second transport is exactly the divergence
that assertion exists to catch.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from aegis.daemon.protocol import FrameDecoder, ProtocolError, parse_hello

log = logging.getLogger(__name__)


async def serve_view(reader, writer, registry, *, on_close=None) -> None:
    """Own one client for its whole lifetime.

    ``reader.read(n)`` returns ``b""`` at EOF; ``writer`` needs ``write``,
    ``drain``, ``close`` and ``wait_closed``.
    """
    decoder = FrameDecoder()
    view = None
    sink_fn = None
    try:
        view_id, width, height = await _read_hello(reader, decoder)
        if registry.get(view_id) is not None:
            # One client per view id. ViewRegistry.open returns the
            # EXISTING view for a live id, so attaching twice would give
            # one app two screens: the second client's repaint lands on
            # the first client's terminal and both drive the same focus.
            raise ProtocolError(f"view {view_id!r} already has a client")

        view = await registry.open(view_id, (width, height))
        await view.run()

        loop = asyncio.get_running_loop()
        pending: list[bytes] = []

        def sink_fn(data: bytes) -> None:          # noqa: F811
            # Called from Textual's render path, which is on this loop.
            # write() is synchronous and buffers; the drain happens in the
            # flusher below so a slow client cannot block a render.
            pending.append(data)

        view.sink.attach(sink_fn)
        flusher = loop.create_task(_flush(writer, pending))
        try:
            # The client has never seen this screen -- on a reattach it has
            # seen a DIFFERENT one, which is worse. Textual emits deltas
            # unless the whole screen is dirty (`_compositor.py:1118`), so
            # ask for a full frame before any delta can be generated.
            view.repaint()
            await _pump(reader, decoder, view)
        finally:
            flusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flusher
    except ProtocolError as e:
        log.info("view client refused: %s", e)
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        if view is not None and sink_fn is not None:
            view.sink.detach(sink_fn)
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()
        if view is not None:
            # Closing persists the ViewState and stops the app. The brain
            # is untouched: the app's owns_brain is False, so its quit path
            # closes no panes and stops no plane.
            await registry.close(view.view_id)
        if on_close is not None:
            on_close()


async def _read_hello(reader, decoder: FrameDecoder) -> tuple[str, int, int]:
    """Block until the first whole frame, which must be a hello.

    Bounded, because an unauthenticated client that dribbles bytes forever
    otherwise holds a task and a socket for as long as it likes.
    """
    async with asyncio.timeout(10):
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                raise ProtocolError("client closed before hello")
            for kind, payload in decoder.feed(chunk):
                if kind != "M":
                    raise ProtocolError(f"first frame is {kind!r}, not meta")
                return parse_hello(payload)


async def _pump(reader, decoder: FrameDecoder, view) -> None:
    driver = view.app._driver
    while True:
        chunk = await reader.read(65536)
        if not chunk:
            return
        for kind, payload in decoder.feed(chunk):
            frame = (b"D" if kind == "D" else b"M")
            frame += len(payload).to_bytes(4, "big") + payload
            try:
                driver.feed(frame)
            except Exception:  # noqa: BLE001
                # driver.feed already swallows per-frame damage; this is
                # the _ExitInput the client's {"type":"exit"} raises, and
                # anything a Textual upgrade adds. Either way the client
                # is done, and no single client may raise into the accept
                # loop that serves the others.
                return


async def _flush(writer, pending: list[bytes]) -> None:
    """Carry buffered frames to the socket without blocking a render."""
    while True:
        if not pending:
            await asyncio.sleep(0.005)
            continue
        batch = b"".join(pending)
        pending.clear()
        writer.write(batch)
        await writer.drain()


class UnixSocketServer:
    """The listener. Everything about a connection is in ``serve_view``."""

    def __init__(self, path: Path, registry) -> None:
        self.path = Path(path)
        self._registry = registry
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A socket file survives SIGKILL. Removing a stale one is safe
        # because the caller has already established (via the daemon
        # registry) that no live daemon owns this root.
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        self._server = await asyncio.start_unix_server(
            self._on_client, path=str(self.path))
        # The socket's mode bits ARE the auth on this transport (the spec
        # says so explicitly), and the daemon runs `permission: full`.
        self.path.chmod(0o600)

    async def _on_client(self, reader, writer) -> None:
        await serve_view(reader, writer, self._registry)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/daemon/test_serve_view.py -q`
Expected: 8 passed.

- [ ] **Step 5: Mutation-check the repaint and the refusal**

Two assertions here are the kind that pass vacuously. Break each and confirm red.

```bash
cd /home/apiad/Workspace/repos/aegis
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/aegis/daemon/server.py"); s = p.read_text()
assert "MUTANT" not in s
p.write_text(s.replace(
    "        if registry.get(view_id) is not None:",
    "        if False:  # MUTANT: let a second client in"))
PY
uv run pytest tests/daemon/test_serve_view.py -q
```

Expected: `test_a_second_client_for_a_live_view_is_refused` FAILS. Restore:

```bash
cd /home/apiad/Workspace/repos/aegis
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/aegis/daemon/server.py"); s = p.read_text()
p.write_text(s.replace(
    "        if False:  # MUTANT: let a second client in",
    "        if registry.get(view_id) is not None:"))
assert "MUTANT" not in p.read_text()
print("restored")
PY
uv run pytest tests/daemon/test_serve_view.py -q
```

Expected: 8 passed, `grep -c MUTANT src/aegis/daemon/server.py` is 0.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/daemon/server.py tests/daemon/test_serve_view.py
git commit -m "feat(daemon): serve_view — one client, any transport

Duck-typed reader/writer rather than a socket, because 5b hands this same
coroutine a WebSocket adapter. The spec's transport-equivalence assertion
is only meaningful if both transports run one implementation; a second one
for the second transport is the divergence that assertion exists to catch.

One client per view id: ViewRegistry.open returns the EXISTING view for a
live id, so a second attach would hand one app two screens and the
newcomer's repaint would land on the incumbent's terminal.

The flusher is a separate task so a slow client cannot block a render."
```

---

## Task 6: the unix socket, end to end

Task 5 proved the logic over pipes. This proves the socket: a real listener, real `asyncio.open_unix_connection` clients, two views at two geometries over one brain — the stage-4 gate, now across a wire — and a reattach that restores state.

**Files:**
- Test: `tests/daemon/test_unix_socket.py` (create)
- Modify: none — this task is a gate over Task 5's code. If it needs a source change, that change is a defect Task 5 shipped.

**Interfaces:**
- Consumes: `UnixSocketServer` (Task 5), `hello`/`FrameDecoder`/`encode_data` (Task 3).
- Produces: nothing.

- [ ] **Step 1: Write the gate**

Create `tests/daemon/test_unix_socket.py`:

```python
"""The stage-5a gate: two terminals, one brain, over a real socket.

The stage-4 gate (tests/views/test_multi_view.py) asserted this in-process
against a list. This asserts it against bytes that crossed a file
descriptor, which is the artifact a user actually touches.
"""
import asyncio

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.daemon.protocol import FrameDecoder, encode_data, hello
from aegis.daemon.server import UnixSocketServer
from aegis.views.registry import ViewRegistry

from tests.views.conftest import FakeMCP


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _brain(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": Agent(harness="claude-code", model="opus",
                               effort="high", permission="auto")}
    mgr = SessionManager(roster, "default",
                         make_session=lambda p, u, h: _FakeHarness(),
                         mcp=None, roots=roots)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(),
                       agents=roster, default_agent="default",
                       make_session=lambda p, u, h: _FakeHarness())
    return reg, mgr, roots


class _Client:
    """What `aegis attach` is, minus the tty."""

    def __init__(self, reader, writer):
        self.r, self.w = reader, writer
        self.dec = FrameDecoder()
        self.screen = bytearray()

    async def pump_until(self, predicate, timeout=10.0):
        async with asyncio.timeout(timeout):
            while not predicate(self):
                chunk = await self.r.read(65536)
                if not chunk:
                    return
                for kind, payload in self.dec.feed(chunk):
                    if kind == "D":
                        self.screen.extend(payload)

    async def send(self, data: bytes):
        self.w.write(data)
        await self.w.drain()

    async def close(self):
        self.w.close()
        try:
            await self.w.wait_closed()
        except Exception:
            pass


async def _connect(path, view_id, geometry):
    r, w = await asyncio.open_unix_connection(str(path))
    c = _Client(r, w)
    await c.send(hello(view_id, *geometry))
    return c


async def _until(predicate, timeout=10.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def test_two_terminals_two_geometries_one_brain(tmp_path):
    reg, mgr, roots = _brain(tmp_path)
    server = UnixSocketServer(roots.state_dir / "daemon.sock", reg)
    await server.start()
    try:
        a = await _connect(server.path, "term-a", (80, 24))
        b = await _connect(server.path, "term-b", (120, 40))
        await _until(lambda: reg.get("term-a") and reg.get("term-b"))

        await a.pump_until(lambda c: len(c.screen) > 500)
        await b.pump_until(lambda c: len(c.screen) > 500)
        assert a.screen, "terminal A got no screen"
        assert b.screen, "terminal B got no screen"

        assert reg.get("term-a").app.size == (80, 24)
        assert reg.get("term-b").app.size == (120, 40)
    finally:
        await a.close()
        await b.close()
        await server.stop()


async def test_a_session_spawned_in_the_brain_reaches_both_terminals(tmp_path):
    """The property session-propagation closed, now over the wire."""
    reg, mgr, roots = _brain(tmp_path)
    server = UnixSocketServer(roots.state_dir / "daemon.sock", reg)
    await server.start()
    try:
        a = await _connect(server.path, "term-a", (80, 24))
        b = await _connect(server.path, "term-b", (80, 24))
        await _until(lambda: reg.get("term-a") and reg.get("term-b"))
        await asyncio.sleep(0.2)

        await mgr.spawn("default")

        def panes(view_id):
            from aegis.tui.pane import ConversationPane
            app = reg.get(view_id).app
            return [p.handle for p in app._panes
                    if isinstance(p, ConversationPane)]

        await _until(lambda: panes("term-a") and panes("term-b"))
        assert panes("term-a") == panes("term-b")
    finally:
        await a.close()
        await b.close()
        await server.stop()


async def test_the_socket_is_owner_only(tmp_path):
    """On this transport the mode bits ARE the auth, and the daemon runs
    permission: full."""
    reg, _, roots = _brain(tmp_path)
    server = UnixSocketServer(roots.state_dir / "daemon.sock", reg)
    await server.start()
    try:
        assert server.path.stat().st_mode & 0o777 == 0o600
    finally:
        await server.stop()


async def test_reattach_restores_the_view_and_repaints(tmp_path):
    reg, _, roots = _brain(tmp_path)
    server = UnixSocketServer(roots.state_dir / "daemon.sock", reg)
    await server.start()
    try:
        a = await _connect(server.path, "term-a", (80, 24))
        await _until(lambda: reg.get("term-a") is not None)
        reg.get("term-a").state.active_handle = "pinned"
        await a.close()
        await _until(lambda: reg.get("term-a") is None)

        b = await _connect(server.path, "term-a", (80, 24))
        await _until(lambda: reg.get("term-a") is not None)
        assert reg.get("term-a").state.active_handle == "pinned"
        await b.pump_until(lambda c: len(c.screen) > 500)
        assert b.screen, "the reattaching client received no full frame"
    finally:
        await b.close()
        await server.stop()


async def test_a_keystroke_from_the_socket_reaches_the_app(tmp_path):
    """The whole inbound path: socket -> _pump -> driver.feed -> app."""
    reg, _, roots = _brain(tmp_path)
    server = UnixSocketServer(roots.state_dir / "daemon.sock", reg)
    await server.start()
    try:
        a = await _connect(server.path, "term-a", (80, 24))
        await _until(lambda: reg.get("term-a") is not None
                     and reg.get("term-a").app._driver is not None)
        seen = []
        reg.get("term-a").app._driver.process_message = \
            lambda ev: seen.append(ev)
        await a.send(encode_data(b"z"))
        await _until(lambda: seen)
        assert [getattr(e, "key", None) for e in seen] == ["z"]
    finally:
        await a.close()
        await server.stop()


async def test_stopping_the_server_removes_the_socket_file(tmp_path):
    reg, _, roots = _brain(tmp_path)
    server = UnixSocketServer(roots.state_dir / "daemon.sock", reg)
    await server.start()
    assert server.path.exists()
    await server.stop()
    assert not server.path.exists()


async def test_a_stale_socket_file_does_not_block_a_restart(tmp_path):
    """A SIGKILLed daemon leaves its socket file behind."""
    reg, _, roots = _brain(tmp_path)
    path = roots.state_dir / "daemon.sock"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    server = UnixSocketServer(path, reg)
    await server.start()
    try:
        a = await _connect(path, "term-a", (80, 24))
        await _until(lambda: reg.get("term-a") is not None)
    finally:
        await a.close()
        await server.stop()
```

- [ ] **Step 2: Run the gate**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/daemon/test_unix_socket.py -q`
Expected: 7 passed. If any fail, the defect is in Task 5's `server.py` — fix it there and re-run rather than weakening an assertion here.

- [ ] **Step 3: Mutation-check the gate**

The two assertions worth doubting are the geometry (does it really come from *this* hello?) and the reattach repaint (does the client really get a frame, or did it read the boot render?).

```bash
cd /home/apiad/Workspace/repos/aegis
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/aegis/daemon/server.py"); s = p.read_text()
assert "MUTANT" not in s
p.write_text(s.replace("            view.repaint()",
                       "            pass  # MUTANT: no repaint"))
PY
uv run pytest tests/daemon/test_unix_socket.py::test_reattach_restores_the_view_and_repaints -q
```

Expected: FAIL (`the reattaching client received no full frame`). If it PASSES, the test is reading the boot render rather than the repaint — make the reattaching client quiesce before asserting, the way `tests/views/test_multi_view.py::_settle` does, and re-mutate until it goes red. Then restore:

```bash
cd /home/apiad/Workspace/repos/aegis
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/aegis/daemon/server.py"); s = p.read_text()
p.write_text(s.replace("            pass  # MUTANT: no repaint",
                       "            view.repaint()"))
assert "MUTANT" not in p.read_text()
print("restored")
PY
uv run pytest tests/daemon/test_unix_socket.py -q
```

Expected: 7 passed.

- [ ] **Step 4: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add tests/daemon/test_unix_socket.py
git commit -m "test(daemon): the stage-5a gate — two terminals over one socket

The stage-4 gate asserted N views in-process against a list. This asserts
the same five properties against bytes that crossed a file descriptor,
which is the artifact a user touches. Adds what only a transport can have:
the socket's mode bits, a stale socket file surviving SIGKILL, and a
reattach receiving a full frame."
```

---

## Task 7: `aegis attach` — the dumb pipe

Eighty lines that never grow. Raw mode on, two pipe loops, `SIGWINCH` → a resize frame, restore the terminal on the way out. It imports `aegis.daemon.protocol` and nothing else from aegis — deliberately, because the day it needs a `SessionManager` is the day `--remote` is back.

**Files:**
- Create: `src/aegis/daemon/client.py`
- Test: `tests/daemon/test_attach_client.py` (create)

**Interfaces:**
- Consumes: `FrameDecoder`, `encode_data`, `hello`, `resize` (Task 3).
- Produces: `async def attach(path: str | Path, view_id: str, *, stdin_fd: int = 0, stdout: BinaryIO | None = None) -> None`; `def terminal_size(fd: int) -> tuple[int, int]`.

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/test_attach_client.py`:

```python
"""The client is a pipe. These tests hold it to that.

Driven over a socketpair with an os.pipe standing in for stdin, so the
assertions are about bytes rather than about a tty.
"""
import asyncio
import json
import os

from aegis.daemon.client import attach, terminal_size
from aegis.daemon.protocol import FrameDecoder, encode_data


class _Collector:
    def __init__(self):
        self.buf = bytearray()

    def write(self, data):
        self.buf.extend(data)

    def flush(self):
        return None


async def _fake_daemon(path, out: list, ready: asyncio.Event):
    """Accept one client, record what it sends, feed it a screen."""

    async def handle(reader, writer):
        dec = FrameDecoder()
        ready.set()
        writer.write(encode_data(b"SCREEN"))
        await writer.drain()
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            out.extend(dec.feed(chunk))
        writer.close()

    server = await asyncio.start_unix_server(handle, path=str(path))
    return server


async def test_the_first_frame_is_a_hello_naming_the_view(tmp_path):
    sock = tmp_path / "d.sock"
    got, ready = [], asyncio.Event()
    server = await _fake_daemon(sock, got, ready)
    r_fd, w_fd = os.pipe()
    out = _Collector()
    task = asyncio.create_task(
        attach(sock, "term-a", stdin_fd=r_fd, stdout=out))
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        await asyncio.sleep(0.2)
        kind, payload = got[0]
        assert kind == "M"
        obj = json.loads(payload)
        assert obj["type"] == "hello" and obj["view_id"] == "term-a"
        assert isinstance(obj["width"], int) and obj["width"] >= 1
    finally:
        os.close(w_fd)
        await asyncio.wait_for(task, timeout=5)
        server.close()


async def test_stdin_bytes_are_forwarded_as_data_frames(tmp_path):
    sock = tmp_path / "d.sock"
    got, ready = [], asyncio.Event()
    server = await _fake_daemon(sock, got, ready)
    r_fd, w_fd = os.pipe()
    out = _Collector()
    task = asyncio.create_task(
        attach(sock, "term-a", stdin_fd=r_fd, stdout=out))
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        os.write(w_fd, b"hello")
        async with asyncio.timeout(5):
            while ("D", b"hello") not in got:
                await asyncio.sleep(0.02)
    finally:
        os.close(w_fd)
        await asyncio.wait_for(task, timeout=5)
        server.close()


async def test_data_frames_from_the_daemon_are_unwrapped_to_stdout(tmp_path):
    """The client writes PAYLOADS to the terminal, not frames. Writing the
    frames verbatim also 'shows something', which is why this asserts the
    header is absent rather than that output is non-empty."""
    sock = tmp_path / "d.sock"
    got, ready = [], asyncio.Event()
    server = await _fake_daemon(sock, got, ready)
    r_fd, w_fd = os.pipe()
    out = _Collector()
    task = asyncio.create_task(
        attach(sock, "term-a", stdin_fd=r_fd, stdout=out))
    try:
        async with asyncio.timeout(5):
            while b"SCREEN" not in bytes(out.buf):
                await asyncio.sleep(0.02)
        assert bytes(out.buf) == b"SCREEN"
    finally:
        os.close(w_fd)
        await asyncio.wait_for(task, timeout=5)
        server.close()


async def test_the_client_exits_when_the_daemon_closes(tmp_path):
    sock = tmp_path / "d.sock"
    ready = asyncio.Event()

    async def handle(reader, writer):
        ready.set()
        await reader.read(65536)
        writer.close()

    server = await asyncio.start_unix_server(handle, path=str(sock))
    r_fd, w_fd = os.pipe()
    out = _Collector()
    task = asyncio.create_task(
        attach(sock, "term-a", stdin_fd=r_fd, stdout=out))
    await asyncio.wait_for(ready.wait(), timeout=5)
    await asyncio.wait_for(task, timeout=10)
    os.close(w_fd)
    server.close()


def test_terminal_size_falls_back_when_the_fd_is_not_a_tty():
    r_fd, w_fd = os.pipe()
    try:
        assert terminal_size(r_fd) == (80, 24)
    finally:
        os.close(r_fd)
        os.close(w_fd)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/daemon/test_attach_client.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'aegis.daemon.client'`.

- [ ] **Step 3: Write the client**

Create `src/aegis/daemon/client.py`:

```python
"""`aegis attach`: a pipe with a window size.

It holds no aegis state and parses no aegis concepts — not a session, not
an agent, not a queue. That constraint is the entire difference between
this and the `--remote` client it replaces, whose protocol grew a message
for every feature until it fell behind the TUI. The only aegis import here
is the frame codec, and it should stay the only one.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import termios
import tty
from pathlib import Path
from typing import BinaryIO

from aegis.daemon.protocol import FrameDecoder, encode_data, hello, resize


def terminal_size(fd: int) -> tuple[int, int]:
    """(columns, lines) for ``fd``, or Textual's own 80x24 when it is not a
    terminal — a pipe, or systemd's /dev/null."""
    try:
        size = os.get_terminal_size(fd)
    except OSError:
        return (80, 24)
    return (size.columns or 80, size.lines or 24)


@contextlib.contextmanager
def _raw(fd: int):
    """Raw mode, restored on every exit path including a traceback.

    A client that dies without restoring leaves the user's shell with no
    echo and no line discipline, which reads as a hung terminal.
    """
    try:
        saved = termios.tcgetattr(fd)
    except termios.error:
        yield          # not a tty: nothing to set, nothing to restore
        return
    try:
        tty.setraw(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


async def attach(path: str | Path, view_id: str, *, stdin_fd: int = 0,
                 stdout: BinaryIO | None = None) -> None:
    """Connect to a daemon's unix socket and pipe until either end stops."""
    out = stdout if stdout is not None else sys.stdout.buffer
    reader, writer = await asyncio.open_unix_connection(str(path))
    loop = asyncio.get_running_loop()

    width, height = terminal_size(stdin_fd)
    writer.write(hello(view_id, width, height))
    await writer.drain()

    def _on_winch() -> None:
        w, h = terminal_size(stdin_fd)
        writer.write(resize(w, h))

    with contextlib.suppress(NotImplementedError, ValueError):
        loop.add_signal_handler(signal.SIGWINCH, _on_winch)

    stdin_q: asyncio.Queue = asyncio.Queue()

    def _readable() -> None:
        try:
            data = os.read(stdin_fd, 65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        stdin_q.put_nowait(data)

    async def _pump_in() -> None:
        while True:
            data = await stdin_q.get()
            if not data:
                return
            writer.write(encode_data(data))
            await writer.drain()

    async def _pump_out() -> None:
        decoder = FrameDecoder()
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                return
            for kind, payload in decoder.feed(chunk):
                if kind == "D":
                    out.write(payload)
                    out.flush()
                # Meta from the daemon is Textual's {"type": "exit"} and
                # its delivery packets. Nothing here needs to act on them:
                # the daemon closes the socket when the view ends, and EOF
                # is what this loop already terminates on. Acting on meta
                # would be the client learning aegis concepts.

    with _raw(stdin_fd):
        loop.add_reader(stdin_fd, _readable)
        tasks = [asyncio.create_task(_pump_in()),
                 asyncio.create_task(_pump_out())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            with contextlib.suppress(Exception):
                loop.remove_reader(stdin_fd)
            with contextlib.suppress(NotImplementedError, ValueError):
                loop.remove_signal_handler(signal.SIGWINCH)
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/daemon/test_attach_client.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/daemon/client.py tests/daemon/test_attach_client.py
git commit -m "feat(daemon): aegis attach — a pipe with a window size

Holds no aegis state and parses no aegis concepts. That constraint is the
whole difference between this and the --remote client it replaces, whose
protocol grew a message per feature until it fell behind the TUI. The only
aegis import is the frame codec and it should stay the only one.

Raw mode is restored on every exit path including a traceback: a client
that dies without restoring leaves the shell with no echo, which reads as
a hung terminal rather than as a crash."
```

---

## Task 8: the daemon registry — `aegis ls` and `aegis kill`

Daemons across roots, so the record cannot live under a root. One JSON file per daemon under `~/.aegis/daemons/`, written at boot and removed at exit, with pid liveness as the truth — a file outlives a `SIGKILL`, a pid does not.

**Files:**
- Create: `src/aegis/daemon/registry.py`
- Test: `tests/daemon/test_daemon_registry.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `DaemonRecord` dataclass: `root: Path`, `pid: int`, `socket: Path`, `started: float`, `version: str`
  - `registry_dir() -> Path` (honours `AEGIS_DAEMON_DIR`)
  - `record(rec: DaemonRecord) -> Path`
  - `forget(root: Path) -> None`
  - `live_daemons() -> list[DaemonRecord]` (prunes dead entries as a side effect)
  - `daemon_for(root: Path) -> DaemonRecord | None`
  - `is_alive(pid: int) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/test_daemon_registry.py`:

```python
"""Which daemons are running, across roots."""
import os
import time

import pytest

from aegis.daemon import registry as reg


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))


def _rec(root, pid=None, socket=None):
    return reg.DaemonRecord(
        root=root, pid=pid if pid is not None else os.getpid(),
        socket=socket or (root / ".aegis" / "state" / "daemon.sock"),
        started=time.time(), version="test")


def test_a_recorded_daemon_comes_back(tmp_path):
    reg.record(_rec(tmp_path))
    found = reg.daemon_for(tmp_path)
    assert found is not None and found.root == tmp_path.resolve()


def test_a_dead_pid_is_not_live_and_is_pruned(tmp_path):
    """A record file outlives SIGKILL; a pid does not. The pid is the
    truth, and a stale file must not make `aegis` refuse to autostart."""
    reg.record(_rec(tmp_path, pid=_a_dead_pid()))
    assert reg.daemon_for(tmp_path) is None
    assert reg.live_daemons() == []
    assert list((tmp_path / "daemons").glob("*.json")) == []


def test_two_roots_are_two_daemons(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    reg.record(_rec(a))
    reg.record(_rec(b))
    assert {d.root for d in reg.live_daemons()} == {a.resolve(), b.resolve()}


def test_recording_the_same_root_twice_replaces_it(tmp_path):
    reg.record(_rec(tmp_path))
    reg.record(_rec(tmp_path))
    assert len(reg.live_daemons()) == 1


def test_forget_removes_the_record(tmp_path):
    reg.record(_rec(tmp_path))
    reg.forget(tmp_path)
    assert reg.daemon_for(tmp_path) is None


def test_forget_of_an_unknown_root_is_a_no_op(tmp_path):
    """The exit path runs from a finally block that may never have
    recorded — a daemon that died during boot."""
    reg.forget(tmp_path)


def test_a_damaged_record_is_ignored_not_raised(tmp_path):
    """One corrupt file must not make `aegis ls` unusable for every root."""
    reg.record(_rec(tmp_path))
    d = tmp_path / "daemons"
    for f in d.glob("*.json"):
        f.write_text("{{{", encoding="utf-8")
    assert reg.live_daemons() == []


def _a_dead_pid() -> int:
    """A pid that has certainly exited: fork a child and reap it."""
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    return pid
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/daemon/test_daemon_registry.py -q`
Expected: collection error — `ImportError: cannot import name 'registry'`.

- [ ] **Step 3: Write the registry**

Create `src/aegis/daemon/registry.py`:

```python
"""Which daemons are running, across roots.

`aegis ls` lists daemons for every project, so the record cannot live under
a project. One JSON file per daemon under ``~/.aegis/daemons/``, named by a
hash of the root so a path with a slash in it still names one file.

The file is a hint; the pid is the truth. A record survives SIGKILL, a
process does not, and a stale file that made ``aegis`` refuse to autostart
would be the worst possible failure of this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DaemonRecord:
    root: Path
    pid: int
    socket: Path
    started: float
    version: str = "0"


def registry_dir() -> Path:
    override = os.environ.get("AEGIS_DAEMON_DIR")
    if override:
        return Path(override)
    return Path.home() / ".aegis" / "daemons"


def _key(root: Path) -> str:
    return hashlib.sha256(
        str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]


def _file(root: Path) -> Path:
    return registry_dir() / f"{_key(root)}.json"


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, owned by someone else
    return True


def record(rec: DaemonRecord) -> Path:
    p = _file(rec.root)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "root": str(Path(rec.root).resolve()),
        "pid": rec.pid,
        "socket": str(rec.socket),
        "started": rec.started,
        "version": rec.version,
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(p)
    return p


def forget(root: Path) -> None:
    # The exit path runs from a finally block that may never have recorded.
    try:
        _file(root).unlink()
    except FileNotFoundError:
        pass


def _read(p: Path) -> DaemonRecord | None:
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return DaemonRecord(
            root=Path(raw["root"]), pid=int(raw["pid"]),
            socket=Path(raw["socket"]), started=float(raw["started"]),
            version=str(raw.get("version", "0")))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        # One damaged file must not make `aegis ls` unusable everywhere.
        return None


def live_daemons() -> list[DaemonRecord]:
    """Every running daemon. Prunes records whose process is gone."""
    out: list[DaemonRecord] = []
    d = registry_dir()
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        rec = _read(p)
        if rec is None or not is_alive(rec.pid):
            try:
                p.unlink()
            except OSError:
                pass
            continue
        out.append(rec)
    return out


def daemon_for(root: Path) -> DaemonRecord | None:
    p = _file(root)
    rec = _read(p) if p.is_file() else None
    if rec is None or not is_alive(rec.pid):
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass
        return None
    return rec


def kill(rec: DaemonRecord, sig: int = signal.SIGTERM) -> bool:
    """Signal a daemon. Returns False when it was already gone."""
    try:
        os.kill(rec.pid, sig)
    except ProcessLookupError:
        forget(rec.root)
        return False
    return True


def now() -> float:
    return time.time()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/daemon/test_daemon_registry.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/daemon/registry.py tests/daemon/test_daemon_registry.py
git commit -m "feat(daemon): the cross-root daemon registry

aegis ls lists daemons for every project, so the record cannot live under a
project. One JSON per daemon under ~/.aegis/daemons, keyed by a hash of the
root.

The file is a hint and the pid is the truth: a record survives SIGKILL and
a process does not, so every read prunes what it finds dead. A stale file
that made `aegis` refuse to autostart would be the worst failure this
module could have."
```

---

## Task 9: lifecycle — socket path, autostart, idle reaper

**Idle timeout, settled.** 1800 s, requiring **both** zero views and zero live sessions. Zero-agents is the right second condition and is the reason the VPS daemon never dies: `aegis serve` there always holds sessions, so the timer never arms. On a laptop, closing the last terminal of a project with no agents running reaps the daemon in half an hour. The knob is `AEGIS_IDLE_TIMEOUT` (seconds, `0` disables) rather than a `.aegis.yaml` block, because reaping is a property of the *host's* habits — a laptop versus a server — and not of the project, and `.aegis.yaml` is per-project and committed.

**Files:**
- Create: `src/aegis/daemon/lifecycle.py`, `src/aegis/__main__.py`
- Test: `tests/daemon/test_lifecycle.py` (create)

**`python -m aegis` does not work today.** `pyproject.toml:70` declares one entry point, the `aegis` console script; there is no `src/aegis/__main__.py`. `_spawn_detached` must use `sys.executable` rather than the console script — the daemon has to be startable from a `uvx` or bare-venv context where `aegis` may not be on `PATH`, while `sys.executable` always is — so this task adds the three-line `__main__.py` that makes `-m` real. Without it the autostart fails with `No module named aegis.__main__` on a detached stderr pointed at `/dev/null`, i.e. silently.

**Interfaces:**
- Consumes: `registry` (Task 8), `AegisRoots` (existing).
- Produces:
  - `socket_path(roots: AegisRoots) -> Path` — `roots.state_dir / "daemon.sock"`
  - `IdleReaper(registry, manager, *, timeout_s: float, stop: asyncio.Event, interval_s: float = 5.0)` with `async def run(self) -> None`
  - `idle_timeout_s() -> float` — reads `AEGIS_IDLE_TIMEOUT`, default 1800.0, `0` meaning never
  - `async def ensure_daemon(root: Path, *, timeout_s: float = 20.0) -> Path` — returns a connectable socket path, spawning `aegis serve` detached if needed
  - `SpawnFailed(Exception)`

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/test_lifecycle.py`:

```python
"""Socket paths, the idle reaper, and autostart."""
import asyncio

import pytest

from aegis.config.roots import AegisRoots
from aegis.daemon import lifecycle


class _Registry:
    def __init__(self, views):
        self._views = list(views)

    def list(self):
        return list(self._views)


class _Manager:
    def __init__(self, sessions):
        self._sessions = list(sessions)

    def list_sessions(self):
        return list(self._sessions)


def test_the_socket_lives_under_this_roots_state_dir(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    assert lifecycle.socket_path(roots) == roots.state_dir / "daemon.sock"


def test_the_default_idle_timeout_is_thirty_minutes(monkeypatch):
    monkeypatch.delenv("AEGIS_IDLE_TIMEOUT", raising=False)
    assert lifecycle.idle_timeout_s() == 1800.0


def test_zero_disables_the_idle_timeout(monkeypatch):
    monkeypatch.setenv("AEGIS_IDLE_TIMEOUT", "0")
    assert lifecycle.idle_timeout_s() == 0.0


def test_a_garbage_idle_timeout_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("AEGIS_IDLE_TIMEOUT", "soon")
    assert lifecycle.idle_timeout_s() == 1800.0


async def test_an_idle_daemon_is_reaped():
    stop = asyncio.Event()
    reaper = lifecycle.IdleReaper(
        _Registry([]), _Manager([]), timeout_s=0.05, stop=stop,
        interval_s=0.01)
    await asyncio.wait_for(reaper.run(), timeout=5)
    assert stop.is_set()


async def test_a_daemon_with_a_view_is_not_reaped():
    stop = asyncio.Event()
    reaper = lifecycle.IdleReaper(
        _Registry(["term-a"]), _Manager([]), timeout_s=0.05, stop=stop,
        interval_s=0.01)
    task = asyncio.create_task(reaper.run())
    await asyncio.sleep(0.3)
    assert not stop.is_set()
    task.cancel()


async def test_a_daemon_with_a_live_session_is_not_reaped():
    """The VPS case. `aegis serve` there always holds sessions, so the
    timer never arms and the daemon never dies."""
    stop = asyncio.Event()
    reaper = lifecycle.IdleReaper(
        _Registry([]), _Manager(["agent-1"]), timeout_s=0.05, stop=stop,
        interval_s=0.01)
    task = asyncio.create_task(reaper.run())
    await asyncio.sleep(0.3)
    assert not stop.is_set()
    task.cancel()


async def test_the_idle_clock_restarts_when_a_view_attaches():
    """Idleness is a contiguous run, not a total. A daemon used every 20
    minutes for a day must never be reaped mid-use."""
    stop = asyncio.Event()
    registry = _Registry([])
    reaper = lifecycle.IdleReaper(
        registry, _Manager([]), timeout_s=0.3, stop=stop, interval_s=0.01)
    task = asyncio.create_task(reaper.run())
    await asyncio.sleep(0.2)
    registry._views.append("term-a")       # someone attached
    await asyncio.sleep(0.2)
    registry._views.clear()                # and left again
    await asyncio.sleep(0.15)
    assert not stop.is_set(), "the clock did not restart on attach"
    task.cancel()


async def test_a_zero_timeout_never_reaps():
    stop = asyncio.Event()
    reaper = lifecycle.IdleReaper(
        _Registry([]), _Manager([]), timeout_s=0.0, stop=stop,
        interval_s=0.01)
    task = asyncio.create_task(reaper.run())
    await asyncio.sleep(0.2)
    assert not stop.is_set()
    task.cancel()


async def test_ensure_daemon_returns_the_existing_socket_without_spawning(
        tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))
    roots = AegisRoots.for_project(tmp_path)
    sock = lifecycle.socket_path(roots)
    sock.parent.mkdir(parents=True, exist_ok=True)

    async def handle(reader, writer):
        writer.close()

    server = await asyncio.start_unix_server(handle, path=str(sock))
    spawned = []
    monkeypatch.setattr(lifecycle, "_spawn_detached",
                        lambda root: spawned.append(root))
    try:
        got = await lifecycle.ensure_daemon(tmp_path, timeout_s=5)
        assert got == sock
        assert spawned == [], "autostart ran against a live daemon"
    finally:
        server.close()


async def test_ensure_daemon_raises_when_the_spawn_never_listens(
        tmp_path, monkeypatch):
    """A daemon that dies during boot must fail the attach with a real
    error, not hang the terminal forever."""
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))
    monkeypatch.setattr(lifecycle, "_spawn_detached", lambda root: None)
    with pytest.raises(lifecycle.SpawnFailed):
        await lifecycle.ensure_daemon(tmp_path, timeout_s=0.5)


def test_python_dash_m_aegis_is_runnable():
    """_spawn_detached launches `sys.executable -m aegis serve`, because the
    daemon must start from a uvx or bare-venv context where the console
    script may not be on PATH. Without __main__.py that fails with
    `No module named aegis.__main__` — on a stderr pointed at /dev/null,
    which is to say silently. Assert the module, not the message."""
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "aegis", "--version"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "aegis" in (r.stdout + r.stderr).lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/daemon/test_lifecycle.py -q`
Expected: collection error — `ImportError: cannot import name 'lifecycle'`.

- [ ] **Step 3: Write the modules**

Create `src/aegis/__main__.py`:

```python
"""`python -m aegis`. The console script at pyproject.toml:70 is the same
entry point, but the daemon is autostarted with sys.executable — which
always exists — rather than with a console script that may not be on the
PATH of a uvx or bare-venv invocation.
"""
from aegis.cli import main

main()
```

Create `src/aegis/daemon/lifecycle.py`:

```python
"""Starting a daemon, finding one, and reaping an idle one."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import sys
from pathlib import Path

from aegis.config.roots import AegisRoots

log = logging.getLogger(__name__)

DEFAULT_IDLE_TIMEOUT_S = 1800.0


class SpawnFailed(Exception):
    """An autostarted daemon never came up."""


def socket_path(roots: AegisRoots) -> Path:
    return roots.state_dir / "daemon.sock"


def idle_timeout_s() -> float:
    """Seconds of contiguous idleness before a daemon reaps itself.

    An environment variable rather than a `.aegis.yaml` block: reaping is a
    property of the host's habits — a laptop that should self-clean versus
    a server that should not — and `.aegis.yaml` is per-project and
    committed, so a laptop policy would follow the repo onto the VPS.
    """
    raw = os.environ.get("AEGIS_IDLE_TIMEOUT")
    if raw is None:
        return DEFAULT_IDLE_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_IDLE_TIMEOUT_S
    return max(0.0, value)


class IdleReaper:
    """Sets ``stop`` after ``timeout_s`` of zero views AND zero sessions.

    Both conditions, deliberately. Zero views alone would reap the VPS
    daemon every night — it runs agents nobody is watching, which is the
    entire point of it. Zero sessions alone would never fire on a laptop
    with a stale tab open.

    Idleness is a contiguous run, not a total: any view or session resets
    the clock, so a daemon touched every 20 minutes all day is never reaped
    mid-use.
    """

    def __init__(self, registry, manager, *, timeout_s: float,
                 stop: asyncio.Event, interval_s: float = 5.0) -> None:
        self._registry = registry
        self._manager = manager
        self._timeout = timeout_s
        self._stop = stop
        self._interval = interval_s

    def _idle(self) -> bool:
        if self._registry.list():
            return False
        try:
            return not self._manager.list_sessions()
        except Exception:  # noqa: BLE001
            return False   # cannot tell => not idle; never reap on a guess

    async def run(self) -> None:
        if self._timeout <= 0:
            return
        idle_for = 0.0
        while not self._stop.is_set():
            await asyncio.sleep(self._interval)
            if self._idle():
                idle_for += self._interval
                if idle_for >= self._timeout:
                    log.info("daemon idle for %.0fs; exiting", idle_for)
                    self._stop.set()
                    return
            else:
                idle_for = 0.0


def _spawn_detached(root: Path) -> None:
    """Fork `aegis serve` into its own session, detached from this tty.

    ``start_new_session`` is what makes it survive the terminal that
    started it: without it the daemon is in the attaching shell's process
    group and dies with the terminal, which is the one thing a daemon may
    not do.
    """
    subprocess.Popen(
        [sys.executable, "-m", "aegis", "serve", "--cwd", str(root)],
        cwd=str(root), start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def _connectable(path: Path) -> bool:
    try:
        reader, writer = await asyncio.open_unix_connection(str(path))
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


async def ensure_daemon(root: Path, *, timeout_s: float = 20.0) -> Path:
    """Return a connectable socket for ``root``, starting one if needed.

    Liveness is "the socket accepts a connection", not "the file exists":
    a SIGKILLed daemon leaves the file behind, and a stale file that
    satisfied this check would make every `aegis` invocation hang against
    a dead socket.
    """
    roots = AegisRoots.for_project(Path(root))
    path = socket_path(roots)
    if await _connectable(path):
        return path

    _spawn_detached(Path(root))
    waited = 0.0
    step = 0.05
    while waited < timeout_s:
        await asyncio.sleep(step)
        waited += step
        if await _connectable(path):
            return path
    raise SpawnFailed(
        f"daemon for {root} did not come up within {timeout_s:.0f}s; "
        f"try `aegis serve --cwd {root}` in a terminal to see why")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/daemon/test_lifecycle.py -q`
Expected: 13 passed.

- [ ] **Step 5: Mutation-check the reaper's second condition**

The zero-sessions condition is the one that keeps the VPS daemon alive, and a reaper that ignored it would still pass most of this file.

```bash
cd /home/apiad/Workspace/repos/aegis
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/aegis/daemon/lifecycle.py"); s = p.read_text()
assert "MUTANT" not in s
p.write_text(s.replace(
    "            return not self._manager.list_sessions()",
    "            return True  # MUTANT: ignore live sessions"))
PY
uv run pytest tests/daemon/test_lifecycle.py -q
```

Expected: `test_a_daemon_with_a_live_session_is_not_reaped` FAILS. Restore:

```bash
cd /home/apiad/Workspace/repos/aegis
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/aegis/daemon/lifecycle.py"); s = p.read_text()
p.write_text(s.replace(
    "            return True  # MUTANT: ignore live sessions",
    "            return not self._manager.list_sessions()"))
assert "MUTANT" not in p.read_text()
print("restored")
PY
uv run pytest tests/daemon/test_lifecycle.py -q
```

Expected: 13 passed.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/__main__.py src/aegis/daemon/lifecycle.py tests/daemon/test_lifecycle.py
git commit -m "feat(daemon): socket path, autostart, and the idle reaper

Settles the spec's open question 1: 1800s, requiring BOTH zero views and
zero live sessions. Zero views alone would reap the VPS daemon nightly —
it runs agents nobody watches, which is the point of it. Idleness is a
contiguous run, so a daemon touched every 20 minutes is never reaped
mid-use.

AEGIS_IDLE_TIMEOUT rather than a .aegis.yaml block: reaping is a property
of the host's habits, and .aegis.yaml is per-project and committed, so a
laptop policy would follow the repo onto the VPS.

ensure_daemon tests liveness by connecting, not by stat: a SIGKILLed
daemon leaves its socket file behind, and trusting the file would hang
every subsequent `aegis` against a dead socket."
```

---

## Task 10: wire it into `aegis serve`

The daemon *is* `aegis serve`. It gains a `ViewRegistry`, a socket server, a registry record and an idle reaper, and keeps everything else — MCP plane, queues, scheduler, and `web=` (which stage 6 removes, not this one).

**Files:**
- Modify: `src/aegis/cli.py:537-670` (`_serve`), `:887` (`_run_serve`)
- Test: `tests/daemon/test_serve_wiring.py` (create)

**Interfaces:**
- Consumes: `UnixSocketServer` (Task 5), `registry`/`DaemonRecord` (Task 8), `socket_path`/`IdleReaper`/`idle_timeout_s` (Task 9), `ViewRegistry` (stage 4).
- Produces: `_serve(..., views: bool = False)`. When True, `_serve` builds a `ViewRegistry` over the manager, starts a `UnixSocketServer` on `socket_path(roots)`, records the daemon, runs an `IdleReaper`, and tears all of it down in its `finally`.

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/test_serve_wiring.py`:

```python
"""`aegis serve` with views=True is the daemon.

Drives the real _serve coroutine against a temp root and connects a real
client to the socket it publishes — not a hand-built ViewRegistry, which is
what every other test in tests/daemon does and which would pass while
_serve's own wiring was missing.
"""
import asyncio

import pytest

from aegis.cli import _serve
from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.daemon import lifecycle
from aegis.daemon import registry as dreg
from aegis.daemon.protocol import FrameDecoder, hello

from tests.views.conftest import FakeMCP


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))
    monkeypatch.setenv("AEGIS_IDLE_TIMEOUT", "0")


async def _boot(tmp_path, stop):
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": Agent(harness="claude-code", model="opus",
                               effort="high", permission="auto")}
    task = asyncio.create_task(_serve(
        roots=roots, agents=roster, default_agent="default",
        make_session=lambda p, u, h: _FakeHarness(), mcp=FakeMCP(),
        stop=stop, views=True))
    return roots, task


async def _until(predicate, timeout=15.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def test_serve_publishes_a_socket_a_client_can_attach_to(tmp_path):
    stop = asyncio.Event()
    roots, task = await _boot(tmp_path, stop)
    sock = lifecycle.socket_path(roots)
    try:
        await _until(sock.exists)
        r, w = await asyncio.open_unix_connection(str(sock))
        w.write(hello("term-a", 80, 24))
        await w.drain()
        dec = FrameDecoder()
        screen = bytearray()
        async with asyncio.timeout(15):
            while len(screen) < 500:
                chunk = await r.read(65536)
                if not chunk:
                    break
                for kind, payload in dec.feed(chunk):
                    if kind == "D":
                        screen.extend(payload)
        assert screen, "attached to serve's socket and got no screen"
        w.close()
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=30)


async def test_serve_records_itself_in_the_daemon_registry(tmp_path):
    stop = asyncio.Event()
    roots, task = await _boot(tmp_path, stop)
    try:
        await _until(lambda: dreg.daemon_for(tmp_path) is not None)
        rec = dreg.daemon_for(tmp_path)
        assert rec.socket == lifecycle.socket_path(roots)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=30)


async def test_serve_forgets_itself_and_removes_the_socket_on_exit(tmp_path):
    stop = asyncio.Event()
    roots, task = await _boot(tmp_path, stop)
    await _until(lambda: dreg.daemon_for(tmp_path) is not None)
    stop.set()
    await asyncio.wait_for(task, timeout=30)
    assert dreg.daemon_for(tmp_path) is None
    assert not lifecycle.socket_path(roots).exists()


async def test_serve_without_views_publishes_no_socket(tmp_path):
    """views=False is today's headless serve, unchanged."""
    stop = asyncio.Event()
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": Agent(harness="claude-code", model="opus",
                               effort="high", permission="auto")}
    task = asyncio.create_task(_serve(
        roots=roots, agents=roster, default_agent="default",
        make_session=lambda p, u, h: _FakeHarness(), mcp=FakeMCP(),
        stop=stop))
    try:
        await asyncio.sleep(0.5)
        assert not lifecycle.socket_path(roots).exists()
        assert dreg.daemon_for(tmp_path) is None
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=30)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/daemon/test_serve_wiring.py -q`
Expected: FAIL — `TypeError: _serve() got an unexpected keyword argument 'views'`.

- [ ] **Step 3: Wire `_serve`**

In `src/aegis/cli.py`, add the parameter to `_serve`'s signature, after `ui`:

```python
                 views: bool = False) -> None:
```

Replace the `tasks = []` / `if web is not None:` block with:

```python
    tasks = []
    view_registry = None
    socket_server = None
    if views:
        # The daemon. A registry of views over this one brain, published on
        # a unix socket that `aegis attach` pipes. `ui` is the OTHER shape
        # — one view that owns the process — and the two are exclusive.
        from aegis.daemon import registry as _dreg
        from aegis.daemon.lifecycle import (
            IdleReaper, idle_timeout_s, socket_path,
        )
        from aegis.daemon.server import UnixSocketServer
        from aegis.views.registry import ViewRegistry
        view_registry = ViewRegistry(
            manager=mgr, roots=roots, mcp=mcp,
            agents=agents, default_agent=default_agent,
            make_session=make_session, queues=queues or {},
            hosts=hosts or {}, host_registry=host_registry,
            cwd=str(roots.harness_cwd))
        socket_server = UnixSocketServer(socket_path(roots), view_registry)
        await socket_server.start()
        _dreg.record(_dreg.DaemonRecord(
            root=roots.state_root, pid=os.getpid(),
            socket=socket_server.path, started=_dreg.now(),
            version=_aegis_version()))
        tasks.append(asyncio.create_task(IdleReaper(
            view_registry, mgr, timeout_s=idle_timeout_s(),
            stop=stop).run()))
    if web is not None:
        from aegis.web.frontend import WebFrontend
        web_fe = WebFrontend(mgr, web, state_dir=roots.state_dir,
                             server_version=_aegis_version())
        tasks.append(asyncio.create_task(web_fe.run()))
        _console.print(f"[green]web UI on {web_fe.url}[/green]")
```

and extend the `finally` block, before `await qm.stop()`:

```python
        if socket_server is not None:
            await socket_server.stop()
        if view_registry is not None:
            await view_registry.close_all()
        if views:
            from aegis.daemon import registry as _dreg
            _dreg.forget(roots.state_root)
```

Add `import os` to the module's imports if it is not already there (check with `grep -n "^import os" src/aegis/cli.py`).

In `_run_serve`, pass the flag — `aegis serve` *is* the daemon now:

```python
                     inline_schedule_names=inline_schedule_names,
                     views=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/daemon/test_serve_wiring.py -q`
Expected: 4 passed.

- [ ] **Step 5: Run the CLI suite for regressions**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/cli -q`
Expected: all pass. `views` defaults False, so the `aegis` (non-daemon) path in `run()` is unchanged until Task 11.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/cli.py tests/daemon/test_serve_wiring.py
git commit -m "feat(cli): aegis serve is the daemon

_serve gains views=: a ViewRegistry over the one brain, a unix socket, a
registry record and an idle reaper, torn down in the same finally as the
rest. Everything else is untouched — the MCP plane, the queues, the
scheduler, and web=, which stage 6 removes and this stage does not.

views= and ui= are the two exclusive shapes: a daemon with N views, or one
view that owns the process."
```

---

## Task 11: `aegis`, `aegis attach`, `aegis ls`, `aegis kill`

The command surface. `aegis` becomes ensure-daemon-then-attach; `--foreground` keeps today's path byte-for-byte, which is the escape hatch for CI, `uvx aegis`, and debugging the daemon itself.

**Files:**
- Modify: `src/aegis/cli.py` — `run()` at `:149`, plus three new `@app.command()`s
- Test: `tests/cli/test_daemon_commands.py` (create)

**Interfaces:**
- Consumes: `attach` (Task 7), `ensure_daemon`/`SpawnFailed` (Task 9), `live_daemons`/`daemon_for`/`kill` (Task 8), `_tty_view_id` (`cli.py:320`, existing).
- Produces: CLI commands `aegis attach [--view ID] [--cwd DIR]`, `aegis ls`, `aegis kill [--cwd DIR] [--all]`; `aegis --foreground`.

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_daemon_commands.py`:

```python
"""The command surface. Typer-level, with the transport stubbed — the
socket is tested in tests/daemon; what is unproven here is the wiring."""
import time

import pytest
from typer.testing import CliRunner

from aegis import cli
from aegis.daemon import registry as dreg

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))


def _record(root, pid):
    dreg.record(dreg.DaemonRecord(
        root=root, pid=pid, socket=root / "d.sock", started=time.time(),
        version="test"))


def test_ls_lists_a_live_daemon(tmp_path):
    import os
    _record(tmp_path, os.getpid())
    result = runner.invoke(cli.app, ["ls"])
    assert result.exit_code == 0
    assert str(tmp_path) in result.output


def test_ls_with_no_daemons_says_so_and_exits_zero(tmp_path):
    result = runner.invoke(cli.app, ["ls"])
    assert result.exit_code == 0
    assert "no aegis daemons" in result.output.lower()


def test_kill_signals_the_daemon_for_this_root(tmp_path, monkeypatch):
    import os
    _record(tmp_path, os.getpid())
    signalled = []
    monkeypatch.setattr(dreg, "kill",
                        lambda rec, **kw: signalled.append(rec.pid) or True)
    result = runner.invoke(cli.app, ["kill", "--cwd", str(tmp_path)])
    assert result.exit_code == 0
    assert signalled == [os.getpid()]


def test_kill_with_no_daemon_exits_nonzero(tmp_path):
    result = runner.invoke(cli.app, ["kill", "--cwd", str(tmp_path)])
    assert result.exit_code != 0


def test_attach_ensures_a_daemon_then_pipes(tmp_path, monkeypatch):
    calls = {}

    async def _fake_ensure(root, **kw):
        calls["root"] = root
        return tmp_path / "d.sock"

    async def _fake_attach(path, view_id, **kw):
        calls["path"] = path
        calls["view_id"] = view_id

    monkeypatch.setattr(cli, "_ensure_daemon", _fake_ensure)
    monkeypatch.setattr(cli, "_attach", _fake_attach)
    result = runner.invoke(cli.app, ["attach", "--cwd", str(tmp_path),
                                     "--view", "review"])
    assert result.exit_code == 0, result.output
    assert calls["view_id"] == "review"
    assert calls["path"] == tmp_path / "d.sock"


def test_attach_reports_a_failed_autostart_instead_of_hanging(
        tmp_path, monkeypatch):
    from aegis.daemon.lifecycle import SpawnFailed

    async def _boom(root, **kw):
        raise SpawnFailed("did not come up")

    monkeypatch.setattr(cli, "_ensure_daemon", _boom)
    result = runner.invoke(cli.app, ["attach", "--cwd", str(tmp_path)])
    assert result.exit_code != 0
    assert "did not come up" in result.output


def test_foreground_takes_the_old_path_not_the_daemon(tmp_path, monkeypatch):
    """--foreground is the escape hatch for CI, uvx and debugging the
    daemon itself. It must not touch ensure_daemon."""
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: a\nagents:\n  a:\n    harness: claude-code\n",
        encoding="utf-8")
    touched = []

    async def _never(root, **kw):
        touched.append(root)
        return tmp_path / "d.sock"

    monkeypatch.setattr(cli, "_ensure_daemon", _never)
    ran = []
    monkeypatch.setattr(cli.asyncio, "run", lambda coro: ran.append(coro)
                        or coro.close())
    monkeypatch.chdir(tmp_path)
    runner.invoke(cli.app, ["--foreground"])
    assert touched == [], "--foreground went through the daemon"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/cli/test_daemon_commands.py -q`
Expected: FAIL — `No such command 'ls'` and friends.

- [ ] **Step 3: Add the commands**

In `src/aegis/cli.py`, add these near `_tty_view_id` (the seams the tests monkeypatch — they exist so a Typer-level test can stub the transport without stubbing an `async def` inside a command body):

```python
async def _ensure_daemon(root: Path, **kw):
    from aegis.daemon.lifecycle import ensure_daemon
    return await ensure_daemon(root, **kw)


async def _attach(path, view_id: str, **kw):
    from aegis.daemon.client import attach
    return await attach(path, view_id, **kw)


def _attach_to_daemon(root: Path, view_id: str) -> None:
    """Ensure a daemon for ``root`` and pipe this terminal to it."""
    from aegis.daemon.lifecycle import SpawnFailed

    async def _go():
        path = await _ensure_daemon(root)
        await _attach(path, view_id)

    try:
        asyncio.run(_go())
    except SpawnFailed as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e
    rec = None
    try:
        from aegis.daemon import registry as _dreg
        rec = _dreg.daemon_for(root)
    except Exception:  # noqa: BLE001
        pass
    if rec is not None:
        _console.print(
            f"[dim]brain running (pid {rec.pid}) · "
            f"`aegis kill` to stop[/dim]")
```

Add the three commands beside `serve`:

```python
@app.command()
def attach(
    view: str = typer.Option(None, "--view",
                             help="View id. Defaults to this terminal."),
    cwd: str = typer.Option(".", "--cwd",
                            help="Project root whose daemon to attach."),
) -> None:
    """Attach this terminal to the daemon for a project root."""
    root = Path(cwd).resolve() if cwd != "." else (
        find_project_root() or Path.cwd())
    _attach_to_daemon(root, view or _tty_view_id())


@app.command("ls")
def ls_cmd() -> None:
    """List running aegis daemons across all project roots."""
    from aegis.daemon import registry as _dreg
    daemons = _dreg.live_daemons()
    if not daemons:
        _console.print("no aegis daemons running")
        return
    for rec in daemons:
        age = max(0, int(_dreg.now() - rec.started))
        _console.print(f"{rec.pid:>8}  up {age:>6}s  {rec.root}")


@app.command("kill")
def kill_cmd(
    cwd: str = typer.Option(".", "--cwd",
                            help="Project root whose daemon to stop."),
    all_: bool = typer.Option(False, "--all",
                              help="Stop every running daemon."),
) -> None:
    """Stop the daemon for a project root (or all of them)."""
    from aegis.daemon import registry as _dreg
    if all_:
        daemons = _dreg.live_daemons()
        if not daemons:
            _console.print("no aegis daemons running")
            return
        for rec in daemons:
            _dreg.kill(rec)
            _console.print(f"stopped {rec.pid} ({rec.root})")
        return
    root = Path(cwd).resolve() if cwd != "." else (
        find_project_root() or Path.cwd())
    rec = _dreg.daemon_for(root)
    if rec is None:
        _console.print(f"[red]no aegis daemon for {root}[/red]")
        raise typer.Exit(1)
    _dreg.kill(rec)
    _console.print(f"stopped {rec.pid} ({rec.root})")
```

In `run()`, add the option beside `--clean`:

```python
    foreground: bool = typer.Option(
        False, "--foreground",
        help="Run the brain and one view in this process (CI, uvx, "
             "debugging the daemon). The pre-daemon shape."),
```

and insert the daemon branch in `run()` **immediately after the bootstrap-mode block** (the `if not (root / ".aegis.yaml").is_file():` … `return` at `cli.py:210-216`) and *before* the `effective_cwd = …` line. `root` is in scope there; `effective_cwd`, `roots` and `load_boot_config` are all below it, and the client needs none of them:

```python
    if not foreground:
        # `aegis` is a client. The brain is a daemon, started on demand.
        # Config errors surface from the daemon's own boot, which is where
        # they can be read (`aegis serve --cwd …` in a terminal).
        _attach_to_daemon(root, _tty_view_id())
        return
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/cli/test_daemon_commands.py -q`
Expected: 7 passed.

- [ ] **Step 5: Run the full CLI suite**

Run: `cd /home/apiad/Workspace/repos/aegis && uv run pytest tests/cli -q`
Expected: all pass. Any test that invoked `aegis` expecting the in-process TUI must now pass `--foreground`; fix those call sites rather than removing the branch.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/cli.py tests/cli/test_daemon_commands.py
git commit -m "feat(cli): aegis is a client — attach, ls, kill, --foreground

`aegis` ensures a daemon for this root and pipes this terminal to it.
--foreground keeps the pre-daemon shape for CI, uvx and debugging the
daemon itself, and is the branch that makes this revertible in practice
rather than in principle.

The client path deliberately does not load the boot config: it needs no
agents and no queues, and a config error is legible where it happens —
in `aegis serve`, which can be run in a terminal and read."
```

---

## Task 12: live-drive it, then the docs

Nothing above has been driven by a human through a real terminal. Per the workspace rule on verifying the artifact rather than something adjacent to it, this task is the artifact.

**Files:**
- Modify: `TASKS.md`, `AGENTS.md`, `README.md`, `docs/superpowers/plans/2026-09-12-aegis-local-daemon.md` (this file's status header)
- Create: `know-how/the-daemon.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing code-facing.

- [ ] **Step 1: Run the whole suite**

```bash
cd /home/apiad/Workspace/repos/aegis
nohup bash -c 'uv run python -m pytest -q -m "not live" > /tmp/aegis-5a.log 2>&1; echo "DONE rc=$?" >> /tmp/aegis-5a.log' > /dev/null 2>&1 &
```

Wait on it with `aegis_monitor` (`done: grep -q DONE /tmp/aegis-5a.log`, `progress` counting `grep -c PASSED`), never a sleep loop. Expected: no regression against 3642 passed / 1 skipped, plus this plan's ~60 new tests.

- [ ] **Step 2: Drive it by hand, in a real terminal**

This is the step that cannot be delegated to the suite. In a terminal (not through a pilot, not through a pipe):

```bash
cd /home/apiad/Workspace/repos/aegis
uv run aegis ls                 # expect: no aegis daemons running
uv run aegis                    # expect: the TUI, from a daemon
```

Then, in that TUI: spawn a session, press `Ctrl+Q`. Expect the terminal to return with `brain running (pid N) · aegis kill to stop`. Then:

```bash
uv run aegis ls                 # expect: one daemon, this root
uv run aegis                    # expect: reattach; the session is still there
```

Open a **second** terminal at the same root, run `uv run aegis`, and confirm: both terminals render at their own sizes, a tab spawned in one appears in the other, resizing one does not resize the other. Then `uv run aegis kill` and confirm `aegis ls` is empty and the socket file is gone.

Record the outcome verbatim in the commit message — including anything that did not work. If something is broken, fix it and re-drive; do not write "works" from the suite being green.

- [ ] **Step 3: Write the know-how**

Create `know-how/the-daemon.md`, in the house shape (a *when to reach for it* line first). It must cover: the two shapes (`aegis` versus `aegis --foreground`), where the socket lives, what `ls`/`kill` do, `AEGIS_IDLE_TIMEOUT`, the fact that `Ctrl+Q` now detaches, and how to debug a daemon that will not start (`aegis serve --cwd <root>` in a terminal, and `.aegis/state/aegis.log`).

- [ ] **Step 4: Update the indexes and this plan's status**

- `AGENTS.md` — add the `know-how/the-daemon.md` index entry with its *when to reach for it* line.
- `README.md` — the command table gains `attach`, `ls`, `kill`, `--foreground`.
- `TASKS.md` — flip row 2b to name 5a shipped and 5b outstanding; update the *Next action* paragraph.
- This file — change the status header from **not started** to shipped, with the commit range and the suite numbers.
- Check off every `- [ ]` in this plan that was executed.

- [ ] **Step 5: Run `rift check`**

Run: `cd /home/apiad/Workspace/repos/aegis && rift check`
Expected: no new errors. The repo lints that every rule is documented in `AGENTS.md`; a new know-how file that the index does not mention is exactly what it catches.

- [ ] **Step 6: Commit and push**

```bash
cd /home/apiad/Workspace/repos/aegis
git add TASKS.md AGENTS.md README.md know-how/the-daemon.md \
        docs/superpowers/plans/2026-09-12-aegis-local-daemon.md
git commit -m "docs: the local daemon ships — aegis is a client

<paste the hand-driven results from Step 2 here, verbatim, including
anything that did not work the first time>"
git push origin main
```

---

## Done when

- `uv run pytest -q -m "not live"` is green with no regression against the 3642 baseline.
- `tests/daemon/test_unix_socket.py` passes **and** goes red under the repaint mutation; `tests/views/test_view_input.py` goes red under the framing mutation; `tests/daemon/test_lifecycle.py` goes red under the live-sessions mutation.
- Alex can run `aegis`, spawn an agent, `Ctrl+Q`, and find the agent still running on reattach — **driven by hand in a terminal**, not inferred from a green suite.
- Two terminals at one root render at their own geometries, and a tab opened in one appears in the other.
- `aegis ls` and `aegis kill` work; the socket file and the registry record are both gone afterwards.
- `aegis --foreground`, `aegis serve`, `aegis web` and `aegis --remote` are observably unchanged.

## Deliberately not in this plan

- **The WebSocket transport, the token handshake, and `aegis attach wss://…`.** Stage 5b.
- **The browser terminal view** (vendored xterm.js, the cookie exchange, the soft-keyboard shim) and **dropping Caddy `basicauth`**. Stage 5b, together, because taking `basicauth` off a live public hostname without the replacement in the same change leaves `dev.apiad.net` open with `permission: full`.
- **Transport equivalence** — the assertion that the unix and WS transports emit byte-identical frames. It needs two transports; it is 5b's gate.
- **Any deletion.** The web client, the WS plane, `RemoteSessionManager`, `ws_client`, `--remote` and their tests all survive this stage untouched. Stage 6.
- **A `.aegis.yaml` `daemon:` block.** `AEGIS_IDLE_TIMEOUT` covers the one knob, for the reason in Task 9. Add the block when a second knob exists.
- **Tab order on reorder** (spec open question 4). Order is brain state and reordering in one view reorders for everyone; nothing in this plan changes that, and confirming it is wanted is a product question better answered with two terminals in front of Alex — which Task 12 Step 2 puts there.

## Open questions 5b must settle

1. **Where the remote token lives.** The spec says `~/.aegis/tokens/<host>`, with `--token` overriding. Not built here; nothing in 5a reads a token.
2. **Whether the browser view gets its own view-id namespace.** A browser mints its id in `localStorage`; a terminal derives one from its tty (`_tty_view_id`). Both are client-minted strings validated by `parse_hello`, so they already share one namespace — confirm a collision between them is acceptable or prefix them.
3. **What `aegis web` becomes.** The spec's command table says "open a browser at the daemon's URL — no longer a server". That change belongs with the browser view, not before it.
