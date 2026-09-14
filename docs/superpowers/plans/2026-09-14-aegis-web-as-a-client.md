# `aegis web` as a client — stage 5b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status: not started.** Written 2026-09-14 against `0eac50d`.

**Goal:** `aegis web` becomes its own process, a client of the daemon's unix
socket that serves one view per browser tab to xterm.js; the daemon stops
binding a web port and `aegis serve` is renamed `aegis server`.

**Architecture:** A browser page runs xterm.js and speaks the daemon's own
frame codec (`b"D"`/`b"M"`, four-byte big-endian length, payload) over a
WebSocket to `aegis web`. `aegis web` checks a cookie, then relays those
frames byte for byte to the daemon's unix socket, where `serve_view` treats
the browser exactly like `aegis attach`. `aegis web` parses one thing: the
hello and resize frames, so it can reopen the same view at the current size
when the daemon restarts. It knows no session, agent or queue.

**Tech Stack:** Python 3.13, Starlette 1.0, uvicorn, websockets 16, Textual
(daemon side, unchanged), xterm.js 6.0.0 and @xterm/addon-fit 0.11.0
vendored as ES modules, node 22 for the page's unit tests.

**Spec:** `docs/superpowers/specs/2026-09-13-aegis-web-as-a-client-design.md`
(accepted, with its *Decisions*) and the amended stage 5 of
`docs/superpowers/specs/2026-09-07-retire-web-ui-tui-over-web-design.md`.

## Global Constraints

- The daemon (`aegis server`) binds no web port. The MCP plane's own port is not the web port and is untouched.
- `src/aegis/webterm/` imports nothing from `aegis.core`, `aegis.tui`, `aegis.views`, `aegis.web` or `aegis.mcp`. Task 2 adds the test that enforces it.
- The old aegis-aware web layer (`src/aegis/web/`) stays in the tree, unwired. Stage 6 deletes it. Do not edit it.
- Do not redeploy the VPS during this plan. dev.apiad.net keeps serving the version deployed there until stage 6.
- The token appears in exactly one URL, the one-time `/?t=` login, which is exchanged for an `HttpOnly; Secure; SameSite=Strict` cookie. uvicorn's access log stays off.
- Browser view ids are `web-<uuid>` from `crypto.randomUUID()`, kept in `sessionStorage` under `aegis-view`: one view per tab.
- No new Python dependency and no npm build step. Vendored files keep their MIT `LICENSE` beside them and their versions in `VERSIONS`.
- `aegis serve` stays as a hidden alias of `aegis server`: `aegis bench --target 0.37.0` starts releases that only know `serve`.
- Work on `main`. This is a shared checkout: `git add` only new files by explicit path, commit with `git commit -m … -- <paths>`, never amend.
- Run Python through `/home/apiad/Workspace/repos/aegis/.venv/bin/python` or `uv run`. Never pip.

---

## File structure

| File | Responsibility |
|---|---|
| `src/aegis/webterm/__init__.py` | **New.** Package docstring: what `aegis web` is and what it must never learn. |
| `src/aegis/webterm/static/frames.js` | **New.** The daemon frame codec in the browser. Pure, node-testable. |
| `src/aegis/webterm/static/keys.js` | **New.** Bytes the on-screen keys send. Pure, node-testable. |
| `src/aegis/webterm/static/index.html`, `term.js`, `term.css` | **New.** The page: xterm.js, one WebSocket, link-state overlay, key bar. |
| `src/aegis/webterm/static/vendor/xterm/` | **New.** `xterm.mjs`, `xterm.css`, `addon-fit.mjs`, two `LICENSE-*`, `VERSIONS`. |
| `src/aegis/webterm/relay.py` | **New.** One browser to one daemon connection, reconnecting across daemon restarts. |
| `src/aegis/webterm/auth.py` | **New.** Cookie name and constant-time token check. |
| `src/aegis/webterm/app.py` | **New.** Starlette app: login, index, `/healthz`, `/static`, WS `/term`. |
| `src/aegis/webterm/server.py` | **New.** Port resolution, uvicorn config, the daemon `connect` for a root. |
| `src/aegis/cli.py` | `web` rewritten; `_serve` and `BootConfig` lose `web`; `serve` → `server` plus hidden alias. |
| `src/aegis/daemon/lifecycle.py` | `_spawn_detached` runs `server`; messages say `aegis server`. |
| `tests/webterm/` | **New.** Node wrapper, relay unit tests, auth, page, server, the two real gates. |
| Docs | README, `docs/configuration.md`, `docs/usage.md`, `docs/remote.md`, `docs/commands.md`, `know-how/the-daemon.md`, `know-how/ssh-execution-hosts.md`, `DESIGN.md`, CHANGELOG. |

Slice order: Tasks 1–5 are the thinnest end-to-end path (a browser reaches a
view through a real `aegis web` and a real daemon). Tasks 6–8 harden it.
Task 9 writes it down and drives it in a real browser.

---

### Task 1: The frame codec in the browser, gated by pytest

**Files:**
- Create: `src/aegis/webterm/__init__.py`
- Create: `src/aegis/webterm/static/frames.js`
- Create: `tests/webterm/__init__.py` (empty)
- Create: `tests/webterm/frames.test.mjs`
- Create: `tests/webterm/test_browser_js.py`

**Interfaces:**
- Produces (JS, `/static/frames.js`): `encodeData(bytesOrString) -> Uint8Array`,
  `encodeMeta(obj) -> Uint8Array`, `hello(viewId, width, height)`,
  `resize(width, height)`, `class FrameDecoder { feed(Uint8Array) -> Array<[type, Uint8Array]>; reset() }`.
- Produces (pytest): every `tests/webterm/*.test.mjs` runs under node in `make test`.

- [ ] **Step 1: Write the package docstring**

`src/aegis/webterm/__init__.py`:

```python
"""`aegis web`: browsers as clients of the daemon's unix socket.

A browser tab gets one view, exactly as a terminal does, and this package
relays that view's frames between the tab's WebSocket and the socket. It
checks the token at its front door, which is the only door that faces a
network; the daemon behind it binds no web port.

What it must never learn is what a session, an agent or a queue is. The
retired web layer knew, and its protocol grew a message per feature until
it fell behind the TUI. tests/webterm/test_imports.py holds the line.
"""
```

- [ ] **Step 2: Write the failing node test**

`tests/webterm/frames.test.mjs`:

```js
// Run: node tests/webterm/frames.test.mjs   (exits non-zero on failure)
import assert from "node:assert";
import { encodeData, hello, resize, FrameDecoder } from "../../src/aegis/webterm/static/frames.js";

const text = (bytes) => new TextDecoder().decode(bytes);
const pairs = (frames) => frames.map(([t, p]) => [t, text(p)]);

// 1) a data frame is "D", a big-endian length, the payload: the bytes
//    src/aegis/daemon/protocol.py::encode_data emits
assert.deepStrictEqual([...encodeData("hi")], [68, 0, 0, 0, 2, 104, 105]);

// 2) hello and resize are meta frames whose JSON parse_hello accepts
const h = hello("web-abc", 120, 40);
const hlen = new DataView(h.buffer).getUint32(1, false);
assert.strictEqual(String.fromCharCode(h[0]), "M");
assert.strictEqual(h.length, 5 + hlen);
assert.deepStrictEqual(JSON.parse(text(h.slice(5))),
  { type: "hello", view_id: "web-abc", width: 120, height: 40 });
assert.deepStrictEqual(JSON.parse(text(resize(90, 30).slice(5))),
  { type: "resize", width: 90, height: 30 });

// 3) the decoder reassembles frames split anywhere, including mid-header
const stream = new Uint8Array([...encodeData("abc"), ...encodeData("de")]);
for (let cut = 0; cut <= stream.length; cut++) {
  const d = new FrameDecoder();
  const got = [...d.feed(stream.slice(0, cut)), ...d.feed(stream.slice(cut))];
  assert.deepStrictEqual(pairs(got), [["D", "abc"], ["D", "de"]], `cut at ${cut}`);
}

// 4) reset drops a half-received frame, which is what a reconnect needs
const d = new FrameDecoder();
d.feed(encodeData("abcdef").slice(0, 7));
d.reset();
assert.deepStrictEqual(pairs(d.feed(encodeData("x"))), [["D", "x"]]);

console.log("frames.test.mjs: ok");
```

`tests/webterm/test_browser_js.py`:

```python
"""The page's pure logic, run under node so `make test` covers it.

The .mjs tests under tests/web are run by hand and nothing gates them.
These are gated. A missing node fails rather than skips, because a skipped
check reads as a passing one; CI's Ubuntu image ships node.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
SCRIPTS = sorted(HERE.glob("*.test.mjs"))


def test_the_scripts_were_found():
    assert SCRIPTS, "no *.test.mjs beside this file; the tests below ran nothing"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_browser_module(script):
    node = shutil.which("node")
    assert node, "node is required to test the aegis web page"
    r = subprocess.run([node, str(script)], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm/test_browser_js.py -q -p no:cacheprovider`
Expected: FAIL, node reports `Cannot find module …/static/frames.js`.

- [ ] **Step 4: Write the codec**

`src/aegis/webterm/static/frames.js`:

```js
// The daemon's frame codec, in the browser. Mirrors
// src/aegis/daemon/protocol.py: one byte of type ("D" or "M"), four bytes of
// big-endian length, then the payload. The page speaks exactly what
// `aegis attach` speaks, so `aegis web` relays without translating.

const enc = new TextEncoder();

function frame(type, payload) {
  const out = new Uint8Array(5 + payload.length);
  out[0] = type.charCodeAt(0);
  new DataView(out.buffer).setUint32(1, payload.length, false);
  out.set(payload, 5);
  return out;
}

export function encodeData(bytes) {
  return frame("D", typeof bytes === "string" ? enc.encode(bytes) : bytes);
}

export function encodeMeta(obj) {
  return frame("M", enc.encode(JSON.stringify(obj)));
}

export function hello(viewId, width, height) {
  return encodeMeta({ type: "hello", view_id: viewId, width, height });
}

export function resize(width, height) {
  return encodeMeta({ type: "resize", width, height });
}

export class FrameDecoder {
  constructor() {
    this._buf = new Uint8Array(0);
  }

  // Every whole frame in what has arrived so far, as [type, payload].
  feed(chunk) {
    const merged = new Uint8Array(this._buf.length + chunk.length);
    merged.set(this._buf, 0);
    merged.set(chunk, this._buf.length);
    const view = new DataView(merged.buffer);
    const frames = [];
    let at = 0;
    while (merged.length - at >= 5) {
      const size = view.getUint32(at + 1, false);
      if (merged.length - at < 5 + size) break;
      frames.push([String.fromCharCode(merged[at]),
                   merged.slice(at + 5, at + 5 + size)]);
      at += 5 + size;
    }
    this._buf = merged.slice(at);
    return frames;
  }

  reset() {
    this._buf = new Uint8Array(0);
  }
}
```

- [ ] **Step 5: Run it to verify it passes**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm/test_browser_js.py -q -p no:cacheprovider`
Expected: PASS, 2 tests.

- [ ] **Step 6: Mutation-check the gate**

```bash
cd /home/apiad/Workspace/repos/aegis
f=src/aegis/webterm/static/frames.js; cp $f /tmp/frames.bak
sed -i 's/setUint32(1, payload.length, false)/setUint32(1, payload.length, true)/' $f
cmp -s $f /tmp/frames.bak && echo "MUTATION DID NOT APPLY"
.venv/bin/python -m pytest tests/webterm/test_browser_js.py -q -p no:cacheprovider
cp /tmp/frames.bak $f
```

Expected: `test_browser_module[frames.test.mjs]` FAILS with little-endian lengths, then the file is restored.

- [ ] **Step 7: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/webterm/__init__.py src/aegis/webterm/static/frames.js tests/webterm/__init__.py tests/webterm/frames.test.mjs tests/webterm/test_browser_js.py
git commit -m "feat(webterm): the daemon frame codec in the browser, tested under node" -- src/aegis/webterm/__init__.py src/aegis/webterm/static/frames.js tests/webterm/__init__.py tests/webterm/frames.test.mjs tests/webterm/test_browser_js.py
```

---

### Task 2: The relay, one connection

**Files:**
- Create: `src/aegis/webterm/relay.py`
- Create: `tests/webterm/fakes.py`
- Create: `tests/webterm/test_relay.py`
- Create: `tests/webterm/test_imports.py`

**Interfaces:**
- Consumes: `aegis.daemon.protocol.FrameDecoder`, `parse_hello`, `ProtocolError`.
- Produces:
  ```python
  class BrowserSocket(Protocol):
      async def receive(self) -> bytes | str | None: ...   # None: the browser is gone
      async def send_bytes(self, data: bytes) -> None: ...
      async def send_text(self, text: str) -> None: ...

  Connect = Callable[[], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]

  async def relay(browser: BrowserSocket, connect: Connect) -> None
  ```
- Produces (tests): `tests/webterm/fakes.py` with `FakeBrowser` and `FakeDaemon`, used again in Tasks 3 and 6.

- [ ] **Step 1: Write the fakes**

`tests/webterm/fakes.py`:

```python
"""A browser and a daemon for relay tests.

The daemon is a real unix socket: the relay's reads and writes cross a file
descriptor, and a socket splits and closes the way the real one does. The
browser is a queue, because the WebSocket layer has its own tests.
"""
from __future__ import annotations

import asyncio
from pathlib import Path


class FakeBrowser:
    def __init__(self) -> None:
        self._inbox: asyncio.Queue = asyncio.Queue()
        self.events: list[tuple[str, bytes | str]] = []   # in arrival order

    async def receive(self):
        return await self._inbox.get()

    async def send_bytes(self, data: bytes) -> None:
        self.events.append(("bytes", bytes(data)))

    async def send_text(self, text: str) -> None:
        self.events.append(("text", text))

    def push(self, message) -> None:
        self._inbox.put_nowait(message)

    def leave(self) -> None:
        self._inbox.put_nowait(None)

    @property
    def screen(self) -> bytes:
        return b"".join(p for k, p in self.events if k == "bytes")

    @property
    def texts(self) -> list[str]:
        return [p for k, p in self.events if k == "text"]


class FakeDaemon:
    """Records every connection's bytes; can speak and hang up."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.received: list[bytearray] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.eof: list[asyncio.Event] = []
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(self._on, path=str(self.path))

    async def _on(self, reader, writer) -> None:
        i = len(self.received)
        self.received.append(bytearray())
        self.writers.append(writer)
        self.eof.append(asyncio.Event())
        while chunk := await reader.read(65536):
            self.received[i].extend(chunk)
        self.eof[i].set()

    async def say(self, i: int, data: bytes) -> None:
        self.writers[i].write(data)
        await self.writers[i].drain()

    async def hang_up(self, i: int) -> None:
        self.writers[i].close()

    async def connect(self):
        return await asyncio.open_unix_connection(str(self.path))

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            for w in self.writers:
                w.close()


async def until(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)
```

- [ ] **Step 2: Write the failing tests**

`tests/webterm/test_relay.py`:

```python
"""One browser, one daemon connection, and nothing changed in between."""
from __future__ import annotations

import asyncio
import json

import pytest

from aegis.daemon.protocol import encode_data, encode_meta, hello, resize
from aegis.webterm.relay import relay

from tests.webterm.fakes import FakeBrowser, FakeDaemon, until


@pytest.fixture
async def daemon(tmp_path):
    d = FakeDaemon(tmp_path / "d.sock")
    await d.start()
    yield d
    await d.stop()


async def test_the_hello_and_the_input_reach_the_daemon_verbatim(daemon):
    b = FakeBrowser()
    task = asyncio.create_task(relay(b, daemon.connect))
    first = hello("web-1", 100, 30)
    b.push(first)
    b.push(encode_data(b"ls\r") + resize(90, 20))
    await until(lambda: daemon.received and
                bytes(daemon.received[0]) == first + encode_data(b"ls\r") + resize(90, 20))
    b.leave()
    await asyncio.wait_for(task, 5)


async def test_the_daemons_bytes_reach_the_browser_unchanged(daemon):
    b = FakeBrowser()
    task = asyncio.create_task(relay(b, daemon.connect))
    b.push(hello("web-1", 100, 30))
    await until(lambda: daemon.received)
    stream = encode_data(bytes(range(256)) * 40) + encode_meta({"type": "exit"})
    for i in range(0, len(stream), 777):       # split mid-frame on purpose
        await daemon.say(0, stream[i:i + 777])
    await until(lambda: b.screen == stream)
    b.leave()
    await asyncio.wait_for(task, 5)


@pytest.mark.parametrize("first", [
    "a text message",
    encode_data(b"keys before any hello"),
    b"M\x00\x00\x00\x03{x}",
    hello("../escape", 80, 24),
    hello("web-1", 0, 24),
])
async def test_a_first_message_that_is_not_a_hello_opens_nothing(first, daemon):
    calls = []

    async def connect():
        calls.append(1)
        return await daemon.connect()

    b = FakeBrowser()
    b.push(first)
    await asyncio.wait_for(relay(b, connect), 5)
    assert calls == [], "the relay reached the daemon for a client that never said hello"


async def test_the_browser_leaving_closes_the_daemon_connection(daemon):
    b = FakeBrowser()
    task = asyncio.create_task(relay(b, daemon.connect))
    b.push(hello("web-1", 100, 30))
    await until(lambda: daemon.received)
    b.leave()
    await asyncio.wait_for(task, 5)
    await asyncio.wait_for(daemon.eof[0].wait(), 5)
```

`tests/webterm/test_imports.py`:

```python
"""`aegis web` relays frames; it must not learn what flows through them.

Checked on the import graph, because that is where the retired web layer's
knowledge arrived: it held the manager and called eleven of its methods.
"""
import ast
from pathlib import Path

import aegis.webterm

FORBIDDEN = ("aegis.core", "aegis.tui", "aegis.views", "aegis.web.", "aegis.mcp")
PKG = Path(aegis.webterm.__file__).parent


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_aegis_web_knows_no_aegis_concepts():
    files = sorted(PKG.glob("*.py"))
    assert files, "found no modules to check"
    for f in files:
        bad = sorted(n for n in _imports(f)
                     if (n + ".").startswith(FORBIDDEN) or n == "aegis.web")
        assert not bad, f"{f.name} imports {bad}"
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm/test_relay.py tests/webterm/test_imports.py -q -p no:cacheprovider`
Expected: FAIL, `ModuleNotFoundError: No module named 'aegis.webterm.relay'` for the relay tests; `test_imports` passes (it has only `__init__.py` so far).

- [ ] **Step 4: Write the relay**

`src/aegis/webterm/relay.py`:

```python
"""One browser tab to one daemon connection.

Frames cross unchanged in both directions. The daemon's `serve_view` sees a
client indistinguishable from `aegis attach`, which is the property the
relay-equivalence gate asserts.

The relay parses exactly one thing, the first frame, because a client that
has not said hello must not reach the daemon at all.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from aegis.daemon.protocol import FrameDecoder, ProtocolError, parse_hello

log = logging.getLogger(__name__)


class BrowserSocket(Protocol):
    async def receive(self) -> bytes | str | None: ...
    async def send_bytes(self, data: bytes) -> None: ...
    async def send_text(self, text: str) -> None: ...


Connect = Callable[[], Awaitable[tuple[asyncio.StreamReader,
                                       asyncio.StreamWriter]]]


def _hello_of(message) -> tuple[str, int, int] | None:
    if not isinstance(message, (bytes, bytearray)):
        return None
    frames = list(FrameDecoder().feed(bytes(message)))
    if not frames or frames[0][0] != "M":
        return None
    try:
        return parse_hello(frames[0][1])
    except ProtocolError:
        return None


async def relay(browser: BrowserSocket, connect: Connect) -> None:
    first = await browser.receive()
    if _hello_of(first) is None:
        return
    reader, writer = await connect()
    try:
        writer.write(bytes(first))
        await writer.drain()
        up = asyncio.create_task(_up(browser, writer))
        down = asyncio.create_task(_down(reader, browser))
        try:
            await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (up, down):
                t.cancel()
            for t in (up, down):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()


async def _up(browser: BrowserSocket, writer: asyncio.StreamWriter) -> None:
    while True:
        message = await browser.receive()
        if message is None:
            return
        if isinstance(message, str):
            continue            # the page sends no text today
        writer.write(bytes(message))
        await writer.drain()


async def _down(reader: asyncio.StreamReader, browser: BrowserSocket) -> None:
    while chunk := await reader.read(65536):
        await browser.send_bytes(chunk)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm -q -p no:cacheprovider`
Expected: PASS, 11 tests (2 node, 8 relay, 1 import).

- [ ] **Step 6: Mutation-check the import guard**

```bash
cd /home/apiad/Workspace/repos/aegis
f=src/aegis/webterm/relay.py; cp $f /tmp/relay.bak
sed -i 's/^import logging$/import logging\nimport aegis.core.manager  # mutation/' $f
cmp -s $f /tmp/relay.bak && echo "MUTATION DID NOT APPLY"
.venv/bin/python -m pytest tests/webterm/test_imports.py -q -p no:cacheprovider
cp /tmp/relay.bak $f
```

Expected: `test_aegis_web_knows_no_aegis_concepts` FAILS naming `relay.py`, then restored.

- [ ] **Step 7: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/webterm/relay.py tests/webterm/fakes.py tests/webterm/test_relay.py tests/webterm/test_imports.py
git commit -m "feat(webterm): relay one browser to one daemon connection, byte for byte" -- src/aegis/webterm/relay.py tests/webterm/fakes.py tests/webterm/test_relay.py tests/webterm/test_imports.py
```

---

### Task 3: The front door — cookie auth and the Starlette app

**Files:**
- Create: `src/aegis/webterm/auth.py`
- Create: `src/aegis/webterm/app.py`
- Test: `tests/webterm/test_app.py`

**Interfaces:**
- Consumes: `relay(browser, connect)` and `Connect` from Task 2.
- Produces:
  ```python
  # auth.py
  COOKIE = "aegis_web"
  def token_ok(presented: str | None, token: str) -> bool

  # app.py
  def build_webterm_app(*, token: str, connect: Connect,
                        static_dir: Path | None = None) -> Starlette
  ```
  Routes: `GET /` (login with `?t=`, else the page), `GET /healthz`,
  `/static/*`, WebSocket `/term`.

- [ ] **Step 1: Write the failing tests**

`tests/webterm/test_app.py`:

```python
"""The only door that faces a network.

`base_url` is https because the session cookie is Secure, and an http test
client would never send it back: every cookie test would pass by testing
nothing.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aegis.daemon.protocol import encode_data, hello
from aegis.webterm.app import build_webterm_app
from aegis.webterm.auth import COOKIE, token_ok

TOKEN = "s3cret-token"


class _Refused(Exception):
    pass


@pytest.fixture
def world(tmp_path):
    (tmp_path / "index.html").write_text('<div id="term"></div>')
    calls = []

    async def connect():
        calls.append(1)
        raise _Refused("no daemon in this test")

    app = build_webterm_app(token=TOKEN, connect=connect, static_dir=tmp_path)
    return TestClient(app, base_url="https://testserver"), calls


def _logged_in(client):
    client.cookies.set(COOKIE, TOKEN)
    return client


def test_token_ok_is_exact():
    assert token_ok(TOKEN, TOKEN)
    for bad in (None, "", TOKEN + "x", TOKEN[:-1]):
        assert not token_ok(bad, TOKEN)
    assert not token_ok("", "")


def test_healthz_needs_no_token(world):
    client, _ = world
    assert client.get("/healthz").json() == {"ok": True}


def test_the_login_url_trades_the_token_for_a_cookie(world):
    client, _ = world
    r = client.get(f"/?t={TOKEN}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/", "the token must not survive into the next URL"
    cookie = r.headers["set-cookie"].lower()
    for attr in ("httponly", "secure", "samesite=strict", f"{COOKIE}="):
        assert attr in cookie, f"{attr} missing from {cookie}"


def test_a_wrong_login_token_sets_nothing(world):
    client, _ = world
    r = client.get("/?t=nope", follow_redirects=False)
    assert r.status_code == 401
    assert "set-cookie" not in r.headers


def test_the_page_needs_the_cookie(world):
    client, _ = world
    assert client.get("/").status_code == 401
    r = _logged_in(client).get("/")
    assert r.status_code == 200 and 'id="term"' in r.text


@pytest.mark.parametrize("cookie", [None, "", "nope", TOKEN + "x"])
def test_a_socket_without_the_cookie_is_refused_before_the_daemon(cookie, world):
    client, calls = world
    if cookie is not None:
        client.cookies.set(COOKIE, cookie)
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/term") as ws:
            ws.send_bytes(hello("web-1", 80, 24))
            ws.receive_bytes()
    assert e.value.code == 4401
    assert calls == [], "an unauthenticated socket reached the daemon"


def test_an_authenticated_socket_reaches_the_daemon_only_after_hello(world):
    client, calls = _logged_in(world[0]), world[1]
    with client.websocket_connect("/term") as ws:
        ws.send_bytes(encode_data(b"no hello first"))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_bytes()
    assert calls == []
    with client.websocket_connect("/term") as ws:
        ws.send_bytes(hello("web-1", 80, 24))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_bytes()
    assert calls == [1]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm/test_app.py -q -p no:cacheprovider`
Expected: FAIL, `ModuleNotFoundError: No module named 'aegis.webterm.app'`.

- [ ] **Step 3: Write auth and the app**

`src/aegis/webterm/auth.py`:

```python
"""One secret, presented once in a URL and thereafter as a cookie."""
from __future__ import annotations

import hmac

COOKIE = "aegis_web"


def token_ok(presented: str | None, token: str) -> bool:
    """Constant-time, and never true for an empty token on either side:
    an unset token must lock the door, not open it."""
    if not presented or not token:
        return False
    return hmac.compare_digest(presented.encode(), token.encode())
```

`src/aegis/webterm/app.py`:

```python
"""The Starlette app `aegis web` serves.

Static files are public: they are the page's code, and the code holds no
secret. The page itself, and the socket behind it, need the cookie.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import (
    FileResponse, JSONResponse, PlainTextResponse, RedirectResponse)
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket

from aegis.webterm.auth import COOKIE, token_ok
from aegis.webterm.relay import Connect, relay

_PKG_STATIC = Path(__file__).resolve().parent / "static"
_YEAR = 60 * 60 * 24 * 365


class _StarletteBrowser:
    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    async def receive(self):
        message = await self._ws.receive()
        if message["type"] == "websocket.disconnect":
            return None
        if message.get("bytes") is not None:
            return message["bytes"]
        return message.get("text")

    async def send_bytes(self, data: bytes) -> None:
        await self._ws.send_bytes(data)

    async def send_text(self, text: str) -> None:
        await self._ws.send_text(text)


def build_webterm_app(*, token: str, connect: Connect,
                      static_dir: Path | None = None) -> Starlette:
    static = Path(static_dir) if static_dir is not None else _PKG_STATIC

    async def healthz(request):
        return JSONResponse({"ok": True})

    async def index(request):
        presented = request.query_params.get("t")
        if presented is not None:
            if not token_ok(presented, token):
                return PlainTextResponse("unauthorized", status_code=401)
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(COOKIE, token, max_age=_YEAR, httponly=True,
                                secure=True, samesite="strict")
            return response
        if not token_ok(request.cookies.get(COOKIE), token):
            return PlainTextResponse(
                "unauthorized: open the URL `aegis web` printed",
                status_code=401)
        return FileResponse(static / "index.html",
                            headers={"Cache-Control": "no-cache"})

    async def term(ws: WebSocket) -> None:
        # Refused before accept(): an unauthenticated client never reaches
        # the relay, so it never reaches the daemon.
        if not token_ok(ws.cookies.get(COOKIE), token):
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            await relay(_StarletteBrowser(ws), connect)
        finally:
            with contextlib.suppress(Exception):
                await ws.close()

    return Starlette(routes=[
        Route("/", index),
        Route("/healthz", healthz),
        WebSocketRoute("/term", term),
        Mount("/static", app=StaticFiles(directory=static)),
    ])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm -q -p no:cacheprovider`
Expected: PASS. If `test_an_authenticated_socket_reaches_the_daemon_only_after_hello` hangs rather than failing, the relay's `connect()` exception is being swallowed; it must propagate out of `relay` so the socket closes.

- [ ] **Step 5: Mutation-check the door**

```bash
cd /home/apiad/Workspace/repos/aegis
f=src/aegis/webterm/auth.py; cp $f /tmp/auth.bak
python3 -c "
from pathlib import Path
p=Path('$f'); s=p.read_text()
p.write_text(s.replace('    if not presented or not token:\n        return False\n','    return True\n',1))"
cmp -s $f /tmp/auth.bak && echo "MUTATION DID NOT APPLY"
.venv/bin/python -m pytest tests/webterm/test_app.py -q -p no:cacheprovider
cp /tmp/auth.bak $f
```

Expected: the refusal and wrong-token tests FAIL, then restored.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/webterm/auth.py src/aegis/webterm/app.py tests/webterm/test_app.py
git commit -m "feat(webterm): the front door, a one-time login URL traded for a cookie" -- src/aegis/webterm/auth.py src/aegis/webterm/app.py tests/webterm/test_app.py
```

---

### Task 4: The page — xterm.js, one socket, one view per tab

**Files:**
- Create: `src/aegis/webterm/static/vendor/xterm/{xterm.mjs,xterm.css,addon-fit.mjs,LICENSE-xterm,LICENSE-addon-fit,VERSIONS}`
- Create: `src/aegis/webterm/static/index.html`
- Create: `src/aegis/webterm/static/term.css`
- Create: `src/aegis/webterm/static/term.js`
- Test: `tests/webterm/test_page.py`

**Interfaces:**
- Consumes: `/static/frames.js` from Task 1; `build_webterm_app` from Task 3.
- Produces: the page at `/`. Its only socket is `/term`; its first message is `hello(viewId, cols, rows)`.

- [ ] **Step 1: Vendor xterm.js**

Both files are self-contained ES modules (checked 2026-09-14: no imports,
one trailing `sourceMappingURL` comment, which is stripped because the map
is not shipped).

```bash
T=$(mktemp -d)
curl -sL https://registry.npmjs.org/@xterm/xterm/-/xterm-6.0.0.tgz | tar xz -C "$T" && mv "$T/package" "$T/xterm"
curl -sL https://registry.npmjs.org/@xterm/addon-fit/-/addon-fit-0.11.0.tgz | tar xz -C "$T" && mv "$T/package" "$T/fit"
D=/home/apiad/Workspace/repos/aegis/src/aegis/webterm/static/vendor/xterm
mkdir -p "$D"
sed '/^\/\/# sourceMappingURL=/d' "$T/xterm/lib/xterm.mjs" > "$D/xterm.mjs"
sed '/^\/\/# sourceMappingURL=/d' "$T/fit/lib/addon-fit.mjs" > "$D/addon-fit.mjs"
cp "$T/xterm/css/xterm.css" "$D/xterm.css"
cp "$T/xterm/LICENSE" "$D/LICENSE-xterm"
cp "$T/fit/LICENSE" "$D/LICENSE-addon-fit"
printf '@xterm/xterm 6.0.0\n@xterm/addon-fit 0.11.0\n' > "$D/VERSIONS"
rm -rf "$T"; ls -la "$D"
```

Expected: six files; `xterm.mjs` about 345 KB, `addon-fit.mjs` about 2 KB.

- [ ] **Step 2: Write the failing test**

`tests/webterm/test_page.py`:

```python
"""Everything the page names exists, and is served.

Resolved from the files rather than from a list here, so adding an import
to term.js cannot outrun this test.
"""
from __future__ import annotations

import re
from pathlib import Path

from starlette.testclient import TestClient

from aegis.webterm.app import build_webterm_app
from aegis.webterm.auth import COOKIE

STATIC = Path(__file__).resolve().parents[2] / "src" / "aegis" / "webterm" / "static"


def _referenced() -> set[str]:
    html = (STATIC / "index.html").read_text()
    refs = set(re.findall(r'(?:src|href)="(/static/[^"]+)"', html))
    for js in STATIC.glob("*.js"):
        refs |= set(re.findall(r'from "(/static/[^"]+)"', js.read_text()))
    return refs


def test_every_static_path_the_page_names_exists():
    refs = _referenced()
    assert "/static/term.js" in refs and "/static/vendor/xterm/xterm.mjs" in refs
    for ref in refs:
        assert (STATIC / ref.removeprefix("/static/")).is_file(), ref


def test_the_vendored_versions_and_licenses_are_recorded():
    vendor = STATIC / "vendor" / "xterm"
    assert (vendor / "VERSIONS").read_text().split("\n")[:2] == [
        "@xterm/xterm 6.0.0", "@xterm/addon-fit 0.11.0"]
    for name in ("LICENSE-xterm", "LICENSE-addon-fit"):
        assert "MIT" in (vendor / name).read_text()


def test_the_page_and_its_modules_are_served():
    async def connect():
        raise AssertionError("not reached")

    client = TestClient(build_webterm_app(token="t", connect=connect),
                        base_url="https://testserver")
    client.cookies.set(COOKIE, "t")
    assert 'id="term"' in client.get("/").text
    for ref in _referenced():
        r = client.get(ref)
        assert r.status_code == 200, ref
        if ref.endswith((".js", ".mjs")):
            assert "javascript" in r.headers["content-type"], (
                f"{ref} served as {r.headers['content-type']}; a browser "
                "refuses to run a module with a non-JS MIME type")


def test_one_view_per_tab():
    js = (STATIC / "term.js").read_text()
    assert "sessionStorage" in js and "localStorage" not in js, (
        "two tabs of one browser must not share a view: a view has one geometry")
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm/test_page.py -q -p no:cacheprovider`
Expected: FAIL, `FileNotFoundError: …/static/index.html`.

- [ ] **Step 4: Write the page**

`src/aegis/webterm/static/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, interactive-widget=resizes-content">
<title>aegis</title>
<link rel="stylesheet" href="/static/vendor/xterm/xterm.css">
<link rel="stylesheet" href="/static/term.css">
</head>
<body>
<div id="term"></div>
<div id="status" hidden></div>
<script type="module" src="/static/term.js"></script>
</body>
</html>
```

`src/aegis/webterm/static/term.css`:

```css
html, body { margin: 0; height: 100%; background: #0b0b0c; overflow: hidden; }
#term { position: absolute; inset: 0; }
#status {
  position: absolute; top: 0.75rem; left: 50%; transform: translateX(-50%);
  padding: 0.35rem 0.8rem; border-radius: 0.4rem;
  background: #2a2112; color: #f0c674; font: 13px ui-monospace, Menlo, monospace;
}
```

`src/aegis/webterm/static/term.js`:

```js
import { Terminal } from "/static/vendor/xterm/xterm.mjs";
import { FitAddon } from "/static/vendor/xterm/addon-fit.mjs";
import { FrameDecoder, encodeData, hello, resize } from "/static/frames.js";

// One view per tab. sessionStorage survives a reload of this tab and nothing
// else, so a reload reopens its view and a second tab gets its own.
function viewId() {
  let id = sessionStorage.getItem("aegis-view");
  if (!id) {
    id = "web-" + crypto.randomUUID();
    sessionStorage.setItem("aegis-view", id);
  }
  return id;
}

const status = document.getElementById("status");
function say(text) {
  status.textContent = text;
  status.hidden = !text;
}

const term = new Terminal({
  fontFamily: "ui-monospace, Menlo, monospace",
  fontSize: 14,
  theme: { background: "#0b0b0c" },
});
const fit = new FitAddon();
term.loadAddon(fit);
term.open(document.getElementById("term"));
fit.fit();

const decoder = new FrameDecoder();
const scheme = location.protocol === "https:" ? "wss:" : "ws:";
const ws = new WebSocket(`${scheme}//${location.host}/term`);
ws.binaryType = "arraybuffer";

function send(bytes) {
  if (ws.readyState === WebSocket.OPEN) ws.send(bytes);
}

ws.onopen = () => send(hello(viewId(), term.cols, term.rows));
ws.onmessage = (event) => {
  if (typeof event.data === "string") return;
  for (const [type, payload] of decoder.feed(new Uint8Array(event.data))) {
    if (type === "D") term.write(payload);
  }
};
ws.onclose = () => say("aegis web is gone — reload to reconnect");

term.onData((data) => send(encodeData(data)));
term.onBinary((data) => send(encodeData(Uint8Array.from(data, (c) => c.charCodeAt(0)))));
term.onResize(({ cols, rows }) => send(resize(cols, rows)));
new ResizeObserver(() => fit.fit()).observe(document.getElementById("term"));
term.focus();
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm -q -p no:cacheprovider`
Expected: PASS. If `test_the_page_and_its_modules_are_served` fails on the
`.mjs` content type, Starlette's `StaticFiles` guesses MIME from
`mimetypes`; add `mimetypes.add_type("text/javascript", ".mjs")` at the top
of `app.py` and rerun.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/webterm/static/index.html src/aegis/webterm/static/term.css src/aegis/webterm/static/term.js src/aegis/webterm/static/vendor/xterm tests/webterm/test_page.py
git commit -m "feat(webterm): the page, xterm.js speaking the daemon's frames" -- src/aegis/webterm/static tests/webterm/test_page.py src/aegis/webterm/app.py
```

---

### Task 5: `aegis web` serves the page; the daemon stops serving the web

This closes the first vertical slice: a browser reaches a real view through
a real `aegis web` and a real daemon.

**Files:**
- Create: `src/aegis/webterm/server.py`
- Modify: `src/aegis/cli.py` — `BootConfig.web` (`cli.py:97`) and its argument (`cli.py:126-129`); `_serve`'s `web=None` parameter (`cli.py:597`) and the `if web is not None:` block (`cli.py:753-758`); `web=resolved.boot.web,` in `_run_serve` (`cli.py:1113`); the `web` command (`cli.py:982-1004`).
- Test: `tests/webterm/test_server.py`, `tests/cli/test_web_command.py`, `tests/cli/test_daemon_ignores_the_web_block.py`, `tests/webterm/test_web_process.py`

**Interfaces:**
- Consumes: `build_webterm_app(token=, connect=)` (Task 3); `aegis.daemon.lifecycle.ensure_daemon(root, *, timeout_s=20.0, preflight=None) -> Path`; `aegis.cli._ensure_daemon(root, **kw)` (the existing test seam); `aegis.cli._daemon_preflight(root)`; `aegis.cli._ensure_web_token(root) -> str`; `aegis.config.yaml_loader.load_config(root).web -> WebConfig(token, bind, port)`; `aegis.state.workspace.state_dir(root) -> Path`.
- Produces:
  ```python
  # aegis/webterm/server.py
  def resolve_port(web_cfg, state_dir: Path) -> int
  def uvicorn_config(app, *, bind: str, port: int) -> uvicorn.Config
  def connect_for(root: Path, *, preflight=None) -> Connect
  async def run_web(app, *, bind: str, port: int) -> None
  ```

- [ ] **Step 1: Write the failing unit tests**

`tests/webterm/test_server.py`:

```python
from aegis.config import WebConfig
from aegis.webterm.server import resolve_port, uvicorn_config


def test_a_configured_port_wins(tmp_path):
    assert resolve_port(WebConfig(port=8123), tmp_path) == 8123


def test_an_unset_port_is_picked_once_and_reused(tmp_path):
    first = resolve_port(WebConfig(), tmp_path)
    assert (tmp_path / "web.port").read_text() == str(first)
    assert resolve_port(WebConfig(), tmp_path) == first


def test_the_access_log_is_off():
    """The login URL carries the token once; an access log is where it
    would stay."""
    assert uvicorn_config(object(), bind="127.0.0.1", port=1).access_log is False
```

`tests/cli/test_daemon_ignores_the_web_block.py`:

```python
"""A terminal `aegis` must never start a web server.

It did whenever `.aegis.yaml` carried a token-bearing `web:` block, which
`aegis web` wrote on first use: every later daemon for that root bound the
port, and on 2026-09-13 four of them collided on it.
"""
from typer.testing import CliRunner

CONFIG = """default_agent: main
agents:
  main:
    provider: claude-code
    model: opus
web:
  token: abc
  port: 8931
"""


def test_the_daemon_is_not_handed_the_web_block(tmp_path, monkeypatch):
    from aegis.cli import app

    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    monkeypatch.chdir(tmp_path)
    seen: dict = {}

    async def _fake_serve(**kw):
        seen.update(kw)

    monkeypatch.setattr("aegis.cli._serve", _fake_serve)
    r = CliRunner().invoke(app, ["serve"])
    assert r.exit_code == 0, r.output
    assert seen, "serve did not reach _serve"
    assert "web" not in seen, "the daemon was still handed the web block"
```

`tests/cli/test_web_command.py`:

```python
"""`aegis web`: ensure a token and a daemon, then serve browsers."""
from pathlib import Path

from typer.testing import CliRunner

CONFIG = """default_agent: main
agents:
  main:
    provider: claude-code
    model: opus
"""


def _world(tmp_path, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AEGIS_WEB_TOKEN", raising=False)
    calls: dict = {"ensure": [], "run": []}

    async def _fake_ensure(root, **kw):
        calls["ensure"].append(Path(root))
        return Path(root) / ".aegis" / "state" / "daemon.sock"

    async def _fake_run(app, *, bind, port):
        calls["run"].append((bind, port))

    monkeypatch.setattr("aegis.cli._ensure_daemon", _fake_ensure)
    monkeypatch.setattr("aegis.webterm.server.run_web", _fake_run)
    return calls


def test_web_ensures_a_daemon_then_serves(tmp_path, monkeypatch):
    from aegis.cli import app

    calls = _world(tmp_path, monkeypatch)
    r = CliRunner().invoke(app, ["web", "--no-browser"])
    assert r.exit_code == 0, r.output
    assert calls["ensure"] == [tmp_path.resolve()]
    assert calls["run"] and calls["run"][0][0] == "127.0.0.1"
    assert "/?t=" in r.output
    assert "port:" not in (tmp_path / ".aegis.yaml").read_text(), (
        "aegis web pinned its port into .aegis.yaml again")


def test_web_refuses_a_broken_config_before_serving(tmp_path, monkeypatch):
    from aegis.cli import app

    calls = _world(tmp_path, monkeypatch)
    (tmp_path / ".aegis.yaml").write_text("agents: [not, a, mapping]\n")
    r = CliRunner().invoke(app, ["web", "--no-browser"])
    assert r.exit_code == 1
    assert calls["run"] == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm/test_server.py tests/cli/test_web_command.py tests/cli/test_daemon_ignores_the_web_block.py -q -p no:cacheprovider`
Expected: FAIL: `No module named 'aegis.webterm.server'`; the daemon test fails with "the daemon was still handed the web block".

- [ ] **Step 3: Write `server.py`**

`src/aegis/webterm/server.py`:

```python
"""Running `aegis web`: its port, its uvicorn, and its way to the daemon."""
from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import uvicorn

from aegis.daemon.lifecycle import ensure_daemon
from aegis.webterm.relay import Connect


def resolve_port(web_cfg, state_dir: Path) -> int:
    """The configured port, else the one recorded last time, else a free one,
    recorded. Never written back into `.aegis.yaml`: pinning it there is
    what made every daemon for a root bind it."""
    if web_cfg.port is not None:
        return int(web_cfg.port)
    persisted = Path(state_dir) / "web.port"
    if persisted.exists():
        try:
            return int(persisted.read_text(encoding="utf-8").strip())
        except ValueError:
            pass
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    persisted.parent.mkdir(parents=True, exist_ok=True)
    persisted.write_text(str(port), encoding="utf-8")
    return port


def uvicorn_config(app, *, bind: str, port: int) -> uvicorn.Config:
    # access_log off: the one-time login URL carries the token.
    return uvicorn.Config(app, host=bind, port=port, log_level="warning",
                          access_log=False)


def connect_for(root: Path, *, preflight=None) -> Connect:
    """Each connection finds (or starts) the daemon the way a terminal does,
    so a restarted daemon is found again and a stopped one is started."""
    async def connect():
        path = await ensure_daemon(Path(root), preflight=preflight)
        return await asyncio.open_unix_connection(str(path))
    return connect


async def run_web(app, *, bind: str, port: int) -> None:
    await uvicorn.Server(uvicorn_config(app, bind=bind, port=port)).serve()
```

- [ ] **Step 4: Rewrite the `web` command**

In `src/aegis/cli.py`, replace the whole `web` command (`@app.command()` at `cli.py:982` through `_run_serve(cwd)` at `cli.py:1004`) with:

```python
@app.command()
def web(cwd: str = typer.Option(".", "--cwd"),
        no_browser: bool = typer.Option(False, "--no-browser")) -> None:
    """Serve aegis to browsers: a client of this root's daemon, one view per tab."""
    root = _root_for(cwd)
    if not (root / ".aegis.yaml").is_file():
        _console.print("[red]No .aegis.yaml found.[/red]")
        raise typer.Exit(1)
    try:
        _daemon_preflight(root)
    except ConfigError as e:
        _print_error(e)
        raise typer.Exit(1) from e
    token = _ensure_web_token(root)
    from aegis.config import WebConfig
    from aegis.config.yaml_loader import load_config as _load_yaml
    from aegis.daemon.lifecycle import SpawnFailed
    from aegis.state.workspace import state_dir as _sd
    from aegis.webterm import server as _webserver
    from aegis.webterm.app import build_webterm_app
    # A token from AEGIS_WEB_TOKEN writes no `web:` block, so there may be
    # none to read; the defaults are the block's own.
    web_cfg = _load_yaml(root).web or WebConfig(token=token)
    port = _webserver.resolve_port(web_cfg, _sd(root))
    url = f"http://{web_cfg.bind}:{port}/?t={token}"
    app_ = build_webterm_app(
        token=token,
        connect=_webserver.connect_for(
            root, preflight=lambda: _daemon_preflight(root)))

    async def _main():
        # Up front, so a daemon that cannot start is reported here rather
        # than as a browser that never draws.
        await _ensure_daemon(root, preflight=lambda: _daemon_preflight(root))
        _console.print(f"[green]aegis web → {url}[/green]", soft_wrap=True)
        if not no_browser:
            import threading
            import webbrowser
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        await _webserver.run_web(app_, bind=web_cfg.bind, port=port)

    try:
        asyncio.run(_main())
    except SpawnFailed as e:
        _print_error(e)
        raise typer.Exit(1) from e
```

- [ ] **Step 5: Take the web block away from the daemon**

In `src/aegis/cli.py`:

1. In `class BootConfig` (`cli.py:86`), delete the line `web: object | None`.
2. In `load_boot_config`'s `return BootConfig(…)` (`cli.py:121-130`), delete the three lines from `# Only a token-bearing block counts, or serve starts a web frontend` through `web=(yaml_cfg.web if (yaml_cfg.web and yaml_cfg.web.token) else None),`.
3. In `async def _serve(` (`cli.py:592`), change `remote_plane=None, web=None,` to `remote_plane=None,`.
4. Delete the block at `cli.py:753-758`:
   ```python
       if web is not None:
           from aegis.web.frontend import WebFrontend
           web_fe = WebFrontend(mgr, web, state_dir=roots.state_dir,
                                server_version=_aegis_version())
           tasks.append(asyncio.create_task(web_fe.run()))
           _console.print(f"[green]web UI on {web_fe.url}[/green]")
   ```
5. In `_run_serve`'s `await _serve(…)` call, change `web=resolved.boot.web, views=True,` to `views=True,`.

Then confirm nothing else read them:

Run: `cd /home/apiad/Workspace/repos/aegis && grep -nE 'boot\.web|web=web|WebFrontend' src/aegis/cli.py src/aegis/embed.py`
Expected: no output.

- [ ] **Step 6: Run the unit tests to verify they pass**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm tests/cli tests/daemon tests/test_web_cli.py -m "not slow" -q -n auto -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 7: Write the real-process gate**

`tests/webterm/test_web_process.py`:

```python
"""A browser reaches a view through a real `aegis web` and a real daemon.

Real processes, real ports, a real WebSocket: the slice a user touches.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import subprocess
import sys
import time

import httpx
import psutil
import pytest
from websockets.asyncio.client import connect as ws_connect

from aegis.config.roots import AegisRoots
from aegis.daemon import lifecycle
from aegis.daemon.protocol import FrameDecoder, hello

READY = b"type a message"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _daemon_pids(root) -> list[int]:
    out = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        cmd = p.info["cmdline"] or []
        if any(c in ("serve", "server") for c in cmd) and str(root) in cmd:
            out.append(p.pid)
    return out


@pytest.mark.slow
async def test_a_browser_reaches_a_view_through_aegis_web(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    port, token = _free_port(), "gate-token"
    (root / ".aegis.yaml").write_text(
        "default_agent: main\nagents:\n  main:\n    provider: claude-code\n"
        f"    model: sonnet\nweb:\n  token: {token}\n  port: {port}\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("AEGIS_")}
    env.update(AEGIS_DAEMON_DIR=str(tmp_path / "daemons"), AEGIS_IDLE_TIMEOUT="0")
    log = (tmp_path / "web.log").open("wb")
    web = subprocess.Popen(
        [sys.executable, "-m", "aegis", "web", "--no-browser", "--cwd", str(root)],
        cwd=root, env=env, start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 90
        async with httpx.AsyncClient() as http:
            while True:
                with contextlib.suppress(httpx.HTTPError):
                    if (await http.get(f"{base}/healthz")).status_code == 200:
                        break
                assert web.poll() is None, (tmp_path / "web.log").read_text()
                assert time.monotonic() < deadline, (tmp_path / "web.log").read_text()
                await asyncio.sleep(0.2)
            login = await http.get(f"{base}/?t={token}", follow_redirects=False)
            assert login.status_code == 303

        screen = bytearray()
        decoder = FrameDecoder()
        async with ws_connect(f"ws://127.0.0.1:{port}/term",
                              additional_headers={"Cookie": f"aegis_web={token}"}) as ws:
            await ws.send(hello("web-gate", 100, 30))
            deadline = time.monotonic() + 60
            while READY not in screen:
                assert time.monotonic() < deadline, bytes(screen[-2000:])
                for kind, payload in decoder.feed(await ws.recv()):
                    if kind == "D":
                        screen.extend(payload)
        assert b"\x1b[?1049h" in screen, "the view never entered the alt screen"

        sock = lifecycle.socket_path(AegisRoots.for_project(root))
        assert sock.exists(), "aegis web did not start a daemon"
        daemons = _daemon_pids(root)
        assert len(daemons) == 1, daemons
        listening = {c.laddr.port for c in psutil.Process(daemons[0]).net_connections("inet")
                     if c.status == psutil.CONN_LISTEN}
        assert port not in listening, "the daemon bound the web port"
        assert port in {c.laddr.port for c in psutil.Process(web.pid).net_connections("inet")
                        if c.status == psutil.CONN_LISTEN}
    finally:
        for pid in [web.pid, *_daemon_pids(root)]:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            web.wait(timeout=15)
        log.close()
```

- [ ] **Step 8: Run the gate**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm/test_web_process.py -q -p no:cacheprovider`
Expected: PASS in under a minute. It needs no `claude` on PATH: a tab's harness starts on its first message and the gate sends none.

- [ ] **Step 9: Mutation-check the gate**

```bash
cd /home/apiad/Workspace/repos/aegis
f=src/aegis/webterm/relay.py; cp $f /tmp/relay.bak
sed -i 's/        await browser.send_bytes(chunk)/        await browser.send_bytes(chunk[1:])/' $f
cmp -s $f /tmp/relay.bak && echo "MUTATION DID NOT APPLY"
.venv/bin/python -m pytest tests/webterm/test_web_process.py -q -p no:cacheprovider
cp /tmp/relay.bak $f
```

Expected: FAIL (the frame stream is corrupted and `READY` never decodes), then restored.

- [ ] **Step 10: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/webterm/server.py tests/webterm/test_server.py tests/cli/test_web_command.py tests/cli/test_daemon_ignores_the_web_block.py tests/webterm/test_web_process.py
git commit -m "feat(web): aegis web is a client of the daemon; the daemon binds no web port" -- src/aegis/webterm/server.py src/aegis/cli.py tests/webterm/test_server.py tests/cli/test_web_command.py tests/cli/test_daemon_ignores_the_web_block.py tests/webterm/test_web_process.py
```

---

### Task 6: Surviving a daemon restart, and changing no byte

**Files:**
- Modify: `src/aegis/webterm/relay.py` (replace `relay`, `_up`, `_down`)
- Modify: `src/aegis/webterm/static/term.js` (the `onmessage` handler)
- Test: `tests/webterm/test_relay.py` (append), `tests/webterm/test_relay_gate.py` (new)

**Interfaces:**
- Consumes: Task 2's `relay`, `BrowserSocket`, `Connect`; `tests/webterm/fakes.py`.
- Produces:
  ```python
  RECONNECTING = '{"type": "reconnecting"}'   # text, aegis web → page
  ATTACHED = '{"type": "attached"}'           # text, sent just before a new view's first bytes
  async def relay(browser: BrowserSocket, connect: Connect, *,
                  delays: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 5.0)) -> None
  ```
  The last delay repeats. Text messages carry link state only and never reach the daemon.

- [ ] **Step 1: Write the failing unit tests**

Append to `tests/webterm/test_relay.py`:

```python
from aegis.daemon.protocol import FrameDecoder, parse_hello
from aegis.webterm.relay import ATTACHED, RECONNECTING

FAST = (0.01, 0.02, 0.05)


def _first_frame(data: bytes):
    kind, payload = next(iter(FrameDecoder().feed(bytes(data))))
    return kind, payload


async def test_a_dropped_daemon_is_reconnected_with_the_same_view_at_the_latest_size(daemon):
    b = FakeBrowser()
    task = asyncio.create_task(relay(b, daemon.connect, delays=FAST))
    b.push(hello("web-1", 100, 30))
    await until(lambda: len(daemon.received) == 1)
    b.push(resize(70, 20))
    await until(lambda: resize(70, 20) in bytes(daemon.received[0]))
    await daemon.hang_up(0)

    await until(lambda: RECONNECTING in b.texts)
    await until(lambda: len(daemon.received) == 2 and daemon.received[1])
    kind, payload = _first_frame(daemon.received[1])
    assert kind == "M" and parse_hello(payload) == ("web-1", 70, 20)

    await daemon.say(1, encode_data(b"fresh view"))
    await until(lambda: ATTACHED in b.texts)
    kinds = [k for k, p in b.events if (k, p) in (("text", ATTACHED), ("bytes", encode_data(b"fresh view")))]
    assert kinds == ["text", "bytes"], "the page must reset before the new view's bytes"
    assert not task.done(), "the browser was dropped"
    b.leave()
    await asyncio.wait_for(task, 5)


async def test_keys_typed_while_the_daemon_was_gone_are_not_replayed(daemon):
    attempts = []
    typed = asyncio.Event()

    async def flaky():
        attempts.append(1)
        if len(attempts) == 2:
            await typed.wait()          # fail only once the key is queued
            raise ConnectionRefusedError
        return await daemon.connect()

    b = FakeBrowser()
    task = asyncio.create_task(relay(b, flaky, delays=FAST))
    b.push(hello("web-1", 100, 30))
    await until(lambda: len(daemon.received) == 1)
    await daemon.hang_up(0)
    await until(lambda: RECONNECTING in b.texts)
    b.push(encode_data(b"rm -rf typed into the void"))
    await asyncio.sleep(0.05)           # the relay's browser reader queues it
    typed.set()
    await until(lambda: len(daemon.received) == 2 and daemon.received[1])
    await asyncio.sleep(0.1)
    assert b"rm -rf" not in bytes(daemon.received[1])
    b.leave()
    await asyncio.wait_for(task, 5)


async def test_a_refusal_is_not_announced_as_attached(daemon):
    b = FakeBrowser()
    task = asyncio.create_task(relay(b, daemon.connect, delays=FAST))
    b.push(hello("web-1", 100, 30))
    await until(lambda: len(daemon.received) == 1)
    await daemon.hang_up(0)
    await until(lambda: len(daemon.received) >= 2)
    await daemon.hang_up(1)              # the daemon refuses: no bytes, just EOF
    await until(lambda: len(daemon.received) >= 3)
    assert ATTACHED not in b.texts
    b.leave()
    await asyncio.wait_for(task, 5)


async def test_a_browser_leaving_during_the_backoff_ends_the_relay():
    async def never():
        raise ConnectionRefusedError

    b = FakeBrowser()
    b.push(hello("web-1", 100, 30))
    task = asyncio.create_task(relay(b, never, delays=(0.05,)))
    await asyncio.sleep(0.2)
    b.leave()
    await asyncio.wait_for(task, 2)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm/test_relay.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'ATTACHED'`.

- [ ] **Step 3: Replace the relay**

In `src/aegis/webterm/relay.py`, add `import json` beside the other imports, keep `BrowserSocket`, `Connect` and `_hello_of`, and replace `relay`, `_up` and `_down` with:

```python
RECONNECTING = json.dumps({"type": "reconnecting"})
ATTACHED = json.dumps({"type": "attached"})
_DELAYS = (0.25, 0.5, 1.0, 2.0, 5.0)


async def relay(browser: BrowserSocket, connect: Connect, *,
                delays: tuple[float, ...] = _DELAYS) -> None:
    """Hold one browser for its whole life, across daemon restarts.

    The browser's socket stays open while the daemon is gone. On reconnect
    the daemon is asked for the same view id at the browser's current size;
    `serve_view` persisted that view when the old connection closed, so the
    new one restores it and boots with a full frame.
    """
    first = await browser.receive()
    greeting = _hello_of(first)
    if greeting is None:
        return
    view_id, width, height = greeting
    size = [width, height]
    inbox: asyncio.Queue = asyncio.Queue()
    reader_task = asyncio.create_task(_read_browser(browser, inbox))
    opening: bytes | None = bytes(first)
    failures = 0
    try:
        while True:
            if opening is None:
                if failures:
                    delay = delays[min(failures - 1, len(delays) - 1)]
                    if await _browser_left_within(delay, reader_task):
                        return
                # After the wait, so keys typed during it are dropped too.
                if not _drop_stale_input(inbox, size):
                    return
            try:
                reader, writer = await connect()
            except Exception as e:  # noqa: BLE001 — the daemon may be down
                log.info("daemon unreachable (%s); retrying", e)
                failures += 1
                opening = None
                continue
            try:
                writer.write(opening if opening is not None
                             else hello(view_id, size[0], size[1]))
                await writer.drain()
                announce = opening is None
                opening = None
                outcome, got_bytes = await _pipe(reader, writer, inbox,
                                                 browser, size, announce)
            finally:
                with contextlib.suppress(Exception):
                    writer.close()
                    await writer.wait_closed()
            if outcome == "browser":
                return
            failures = 0 if got_bytes else failures + 1
            with contextlib.suppress(Exception):
                await browser.send_text(RECONNECTING)
    finally:
        reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader_task


async def _read_browser(browser: BrowserSocket, inbox: asyncio.Queue) -> None:
    try:
        while True:
            message = await browser.receive()
            inbox.put_nowait(message)
            if message is None:
                return
    except Exception:  # noqa: BLE001 — a broken socket is a browser gone
        inbox.put_nowait(None)


def _note_resize(message: bytes, size: list[int]) -> None:
    for kind, payload in FrameDecoder().feed(message):
        if kind != "M":
            continue
        with contextlib.suppress(ValueError, TypeError):
            obj = json.loads(payload)
            if obj.get("type") == "resize":
                size[0], size[1] = int(obj["width"]), int(obj["height"])


def _drop_stale_input(inbox: asyncio.Queue, size: list[int]) -> bool:
    """Discard keys typed while no view was listening; keep their resizes.
    False when the browser left in the meantime."""
    while not inbox.empty():
        message = inbox.get_nowait()
        if message is None:
            return False
        if isinstance(message, (bytes, bytearray)):
            _note_resize(bytes(message), size)
    return True


async def _browser_left_within(delay: float, reader_task: asyncio.Task) -> bool:
    done, _ = await asyncio.wait({reader_task}, timeout=delay)
    return bool(done)


async def _pipe(reader, writer, inbox: asyncio.Queue, browser: BrowserSocket,
                size: list[int], announce: bool) -> tuple[str, bool]:
    """Pump until one side ends. Returns who ended it and whether the daemon
    sent anything, which separates a view from a refusal."""
    got_bytes = False

    async def up() -> str:
        while True:
            message = await inbox.get()
            if message is None:
                return "browser"
            if isinstance(message, str):
                continue
            _note_resize(bytes(message), size)
            try:
                writer.write(bytes(message))
                await writer.drain()
            except (ConnectionError, OSError):
                return "daemon"

    async def down() -> str:
        nonlocal got_bytes
        while True:
            try:
                chunk = await reader.read(65536)
            except (ConnectionError, OSError):
                return "daemon"
            if not chunk:
                return "daemon"
            try:
                if not got_bytes and announce:
                    await browser.send_text(ATTACHED)
                got_bytes = True
                await browser.send_bytes(chunk)
            except Exception:  # noqa: BLE001
                return "browser"

    tasks = {asyncio.create_task(up()), asyncio.create_task(down())}
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        return next(iter(done)).result(), got_bytes
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
```

and change the protocol import to `from aegis.daemon.protocol import FrameDecoder, ProtocolError, hello, parse_hello`.

Note what changed for Task 3: `connect()` raising no longer propagates;
the relay retries. Update `test_an_authenticated_socket_reaches_the_daemon_only_after_hello`
so its second block closes the client instead of waiting for a disconnect:

```python
    with client.websocket_connect("/term") as ws:
        ws.send_bytes(hello("web-1", 80, 24))
        import time
        deadline = time.monotonic() + 5
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
    assert calls, "a hello never reached the daemon"
```

- [ ] **Step 4: Teach the page the two link states**

In `src/aegis/webterm/static/term.js`, replace the `ws.onmessage` handler with:

```js
ws.onmessage = (event) => {
  if (typeof event.data === "string") {
    const { type } = JSON.parse(event.data);
    if (type === "reconnecting") say("aegis is restarting — reconnecting…");
    if (type === "attached") {
      // A new view is about to draw from scratch. Anything half-received
      // from the old one would corrupt it.
      decoder.reset();
      term.reset();
      say("");
    }
    return;
  }
  for (const [type, payload] of decoder.feed(new Uint8Array(event.data))) {
    if (type === "D") term.write(payload);
  }
};
```

- [ ] **Step 5: Run the unit tests to verify they pass**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm -m "not slow" -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Write the real gates**

`tests/webterm/test_relay_gate.py`:

```python
"""Against a real brain, real views and a real socket.

Two properties: the relay changes no byte of what the daemon sends, and a
browser survives the daemon going away and coming back.
"""
from __future__ import annotations

import asyncio

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.daemon.lifecycle import socket_path
from aegis.daemon.protocol import hello
from aegis.daemon.server import UnixSocketServer
from aegis.views.registry import ViewRegistry
from aegis.webterm.relay import ATTACHED, RECONNECTING, relay

from tests.brain import make_brain
from tests.views.conftest import FakeMCP
from tests.webterm.fakes import FakeBrowser, until

READY = b"type a message"


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


async def _daemon(roots):
    roster = {"main": Agent(harness="claude-code", model="opus",
                            effort="high", permission="auto")}
    mgr = make_brain(roster, "main", make_session=lambda *a, **k: _FakeHarness(),
                     mcp=None, roots=roots)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(), agents=roster,
                       default_agent="main",
                       make_session=lambda *a, **k: _FakeHarness())
    server = UnixSocketServer(socket_path(roots), reg)
    await server.start()
    return reg, server


class _Tee:
    def __init__(self, reader):
        self._reader, self.seen = reader, bytearray()

    async def read(self, n):
        data = await self._reader.read(n)
        self.seen.extend(data)
        return data


async def test_the_relay_changes_no_byte(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    reg, server = await _daemon(roots)
    tees = []

    async def connect():
        r, w = await asyncio.open_unix_connection(str(server.path))
        tees.append(_Tee(r))
        return tees[-1], w

    b = FakeBrowser()
    task = asyncio.create_task(relay(b, connect))
    try:
        b.push(hello("web-eq", 100, 30))
        await until(lambda: READY in b.screen, 30)
        await asyncio.sleep(0.5)
        assert b.screen == bytes(tees[0].seen), (
            "the browser received something other than what the daemon sent")
    finally:
        b.leave()
        await asyncio.wait_for(task, 10)
        await reg.close_all()
        await server.stop()


async def test_a_browser_survives_the_daemon_restarting(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    reg, server = await _daemon(roots)

    async def connect():
        return await asyncio.open_unix_connection(str(socket_path(roots)))

    b = FakeBrowser()
    task = asyncio.create_task(relay(b, connect, delays=(0.05, 0.1, 0.2)))
    try:
        b.push(hello("web-restart", 100, 30))
        await until(lambda: READY in b.screen, 30)

        await reg.close_all()          # the daemon's views stop and persist
        await server.stop()
        await until(lambda: RECONNECTING in b.texts, 10)

        reg, server = await _daemon(roots)
        await until(lambda: ATTACHED in b.texts, 30)
        at = b.events.index(("text", ATTACHED))
        await until(lambda: READY in b"".join(
            p for k, p in b.events[at:] if k == "bytes"), 30)
        after = b"".join(p for k, p in b.events[at:] if k == "bytes")
        assert b"\x1b[?1049h" in after, "the returning view did not draw from scratch"
        assert reg.get("web-restart") is not None, "the view came back under another id"
        assert not task.done(), "the browser was dropped"
    finally:
        b.leave()
        await asyncio.wait_for(task, 10)
        await reg.close_all()
        await server.stop()
```

- [ ] **Step 7: Run the gates, then break them on purpose**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm/test_relay_gate.py -q -p no:cacheprovider`
Expected: PASS.

```bash
cd /home/apiad/Workspace/repos/aegis
f=src/aegis/webterm/relay.py; cp $f /tmp/relay.bak
sed -i 's/                await browser.send_bytes(chunk)/                await browser.send_bytes(chunk.replace(b"a", b"b", 1))/' $f
cmp -s $f /tmp/relay.bak && echo "MUTATION A DID NOT APPLY"
.venv/bin/python -m pytest tests/webterm/test_relay_gate.py::test_the_relay_changes_no_byte -q -p no:cacheprovider
cp /tmp/relay.bak $f
python3 -c "
from pathlib import Path
p=Path('$f'); s=p.read_text()
old='            if outcome == \"browser\":\n                return\n'
assert s.count(old)==1
p.write_text(s.replace(old, '            return\n',1))"
cmp -s $f /tmp/relay.bak && echo "MUTATION B DID NOT APPLY"
.venv/bin/python -m pytest tests/webterm/test_relay_gate.py::test_a_browser_survives_the_daemon_restarting -q -p no:cacheprovider
cp /tmp/relay.bak $f
```

Expected: mutation A (one byte altered) fails the equivalence gate; mutation B (the relay gives up on daemon EOF) fails the restart gate; both restored.

- [ ] **Step 8: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add tests/webterm/test_relay_gate.py
git commit -m "feat(webterm): a browser survives the daemon restarting, and the relay changes no byte" -- src/aegis/webterm/relay.py src/aegis/webterm/static/term.js tests/webterm/test_relay.py tests/webterm/test_relay_gate.py tests/webterm/test_app.py
```

---

### Task 7: `aegis serve` is `aegis server`

**Files:**
- Modify: `src/aegis/cli.py:786-795` (the `serve` command)
- Modify: `src/aegis/daemon/lifecycle.py` (`_spawn_detached` argv, `SpawnFailed` message, `DaemonAlreadyRunning` and `_spawn_detached` docstrings)
- Modify: `tests/test_detach_and_quit.py`, `tests/cli/test_boot_unification.py:172`, `tests/daemon/test_single_daemon.py` (`_serve` argv)
- Create: `tests/cli/test_server_command.py`

**Interfaces:**
- Produces: `aegis server [--cwd DIR]` (visible), `aegis serve` (hidden alias, same function). `_spawn_detached` runs `python -m aegis server --cwd <root> --autostarted`.

- [ ] **Step 1: Write the failing tests**

`tests/cli/test_server_command.py`:

```python
"""`aegis server` runs the daemon; `aegis serve` still does, quietly.

The alias exists because `aegis bench --target 0.37.0` starts releases that
only know `serve`, and a unit written for `serve` must keep working until
stage 6 deploys the new ones.
"""
from typer.testing import CliRunner

CONFIG = "default_agent: main\nagents:\n  main:\n    provider: claude-code\n    model: opus\n"


def _fake(monkeypatch, tmp_path):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    monkeypatch.chdir(tmp_path)
    runs = []

    async def _fake_serve(**kw):
        runs.append(kw)

    monkeypatch.setattr("aegis.cli._serve", _fake_serve)
    return runs


def test_server_and_its_old_name_both_run_the_daemon(tmp_path, monkeypatch):
    from aegis.cli import app

    runs = _fake(monkeypatch, tmp_path)
    for name in ("server", "serve"):
        r = CliRunner().invoke(app, [name])
        assert r.exit_code == 0, (name, r.output)
    assert len(runs) == 2 and all(kw["views"] for kw in runs)


def test_help_names_server_and_hides_serve():
    from aegis.cli import app

    out = CliRunner().invoke(app, ["--help"]).output
    assert "server" in out
    assert " serve " not in out.replace("server", "")
```

In `tests/test_detach_and_quit.py::test_the_autostart_spawn_marks_the_daemon`, add after the existing assertion:

```python
    assert seen["argv"][3] == "server", (
        f"autostart still runs the old command name: {seen['argv']}")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/cli/test_server_command.py tests/test_detach_and_quit.py -q -p no:cacheprovider`
Expected: FAIL: `No such command 'server'`, and argv[3] is `serve`.

- [ ] **Step 3: Rename**

In `src/aegis/cli.py`, replace the `serve` command with:

```python
@app.command()
def server(
    cwd: str = typer.Option(".", "--cwd"),
    autostarted: bool = typer.Option(
        False, "--autostarted", hidden=True,
        help="Set by a client's autostart. Marks this daemon as one a "
             "client may later stop; a daemon you start yourself is not."),
) -> None:
    """Run the daemon in the foreground: brain, views and MCP plane, on a unix socket."""
    _run_serve(cwd, autostarted=autostarted)


# The old name, hidden. `aegis bench --target X.Y.Z` starts releases that
# only know `serve`, and a systemd unit written for it keeps working until
# stage 6 deploys the new units.
app.command("serve", hidden=True)(server)
```

In `src/aegis/daemon/lifecycle.py`:
- in `_spawn_detached`, change `"serve", "--cwd"` to `"server", "--cwd"` and the docstring's `aegis serve` to `aegis server`;
- in `SpawnFailed`'s raise at the end of `ensure_daemon`, change ``try `aegis serve --cwd {root}` `` to ``try `aegis server --cwd {root}` ``;
- in `DaemonAlreadyRunning`'s docstring, `aegis serve` → `aegis server`.

In `tests/cli/test_boot_unification.py:172`, change `["serve"]` to `["server"]`.
In `tests/daemon/test_single_daemon.py::_serve`, change `"serve", "--cwd"` to `"server", "--cwd"`.
Leave `src/aegis/bench/world.py` on `serve`, and add above its `aegis_argv(… ["serve", …])` line:

```python
    # `serve`, not `server`: --target runs releases that predate the rename,
    # and the hidden alias keeps the current one answering to it.
```

- [ ] **Step 4: Run the affected suites**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/cli tests/daemon tests/test_detach_and_quit.py tests/bench -q -n auto -p no:cacheprovider`
Expected: PASS, the slow single-daemon tests included.

- [ ] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add tests/cli/test_server_command.py
git commit -m "feat(cli): aegis serve is aegis server, the old name kept hidden" -- src/aegis/cli.py src/aegis/daemon/lifecycle.py src/aegis/bench/world.py tests/cli/test_server_command.py tests/test_detach_and_quit.py tests/cli/test_boot_unification.py tests/daemon/test_single_daemon.py
```

---

### Task 8: The key bar a phone keyboard lacks

**Files:**
- Create: `src/aegis/webterm/static/keys.js`
- Create: `tests/webterm/keys.test.mjs`
- Modify: `src/aegis/webterm/static/index.html`, `term.css`, `term.js`

**Interfaces:**
- Produces (JS): `KEYS: {esc, tab, up, down, right, left} -> string`; `ctrl(ch: string) -> string`.

- [ ] **Step 1: Write the failing node test**

`tests/webterm/keys.test.mjs`:

```js
// Run: node tests/webterm/keys.test.mjs
import assert from "node:assert";
import { KEYS, ctrl } from "../../src/aegis/webterm/static/keys.js";

assert.strictEqual(KEYS.esc, "\x1b");
assert.strictEqual(KEYS.tab, "\t");
assert.strictEqual(KEYS.up, "\x1b[A");
assert.strictEqual(KEYS.left, "\x1b[D");
assert.strictEqual(ctrl("c"), "\x03");
assert.strictEqual(ctrl("C"), "\x03");
assert.strictEqual(ctrl("t"), "\x14");     // Ctrl+T opens a tab in aegis
assert.strictEqual(ctrl("["), "\x1b");
assert.strictEqual(ctrl("1"), "1");        // no control code: sent as typed
assert.strictEqual(ctrl("ab"), "ab");      // a paste is not a keystroke
console.log("keys.test.mjs: ok");
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm/test_browser_js.py -q -p no:cacheprovider`
Expected: FAIL on `keys.test.mjs`, module not found.

- [ ] **Step 3: Write the keys and the bar**

`src/aegis/webterm/static/keys.js`:

```js
// What the on-screen keys send. A phone keyboard has no Esc, Tab, Ctrl or
// arrows, and the TUI needs all four.
export const KEYS = {
  esc: "\x1b", tab: "\t",
  up: "\x1b[A", down: "\x1b[B", right: "\x1b[C", left: "\x1b[D",
};

// Ctrl+<letter> is the letter's code minus 64: Ctrl+C is 0x03. Only one
// character is a keystroke; anything longer is a paste and passes through.
export function ctrl(text) {
  if (text.length !== 1) return text;
  const code = text.toUpperCase().charCodeAt(0);
  return code >= 64 && code <= 95 ? String.fromCharCode(code - 64) : text;
}
```

In `index.html`, add before the `<script>` line:

```html
<div id="keys">
  <button data-key="esc">Esc</button><button data-key="tab">Tab</button>
  <button data-ctrl>Ctrl</button>
  <button data-key="left">←</button><button data-key="up">↑</button>
  <button data-key="down">↓</button><button data-key="right">→</button>
</div>
```

Append to `term.css`:

```css
#keys { display: none; }
@media (pointer: coarse) {
  #term { bottom: 2.75rem; }
  #keys {
    display: flex; position: absolute; left: 0; right: 0; bottom: 0; height: 2.75rem;
    background: #151517; border-top: 1px solid #2a2a2e;
  }
  #keys button {
    flex: 1; border: 0; background: none; color: #d8d8dc;
    font: 15px ui-monospace, Menlo, monospace;
  }
  #keys button[aria-pressed="true"] { color: #0b0b0c; background: #f0c674; }
}
```

In `term.js`, add `import { KEYS, ctrl } from "/static/keys.js";` to the
imports, replace `term.onData((data) => send(encodeData(data)));` with:

```js
let ctrlLatched = false;
const ctrlButton = document.querySelector("#keys [data-ctrl]");
function latch(on) {
  ctrlLatched = on;
  ctrlButton.setAttribute("aria-pressed", String(on));
}

term.onData((data) => {
  send(encodeData(ctrlLatched ? ctrl(data) : data));
  if (ctrlLatched) latch(false);
});

// pointerdown, and preventDefault: a click would move focus off xterm's
// hidden textarea and close the phone keyboard.
for (const button of document.querySelectorAll("#keys button")) {
  button.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    if (button.hasAttribute("data-ctrl")) latch(!ctrlLatched);
    else send(encodeData(KEYS[button.dataset.key]));
    term.focus();
  });
}
```

- [ ] **Step 4: Run the page tests**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm -m "not slow" -q -p no:cacheprovider`
Expected: PASS, `keys.test.mjs` included and `test_every_static_path_the_page_names_exists` resolving `/static/keys.js`.

- [ ] **Step 5: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add src/aegis/webterm/static/keys.js tests/webterm/keys.test.mjs
git commit -m "feat(webterm): Esc, Tab, Ctrl and arrows for a phone keyboard" -- src/aegis/webterm/static/keys.js src/aegis/webterm/static/index.html src/aegis/webterm/static/term.css src/aegis/webterm/static/term.js tests/webterm/keys.test.mjs
```

---

### Task 9: Write it down, and drive it in a real browser

**Files:**
- Modify: `README.md`, `docs/configuration.md`, `docs/usage.md`, `docs/remote.md`, `docs/commands.md`, `know-how/the-daemon.md`, `know-how/ssh-execution-hosts.md`, `DESIGN.md`, `CHANGELOG.md`, `TASKS.md`
- Modify: this plan's status header; the status header of `docs/superpowers/specs/2026-09-13-aegis-web-as-a-client-design.md`

- [ ] **Step 1: Rename `aegis serve` in the user-facing docs**

```bash
cd /home/apiad/Workspace/repos/aegis
sed -i 's/`aegis serve`/`aegis server`/g; s/aegis serve --cwd/aegis server --cwd/g' \
  docs/usage.md docs/remote.md docs/commands.md know-how/the-daemon.md know-how/ssh-execution-hosts.md README.md DESIGN.md
grep -nE 'aegis serve\b' docs/usage.md docs/remote.md docs/commands.md know-how/the-daemon.md know-how/ssh-execution-hosts.md README.md DESIGN.md
```

Expected: the grep prints only unquoted mentions. Rename those by hand except lines about `--remote`, which stage 6 deletes; `docs/remote.md:38` is an ASCII diagram, so keep its box columns aligned. Then read `git diff -- docs know-how README.md DESIGN.md` hunk by hunk.

- [ ] **Step 2: Rewrite the web sections**

In `README.md`, replace the Quickstart paragraph that begins
`aegis has **two co-equal first-class UIs** over one backend:` (through
`and serves the client.`) with:

```markdown
`aegis` in a terminal and `aegis web` in a browser are two screens on the
same daemon. `aegis web` is its own process: it ensures a token and a
daemon, prints a login URL, and serves each browser tab its own view of the
same TUI, rendered by xterm.js. Sessions, queues and monitors live in the
daemon, so every screen shows the same ones.
```

and change the Quickstart code line
`aegis web      # installable PWA — first-class UI for remote (and local) dev`
to `aegis web      # the same TUI in a browser, one view per tab`.

In `README.md`'s "The daemon, headless and web" section, replace the two
paragraphs from ``` `aegis server` runs the SessionManager and MCP plane``` through
`A systemd unit template lives at `scripts/aegis-serve.service`.` with:

````markdown
`aegis server` binds no web port. Browsers reach the daemon through
`aegis web`, a separate process that holds the port and the token:

```yaml
# .aegis.yaml
web:
  bind: 127.0.0.1          # front with a reverse proxy for remote access
  port: 8899               # omit to reuse the last port, else pick a free one
  # token: "…"             # or set AEGIS_WEB_TOKEN (env wins) — keeps it out of git
```

`aegis web` prints `http://127.0.0.1:8899/?t=<token>` once. Opening it
trades the token for a cookie, so the token does not stay in the address
bar. If the daemon restarts, open tabs say so and reconnect to their views.
Remote terminals use ssh: `ssh host` and then `aegis`.
````

In `docs/configuration.md`, replace the `## Web UI` section's first
paragraph and its closing paragraph (from `Optional. \`aegis web\` and` to
the end of the paragraph ending `[Remote plane](remote.md).`, keeping the
YAML block and the field table) so that it reads:

```markdown
## Web UI

Optional. Configures `aegis web`, the process that serves browsers. The
daemon does not read this block and never binds this port.
```

(YAML block and table unchanged)

```markdown
`aegis web` creates a token on first run when none is set and writes it
here; `port` is never written back. The login URL it prints carries the
token once, and the page exchanges it for an `HttpOnly`, `Secure`,
`SameSite=Strict` cookie.
```

In `DESIGN.md`, replace the paragraph beginning `**Two co-equal front ends
over one backend.**` with:

```markdown
**A browser is a view, like a terminal.** `aegis web` is a separate process
that serves each browser tab one view over the daemon's unix socket and
relays its frames unchanged. It checks the token and knows nothing else:
no session, agent or queue crosses into it, and `tests/webterm/test_imports.py`
fails if one does. The daemon binds no web port, so the process facing a
network is never the one running the agents.
```

In `know-how/the-daemon.md`, add before `## One daemon per root`:

```markdown
## `aegis web`

A separate process and a client of the socket, like a terminal. It starts
a daemon if none is running, and each browser tab gets its own view, keyed
by an id in that tab's `sessionStorage`. When the daemon goes away the
tabs show "reconnecting" and return to the same views once it is back; a
view file is written when its connection closes, so nothing is lost but
keys typed during the gap, which are dropped on purpose.
```

- [ ] **Step 3: CHANGELOG, TASKS, status headers**

Add under `## [Unreleased]` in `CHANGELOG.md`, in a `### Changed` section
placed before `### Fixed`:

```markdown
### Changed

- **`aegis web` is a client of the daemon, and the daemon no longer serves
  the web.** `aegis web` runs as its own process: it ensures a token and a
  daemon, serves the same TUI to each browser tab through xterm.js, and
  relays each tab's frames to the daemon's unix socket unchanged. A tab
  whose daemon restarts reconnects to its view. The daemon binds no web port
  even with a `web:` block configured, so a terminal `aegis` never starts a
  web server, and `aegis web` no longer writes its port into `.aegis.yaml`.
  The previous browser client is unwired and will be deleted; dev.apiad.net
  runs the old version until it is redeployed.
- **`aegis serve` is now `aegis server`.** `serve` still works and is hidden
  from `--help`.
```

In `TASKS.md`, change the row-2b text `5b decided 2026-09-14 (\`aegis web\` as a socket client), not yet planned; 6 not planned`
to `5b shipped <date> (\`<first>\`..\`<last>\`); 6 not planned`, and replace
the paragraph beginning `**Stage 5b is what remains of the spec's stage 5**`
with a two-sentence shipped note naming the commits and the verification.
Set this plan's header to `**Status: shipped <date>** (\`<first>\`..\`<last>\`)`
and tick every box; set the web-as-a-client spec's status to
`**Status: accepted 2026-09-14; implemented in stage 5b** (\`<first>\`..\`<last>\`)`.

- [ ] **Step 4: Run every gate**

```bash
cd /home/apiad/Workspace/repos/aegis
rift check
.venv/bin/python -m pytest -q -n auto -m "not live" -p no:cacheprovider
```

Expected: rift reports no errors; the suite passes with no regression
against the last full run (3866 passed, 1 skipped at `3498235`) plus the
new tests. Run `uv run ruff check` on every touched `src/` and `tests/`
file, and compare `uv run ty check src/` diagnostic counts against a
worktree of `0eac50d`: no new diagnostics.

- [ ] **Step 5: Drive it in a real browser**

Use the `saidkick` skill (`uv run saidkick …` from `repos/saidkick`).

```bash
R=$(mktemp -d /tmp/aegis-web-e2e-XXXX)
printf 'default_agent: main\nagents:\n  main:\n    provider: claude-code\n    model: sonnet\nweb:\n  port: 8977\n' > "$R/.aegis.yaml"
cd "$R" && AEGIS_DAEMON_DIR="$R/.daemons" /home/apiad/Workspace/repos/aegis/.venv/bin/python -m aegis web --no-browser > web.log 2>&1 &
```

Then, in saidkick, open the URL printed in `$R/web.log`, and record in
this plan, verbatim, the result of each:

1. The address bar shows `/` with no `?t=` after the login redirect.
2. The screenshot shows the aegis tab bar and the `type a message…` input.
3. After a click in the input, typed text appears in it. (A cold attach leaves focus on the tab bar; that is a known defect listed in `TASKS.md`, not this plan's.)
4. Ctrl+T, sent with the key bar's Ctrl then `t` on a narrow viewport, opens a second tab in the tab bar.
5. A reload of the page returns to the same view with both tabs.
6. A second browser tab on the same URL gets its own view: its focus change does not move the first tab's.
7. `AEGIS_DAEMON_DIR="$R/.daemons" aegis kill --cwd "$R"`: the page shows "reconnecting", then returns with its tabs.

Stop `aegis web` and the daemon by PID afterwards.

- [ ] **Step 6: Commit and push**

```bash
cd /home/apiad/Workspace/repos/aegis
git commit -m "docs: aegis web as a client of the daemon, and aegis server" -- README.md docs/configuration.md docs/usage.md docs/remote.md docs/commands.md know-how/the-daemon.md know-how/ssh-execution-hosts.md DESIGN.md CHANGELOG.md TASKS.md docs/superpowers/plans/2026-09-14-aegis-web-as-a-client.md docs/superpowers/specs/2026-09-13-aegis-web-as-a-client-design.md
git push origin main
```

---

## Done when

- A browser reaches a live view through a real `aegis web` process and a real daemon, and the daemon's process holds no listener on the web port (Task 5 gate, mutation-checked).
- The bytes a browser receives equal the bytes the daemon sent (Task 6 gate, mutation-checked).
- A browser survives the daemon stopping and starting again, and returns to the same view id with a full frame (Task 6 gate, mutation-checked).
- An unauthenticated socket is refused with 4401 before the daemon is contacted; the token lives in no URL after login (Task 3, mutation-checked).
- `src/aegis/webterm/` imports no aegis concept module (Task 2, mutation-checked).
- `aegis server` runs the daemon; `aegis serve` still does and is hidden.
- The page's JS logic runs under node in `make test`.
- The seven browser checks in Task 9 Step 5 are recorded with their results.
- `-m "not live"` is green; rift has no errors.

## Deliberately not in this plan

- **Stage 6.** Deleting `src/aegis/web/`, `RemoteSessionManager`, `ws_client` and `--remote`; deploying the VPS as two units; dropping Caddy's `basicauth`. When stage 6 writes the units, use `Wants=` and `After=aegis-server.service` for `aegis-web.service`, not the `Requires=` the spec's *Decisions* names: with `Requires=`, `systemctl restart aegis-server` restarts `aegis-web` too and drops every browser, which is the opposite of decision 3. That unit should also stop `aegis web` from autostarting a daemon beside systemd's; a `--no-autostart` flag belongs there, not here.
- **`_maybe_autolaunch_serve`** (`cli.py:478`). It waits for a web port the daemon no longer binds, so `aegis --remote ws://localhost…` autostart stops working. `--remote` was already broken and stage 6 deletes it.
- **Reaping view files.** Every browser tab leaves `.aegis/state/views/web-<uuid>.json`. A rule for removing ones no tab will reopen is a follow-up.
- **The installable PWA.** The new page has no manifest or service worker; installing it is a follow-up once stage 6 retires the old one.
- **Voice in the browser.** The microphone is the daemon host's (see the daemon spec's open question 3).
- **The cold-attach focus defect** from `aegis bench`, which the browser inherits.
