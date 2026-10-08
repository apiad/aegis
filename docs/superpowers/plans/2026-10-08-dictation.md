# Dictation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A mic button in both composers that transcribes speech in the browser with Cactus Whistle and inserts the text, never sending it.

**Architecture:** The server pins, downloads, verifies and serves three files (`needle.js`, `needle.wasm`, `whistle.cact`) and answers one operation, `dictation.prepare`, with their URL base and a keyword list. The client captures the mic through an AudioWorklet, resamples to 16 kHz, cuts chunks at pauses, and transcribes them in two Web Workers, inserting text in order.

**Tech Stack:** Python 3.13, Starlette, httpx, pydantic; plain ES modules, Web Workers, AudioWorklet; pytest and Playwright (Chromium).

**Spec:** `docs/superpowers/specs/2026-10-08-dictation-design.md`

## Global Constraints

- Audio never leaves the browser. No operation, route or socket message carries samples.
- Pins (revision URLs and sha256) are exactly the three rows in the spec's table.
- Cache directory: `AEGIS_DICTATION_DIR` if set, else `$XDG_CACHE_HOME/aegis/dictation`, else `~/.cache/aegis/dictation`; files under `<dir>/<pin>/`, where `<pin>` is the first 12 hex digits of sha256 over the three sha256 strings joined.
- Served at `/dictation/<pin>/<name>` with `Cache-Control: public, max-age=31536000, immutable`; any other pin or name is a 404.
- Chunking: cut at the first 300 ms window with RMS under 0.01 once 20 s are held; at 30 s with no pause, at the quietest 200 ms between 20 and 30 s. A tail over 10 s splits at the quietest 200 ms of its middle third. A tail with RMS under 0.001 is dropped.
- Keywords: fixed vocabulary, open sessions' handles with hyphens as spaces, last component of each open session's cwd, agent and queue names; case-insensitive dedup; at most 300.
- No `.aegis.yaml` key. No test may download from the network except the `live` one.
- Client code: plain ES modules, no build step, no `confirm(`/`alert(`/`prompt(`.
- English everywhere; conventional commits; stage named paths only.

## Review Focus

1. A recording whose tab is closed or switched mid-dictation: late text must land in that session's draft, never in the session now shown. Pinned in Task 4's tab-switch test.
2. The person types into the composer while a recording is running: dictated pieces keep landing after the previous piece, and the typed text survives. Pinned in Task 3's insertion test.
3. Pieces finishing out of order (the short second half of a split tail before the first): text must still read in order. Pinned in Task 3's ordering test, where the stub engine answers the shorter chunk faster.
4. The model download fails or is corrupt (no network at UH, a truncated file): the button reports it, no partial file is served as the model, and the next press retries. Pinned in Task 1's hash-mismatch and retry tests and Task 4's error test.
5. Two browsers pressing mic on a fresh server at once: one download, both get the files. Pinned in Task 1's concurrency test.

---

### Task 1: `dictation.py`, the pinned files and the keywords

**Files:**
- Create: `src/aegis/dictation.py`
- Modify: `tests/conftest.py` (autouse fixture sets `AEGIS_DICTATION_DIR` to a temp dir)
- Test: `tests/test_dictation.py`

**Interfaces:**
- Produces: `Pin(name, url, sha256)`, `PINS`, `VOCABULARY`, `pin_id(pins=PINS) -> str`, `default_dir() -> Path`, `class Unavailable(Exception)`, `class Store(root: Path, pins=PINS, timeout_s=DOWNLOAD_TIMEOUT_S)` with `.id`, `.dir`, `ready() -> bool`, `file(pin, name) -> Path | None`, `async ensure() -> None`; `keywords(handles, cwds, names, limit=300) -> list[str]`.

- [ ] **Step 1: Write the failing tests** in `tests/test_dictation.py`: a local `http.server` thread serves fixture bytes; pins built from those bytes' real sha256.

```python
import asyncio, hashlib, http.server, threading
from pathlib import Path
import pytest
from aegis import dictation
from aegis.dictation import Pin, Store, Unavailable, keywords, pin_id

FILES = {"needle.js": b"js", "needle.wasm": b"\0asm", "whistle.cact": b"model" * 1000}

@pytest.fixture
def origin(tmp_path):
    hits = []
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            body = FILES.get(self.path.lstrip("/"))
            self.send_response(200 if body else 404); self.end_headers()
            if body: self.wfile.write(body)
        def log_message(self, *a): pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", hits
    srv.shutdown()

def pins(base, bad=None):
    return tuple(Pin(n, f"{base}/{n}", "0" * 64 if n == bad else hashlib.sha256(b).hexdigest()) for n, b in FILES.items())

def test_ensure_downloads_verifies_and_serves(tmp_path, origin):
    base, hits = origin
    s = Store(tmp_path, pins(base))
    asyncio.run(s.ensure())
    assert s.ready() and (s.dir / "whistle.cact").read_bytes() == FILES["whistle.cact"]
    assert s.file(s.id, "needle.js") == s.dir / "needle.js"
    assert s.file(s.id, "other") is None and s.file("0" * 12, "needle.js") is None
    asyncio.run(s.ensure())
    assert len(hits) == 3  # a ready directory downloads nothing

def test_a_wrong_hash_fails_by_name_and_leaves_nothing(tmp_path, origin):
    s = Store(tmp_path, pins(origin[0], bad="needle.wasm"))
    with pytest.raises(Unavailable, match="needle.wasm"):
        asyncio.run(s.ensure())
    assert not (s.dir / "needle.wasm").exists() and not list(s.dir.glob(".*part"))
    assert not s.ready()

def test_a_failed_download_is_retried_on_the_next_call(tmp_path, origin):
    base, hits = origin
    s = Store(tmp_path, tuple(Pin(p.name, p.url.replace("needle.js", "missing"), p.sha256) if p.name == "needle.js" else p for p in pins(base)))
    with pytest.raises(Unavailable, match="needle.js: HTTP 404"):
        asyncio.run(s.ensure())
    s.pins = pins(base)
    asyncio.run(s.ensure())
    assert s.ready()

def test_concurrent_calls_share_one_download(tmp_path, origin):
    base, hits = origin
    s = Store(tmp_path, pins(base))
    async def both():
        await asyncio.gather(s.ensure(), s.ensure(), s.ensure())
    asyncio.run(both())
    assert sorted(hits) == ["/needle.js", "/needle.wasm", "/whistle.cact"]

def test_pin_id_follows_the_hashes():
    assert pin_id() == pin_id(dictation.PINS) and len(pin_id()) == 12
    assert pin_id(pins("http://x")) != pin_id()

def test_default_dir_reads_the_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_DICTATION_DIR", str(tmp_path))
    assert dictation.default_dir() == tmp_path
    monkeypatch.delenv("AEGIS_DICTATION_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    assert dictation.default_dir() == tmp_path / "c" / "aegis" / "dictation"

def test_keywords_merge_dedupe_and_cap():
    kw = keywords(["aegis-whistle-mic", None, "Pull-Request"], [Path("/w/repos/aegis"), Path("/w/scriptorium")], ["opus", "general"])
    assert kw[: len(dictation.VOCABULARY)] == list(dictation.VOCABULARY)
    assert "aegis whistle mic" in kw and "scriptorium" in kw and "opus" in kw and "general" in kw
    assert [k.lower() for k in kw].count("aegis") == 1 and [k.lower() for k in kw].count("pull request") == 1
    assert len(keywords([f"h{i}" for i in range(500)], [], [])) == 300
```

- [ ] **Step 2: Run them to see them fail.** `uv run pytest tests/test_dictation.py -q` → ImportError.

- [ ] **Step 3: Write `src/aegis/dictation.py`.**

```python
"""The server's half of dictation: the pinned engine and model, and the keywords.

Speech is transcribed in the browser (DESIGN.md, "Audio never leaves the
browser"). The server keeps Cactus Whistle's browser build on disk, verified
against the hashes below, serves it at ``/dictation/<pin>/<name>`` with an
immutable cache header, and tells the client which words to bias toward. The
files come from Hugging Face at fixed revisions; a new pin is a new directory,
so a cache never mixes two versions. A directory that already holds the files
is trusted: aegis checked each one when it wrote it.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True)
class Pin:
    name: str
    url: str
    sha256: str


_ENGINE = "https://huggingface.co/Cactus-Compute/needle3/resolve/c7c415a3d1b3d929014bc6e866d51ebb971f7089/wasm"
_MODEL = "https://huggingface.co/Cactus-Compute/whistle/resolve/b358ddadd89b7a713b5aa131f23032d3cca1b251"
PINS = (
    Pin("needle.js", f"{_ENGINE}/needle.js", "f3f7366dcad9555b792ee519d2518f3c506038bcb2ffd179e76e850000749359"),
    Pin("needle.wasm", f"{_ENGINE}/needle.wasm", "c19b9ddf9c7de4eb4f37e5f1811c5bbea9f099041d2a27284daf89789ee8523d"),
    Pin("whistle.cact", f"{_MODEL}/whistle.cact", "b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb"),
)

# Words of agent work that Whistle misses inside Spanish speech without a hint.
VOCABULARY = (
    "agent", "agents", "harness", "issue", "pull request", "PR", "repo",
    "worktree", "branch", "commit", "merge", "CI", "aegis", "Claude",
    "Claude Code", "OpenCode", "MCP", "monitor", "queue", "handoff", "session",
    "subagent", "spec", "plan", "transcript",
)
MAX_KEYWORDS = 300
# httpx's timeout is per operation, so a slow stream never ends without this.
DOWNLOAD_TIMEOUT_S = 900.0


class Unavailable(Exception):
    """The files could not be fetched or did not match their hashes."""


def pin_id(pins: tuple[Pin, ...] = PINS) -> str:
    return hashlib.sha256("".join(p.sha256 for p in pins).encode()).hexdigest()[:12]


def default_dir() -> Path:
    if d := os.environ.get("AEGIS_DICTATION_DIR"):
        return Path(d)
    cache = os.environ.get("XDG_CACHE_HOME")
    return (Path(cache) if cache else Path.home() / ".cache") / "aegis" / "dictation"


class Store:
    def __init__(self, root: Path, pins: tuple[Pin, ...] = PINS, timeout_s: float = DOWNLOAD_TIMEOUT_S) -> None:
        self.root, self.pins, self.timeout_s = root, pins, timeout_s
        self._task: asyncio.Task | None = None

    @property
    def id(self) -> str:
        return pin_id(self.pins)

    @property
    def dir(self) -> Path:
        return self.root / self.id

    def ready(self) -> bool:
        return all((self.dir / p.name).is_file() for p in self.pins)

    def file(self, pin: str, name: str) -> Path | None:
        if pin != self.id or name not in {p.name for p in self.pins}:
            return None
        path = self.dir / name
        return path if path.is_file() else None

    async def ensure(self) -> None:
        """The files on disk, downloading what is missing. Concurrent callers
        share one download; one that fails is retried by the next call."""
        if self.ready():
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._download_all())
        await asyncio.shield(self._task)

    async def _download_all(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for p in self.pins:
                if (self.dir / p.name).is_file():
                    continue
                try:
                    await asyncio.wait_for(self._download(client, p), self.timeout_s)
                except TimeoutError:
                    raise Unavailable(f"{p.name}: not downloaded within {self.timeout_s:.0f} s") from None

    async def _download(self, client: httpx.AsyncClient, p: Pin) -> None:
        part = self.dir / f".{p.name}.part"
        digest = hashlib.sha256()
        try:
            async with client.stream("GET", p.url) as r:
                if r.status_code != 200:
                    raise Unavailable(f"{p.name}: HTTP {r.status_code}")
                with part.open("wb") as f:
                    async for chunk in r.aiter_bytes():
                        digest.update(chunk)
                        f.write(chunk)
            if digest.hexdigest() != p.sha256:
                raise Unavailable(f"{p.name}: sha256 does not match the pin")
            part.replace(self.dir / p.name)
        except httpx.HTTPError as e:
            raise Unavailable(f"{p.name}: {e}") from e
        finally:
            part.unlink(missing_ok=True)


def keywords(handles: Iterable[str | None], cwds: Iterable[Path], names: Iterable[str], limit: int = MAX_KEYWORDS) -> list[str]:
    """The phrases Whistle is biased toward: the vocabulary, then what this
    server knows by name. A handle is spoken with spaces, not hyphens."""
    out: list[str] = []
    seen: set[str] = set()
    words = (*VOCABULARY, *((h or "").replace("-", " ") for h in handles), *(Path(c).name for c in cwds), *names)
    for w in words:
        w = " ".join(w.split())
        if w and w.lower() not in seen and len(out) < limit:
            seen.add(w.lower())
            out.append(w)
    return out
```

- [ ] **Step 4: Isolate the cache in tests.** In `tests/conftest.py`, inside `_no_real_quota` (it already runs for every test and the browser tests' `aegis serve` inherits its env), add `monkeypatch.setenv("AEGIS_DICTATION_DIR", str(off / "dictation"))` and one sentence to its docstring: no test downloads the model into the real cache.

- [ ] **Step 5: Run.** `uv run pytest tests/test_dictation.py -q` → all pass.

- [ ] **Step 6: Commit** `src/aegis/dictation.py tests/test_dictation.py tests/conftest.py`: `feat(dictation): pinned Whistle files, verified download and keywords`.

### Task 2: the operation and the route

**Files:**
- Modify: `src/aegis/app.py` (constructor gains `dictation_dir: Path | None = None`; `self.dictation = Store(dictation_dir or default_dir())`; op `dictation.prepare`)
- Modify: `src/aegis/web.py` (route `/dictation/{pin}/{name}`)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `Store`, `keywords`, `Unavailable`, `default_dir` from Task 1; `reg.open_sessions()` (`Session.handle`, `Session.cwd`); `self.config.current()` (`.agents[i].name`, `.queues` keys).
- Produces: op `dictation.prepare` → `{"base": "/dictation/<pin>/", "keywords": [str]}`, error code `dictation_unavailable`; route `GET /dictation/{pin}/{name}`.

- [ ] **Step 1: Failing tests** in `tests/test_web.py`:

```python
def stub_dictation(root: Path) -> Path:
    from aegis.dictation import PINS, pin_id
    d = root / pin_id()
    d.mkdir(parents=True)
    for p in PINS:
        (d / p.name).write_bytes(b"stub " + p.name.encode())
    return root


def test_dictation_prepare_returns_the_base_and_keywords(project, fake_claude, tmp_path):
    from aegis.dictation import pin_id
    app = App(make_roots(project, None), claude_bin=fake_claude, dictation_dir=stub_dictation(tmp_path / "dict"))
    with TestClient(build_web(app, TOKEN, {"testserver"})) as c:
        with c.websocket_connect("/ws", headers=ORIGIN) as ws:
            conn = Conn(ws).hello()
            got = conn.call("dictation.prepare")["result"]
            assert got["base"] == f"/dictation/{pin_id()}/"
            assert "pull request" in got["keywords"] and "reviewer" in got["keywords"]
        r = c.get(f"/dictation/{pin_id()}/whistle.cact")
        assert r.status_code == 200 and r.content == b"stub whistle.cact"
        assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert c.get(f"/dictation/{pin_id()}/token").status_code == 404
        assert c.get("/dictation/000000000000/needle.js").status_code == 404


def test_dictation_prepare_says_why_when_the_files_cannot_be_had(project, fake_claude, tmp_path, monkeypatch):
    from aegis import dictation
    async def fail(self):
        raise dictation.Unavailable("needle.js: HTTP 503")
    monkeypatch.setattr(dictation.Store, "ensure", fail)
    app = App(make_roots(project, None), claude_bin=fake_claude, dictation_dir=tmp_path / "dict")
    with TestClient(build_web(app, TOKEN, {"testserver"})) as c:
        with c.websocket_connect("/ws", headers=ORIGIN) as ws:
            reply = Conn(ws).hello().call("dictation.prepare")
            assert reply["error"]["code"] == "dictation_unavailable" and "503" in reply["error"]["message"]
```

Check the reply shape (`result` / `error`) against an existing `Conn.call` use in `tests/test_web.py` before running, and match it.

- [ ] **Step 2: Run** `uv run pytest tests/test_web.py -q -k dictation` → fails (`dictation_dir` unknown).

- [ ] **Step 3: Implement.** In `app.py`: `from . import dictation` and `from .ops import NoParams`; constructor parameter `dictation_dir: Path | None = None` and `self.dictation = dictation.Store(dictation_dir or dictation.default_dir())`. In `_register`, next to `recap.request`:

```python
        @r.op("dictation.prepare")
        async def dictation_prepare(p: NoParams, caller):
            """The engine and model the browser transcribes with, on disk and
            verified, and the words to bias it toward."""
            try:
                await self.dictation.ensure()
            except dictation.Unavailable as e:
                raise OpError("dictation_unavailable", str(e)) from e
            snap = self.config.current()
            open_ = reg.open_sessions()
            return {
                "base": f"/dictation/{self.dictation.id}/",
                "keywords": dictation.keywords(
                    [s.handle for s in open_], [s.cwd for s in open_],
                    [*(a.name for a in snap.agents), *snap.queues],
                ),
            }
```

In `web.py`, next to `sent_file`:

```python
    async def dictation_file(request):
        """The pinned dictation files. The path changes with the pin, so a
        browser may keep them forever (dictation.py)."""
        p = request.path_params
        path = app.dictation.file(p["pin"], p["name"])
        if path is None:
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})
```

and `Route("/dictation/{pin}/{name}", dictation_file),` after the `/files/` route.

- [ ] **Step 4: Run** `uv run pytest tests/test_web.py tests/test_dictation.py -q` → pass.

- [ ] **Step 5: Commit** `src/aegis/app.py src/aegis/web.py tests/test_web.py`: `feat(dictation): dictation.prepare and the immutable file route`.

### Task 3: the client engine: capture, chunker, workers, insertion

**Files:**
- Create: `src/aegis/client/js/capture.worklet.js`, `src/aegis/client/js/dictation.worker.js`, `src/aegis/client/js/dictation.js`
- Create: `tests/fixtures/dictation/needle.js` (stub engine)
- Test: `tests/test_browser.py` (new section "dictation")

**Interfaces:**
- Consumes: `dictation.prepare` (Task 2) through a `prepare()` function passed in.
- Produces (`dictation.js`): `SR`, `rms(a, from, to)`, `quietest(a, from, to)`, `resampler(inRate)`, `class Chunker(emit)` with `push(samples)` and `finish() -> Float32Array[]`, `micSource(onSamples) -> Promise<stop>`, `class Dictation({ prepare, onState, onLevel, onError })` with `state`, `target`, `async start(target, source = micSource)`, `async stop()`, `async toggle(target)`. A target is `{ el, key, current }`: `el` the textarea, `key` the `localStorage` draft key or `null`, `current()` the key `el` currently shows.
- Worker protocol: in `{type:"load", base}` → out `{type:"ready"}`; in `{type:"transcribe", id, audio, keywords}` → out `{type:"text", id, text, language, ms}`; any failure → `{type:"error", id?, message}`.

- [ ] **Step 1: The stub engine**, `tests/fixtures/dictation/needle.js`, defining what the worker uses and answering each chunk with its length and keyword count. A chunk shorter than 12 s answers after 50 ms and a longer one after 400 ms, so the ordering test can make the second half finish first.

```js
// A stand-in for Cactus's needle.js in tests: the six members the worker uses.
self.createNeedle = async () => {
  const heap = new Uint8Array(64 << 20);
  let top = 8;
  const e = {
    HEAPU8: heap,
    _malloc: (n) => { const p = top; top += (n + 7) & ~7; return p; },
    _free: () => {},
    UTF8ToString: (p) => { let q = p; while (heap[q]) q++; return new TextDecoder().decode(heap.subarray(p, q)); },
    _needle_load: () => 0,
    _needle_transcribe: (pcm, n, lang, kw, _z, out, cap) => {
      const words = kw ? e.UTF8ToString(kw).split("\n").length : 0;
      const until = performance.now() + (n < 12 * 16000 ? 50 : 400);
      while (performance.now() < until);
      const json = JSON.stringify({ text: ` [${(n / 16000).toFixed(1)}s kw=${words}] `, language: "en" });
      heap.set(new TextEncoder().encode(json + "\0"), out);
      return 0;
    },
  };
  return e;
};
```

- [ ] **Step 2: Failing browser tests.** A fixture prefills the server's dictation dir with the stub (the real `needle.wasm` and `whistle.cact` names, any bytes):

```python
@pytest.fixture
def dict_server(tmp_path: Path, fake_claude: str, fake_opencode: str):
    from aegis.dictation import PINS, pin_id
    d = tmp_path / "dictation"
    (d / pin_id()).mkdir(parents=True)
    stub = Path(__file__).parent / "fixtures" / "dictation" / "needle.js"
    for p in PINS:
        (d / pin_id() / p.name).write_bytes(stub.read_bytes() if p.name == "needle.js" else b"stub")
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    s = Server(tmp_path, fake_claude, fake_opencode)
    s.env["AEGIS_DICTATION_DIR"] = str(d)
    s.start()
    yield s
    s.stop()
```

and a page helper that runs a script against the module with a synthetic source:

```python
TONE_JS = """
const tone = (s) => Float32Array.from({ length: Math.round(s * 16000) }, (_, i) => 0.3 * Math.sin(i / 5));
const gap = (s) => new Float32Array(Math.round(s * 16000));
const feeder = (parts) => async (on) => { for (const p of parts) for (let i = 0; i < p.length; i += 1600) on(p.subarray(i, i + 1600)); return async () => {}; };
"""

def run_dictation(pg, body: str):
    return pg.evaluate(f"""async () => {{
        const m = await import('/static/js/dictation.js');
        {TONE_JS}
        const prepare = async () => ({{ base: await (await fetch('/dictation-base-for-tests')).text(), keywords: ['aegis', 'pull request'] }});
        {body}
    }}""")
```

The `prepare` stub in tests needs the real base. Use the pin from Python instead of a route: format it into the script as `base: '/dictation/{pin_id()}/'`. Tests:

```python
def test_the_chunker_cuts_at_the_first_pause_after_20s_and_at_30s_without_one(dict_server, page):
    page.goto(dict_server.url)
    cuts = run_dictation(page, """
        const out = []; const c = new m.Chunker((a) => out.push(a.length / 16000));
        feeder([tone(22), gap(0.5), tone(35), tone(3)])((x) => c.push(x));
        const tail = c.finish().map((a) => a.length / 16000);
        return { out, tail };""")
    assert 22.0 <= cuts["out"][0] <= 22.4          # the pause after 22 s
    assert 20.0 <= cuts["out"][1] <= 30.0          # no pause: the quietest point
    assert len(cuts["tail"]) == 1 or sum(cuts["tail"]) > 10


def test_a_tail_over_10s_splits_in_two_and_silence_is_dropped(dict_server, page):
    page.goto(dict_server.url)
    got = run_dictation(page, """
        const a = new m.Chunker(() => {}); a.push(tone(6)); a.push(gap(0.3)); a.push(tone(8));
        const b = new m.Chunker(() => {}); b.push(tone(4));
        const c = new m.Chunker(() => {}); c.push(gap(5));
        return [a.finish().map((x) => x.length / 16000), b.finish().length, c.finish().length];""")
    halves, short, silent = got
    assert len(halves) == 2 and 4.6 <= halves[0] <= 9.4   # cut inside the middle third
    assert short == 1 and silent == 0


def test_pieces_land_in_order_after_the_last_one_and_keep_typed_text(dict_server, page):
    page.goto(dict_server.url)
    pin = pin_id()
    text = run_dictation(page, f"""
        const el = document.createElement('textarea'); document.body.append(el);
        el.value = 'Before. After.'; el.setSelectionRange(7, 7);
        const d = new m.Dictation({{ prepare: async () => ({{ base: '/dictation/{pin}/', keywords: ['aegis', 'pull request'] }}), onState() {{}}, onLevel() {{}}, onError(e) {{ throw new Error(e); }} }});
        await d.start({{ el, key: null, current: () => null }}, feeder([tone(21), gap(0.5), tone(14), gap(0.3), tone(7)]));
        el.value = el.value + ' typed';
        await d.stop();
        while (d.state !== 'idle') await new Promise((r) => setTimeout(r, 20));
        return el.value;""")
    # 21.3 s cut at the pause, then a 22 s tail split into two halves; the long
    # half answers slower, so order proves the reordering.
    pieces = re.findall(r"\[(\d+\.\d)s kw=(\d+)\]", text)
    assert [float(s) for s, _ in pieces][0] > 20 and len(pieces) == 3
    assert all(k == "2" for _, k in pieces)
    assert text.startswith("Before. [") and text.endswith("After. typed")
```

- [ ] **Step 3: Run** `uv run pytest tests/test_browser.py -q -m browser -k "chunker or tail_over or pieces_land"` → fail (module missing).

- [ ] **Step 4: Write the three client files.**

`capture.worklet.js`:

```js
// The microphone on the audio thread: mixed to mono, posted in 128-frame blocks.
class Capture extends AudioWorkletProcessor {
  process(inputs) {
    const chans = inputs[0];
    if (chans.length) {
      const out = new Float32Array(chans[0].length);
      for (const c of chans) for (let i = 0; i < out.length; i++) out[i] += c[i] / chans.length;
      this.port.postMessage(out, [out.buffer]);
    }
    return true;
  }
}
registerProcessor("capture", Capture);
```

`dictation.worker.js`:

```js
// One Whistle engine (Cactus's browser build), loaded from the server's pinned
// files. A classic worker, because needle.js is an Emscripten script that
// defines a global createNeedle.
const OUT = 16384;
let engine, out;

function cstr(s) {
  if (!s) return 0;
  const b = new TextEncoder().encode(s);
  const p = engine._malloc(b.length + 1);
  engine.HEAPU8.set(b, p);
  engine.HEAPU8[p + b.length] = 0;
  return p;
}

async function load(base) {
  const get = async (name) => {
    const r = await fetch(base + name);
    if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
    return r;
  };
  const [js, wasm, model] = await Promise.all([get("needle.js").then((r) => r.text()), get("needle.wasm").then((r) => r.arrayBuffer()), get("whistle.cact").then((r) => r.arrayBuffer())]);
  importScripts(URL.createObjectURL(new Blob([js], { type: "text/javascript" })));
  engine = await createNeedle({ wasmBinary: wasm });
  const bytes = new Uint8Array(model);
  const p = engine._malloc(bytes.length);
  engine.HEAPU8.set(bytes, p);
  if (engine._needle_load(p, BigInt(bytes.length)) !== 0) throw new Error("the model did not load");
  out = engine._malloc(OUT);
}

onmessage = async ({ data }) => {
  try {
    if (data.type === "load") {
      await load(data.base);
      postMessage({ type: "ready" });
      return;
    }
    const a = data.audio;
    const pcm = engine._malloc(a.byteLength);
    const kw = cstr(data.keywords);
    const t = performance.now();
    let rc;
    try {
      engine.HEAPU8.set(new Uint8Array(a.buffer, a.byteOffset, a.byteLength), pcm);
      rc = engine._needle_transcribe(pcm, a.length, 0, kw, 0, out, OUT);
    } finally {
      engine._free(pcm);
      if (kw) engine._free(kw);
    }
    if (rc < 0) throw new Error("transcription failed");
    const r = JSON.parse(engine.UTF8ToString(out));
    postMessage({ type: "text", id: data.id, text: r.text.trim(), language: r.language, ms: performance.now() - t });
  } catch (e) {
    postMessage({ type: "error", id: data.id, message: String(e?.message ?? e) });
  }
};
```

`dictation.js`: header comment naming the spec and the measured reasons for each constant; then `SR`, the constants from Global Constraints, `rms`, `quietest`, `resampler` (streaming box filter, as in the playground), `Chunker`, `micSource`, and `Dictation`:

```js
export class Chunker {
  constructor(emit) { this.emit = emit; this.buf = new Float32Array(0); }
  push(samples) {
    const m = new Float32Array(this.buf.length + samples.length);
    m.set(this.buf); m.set(samples, this.buf.length); this.buf = m;
    for (;;) {
      const n = this.buf.length;
      if (n >= MIN_CUT && rms(this.buf, n - PAUSE, n) < QUIET) this.cut(n - PAUSE / 2);
      else if (n >= MAX_CUT) this.cut(quietest(this.buf, MIN_CUT, MAX_CUT));
      else return;
    }
  }
  cut(at) { const c = this.buf.slice(0, at); this.buf = this.buf.slice(at); this.emit(c); }
  finish() {
    const t = this.buf; this.buf = new Float32Array(0);
    if (!t.length || rms(t) < SILENT) return [];
    if (t.length <= SPLIT_OVER) return [t];
    const third = Math.floor(t.length / 3), at = quietest(t, third, 2 * third);
    return [t.slice(0, at), t.slice(at)];
  }
}
```

`Dictation` holds `workers` (two, created on the first `start` of the page, each `{ w, busy }`), `queue` of `{ id, audio, rec }`, `recs` (recordings with pending pieces), and `next` id. `start(target, source)`: stop any listening recording; make `rec = { target, at: target.el.selectionStart ?? target.el.value.length, pending: new Map(), done: new Map(), order: [], chunker }`; set state `loading` until workers are ready, else `listening`; `this.stopCapture = await source((s) => { onLevel(rms(s)); rec.chunker.push(s); })`; call `prepare()` (keywords for this recording; on the first call also the base, posting `load` to both workers and resolving when both answer `ready`). `stop()`: stop capture, `rec.chunker.finish()` pieces are enqueued, state `finishing` while any rec has pending ids, then `idle`. `dispatch()`: give the head of the queue to an idle worker, posting `{type: "transcribe", id, audio, keywords: rec.keywords.join("\n")}` with the buffer transferred. A worker's answer marks it idle, stores the text under its id, and calls `flush(rec)`, which inserts every consecutive finished id from the front of `rec.order`. Insert:

```js
function insert(rec, text) {
  if (!text) return;
  const t = rec.target;
  if (t.key === null || t.current() === t.key) {
    const v = t.el.value, at = Math.min(rec.at, v.length);
    const before = v.slice(0, at), piece = (before && !/\s$/.test(before) ? " " : "") + text;
    t.el.value = before + piece + v.slice(at);
    rec.at = at + piece.length;
    t.el.dispatchEvent(new Event("input", { bubbles: true }));
  } else {
    const v = localStorage.getItem(t.key) || "";
    localStorage.setItem(t.key, v + (v && !/\s$/.test(v) ? " " : "") + text);
  }
}
```

Errors: a rejected `prepare` or a worker `error` without an id calls `onError("Dictation model unavailable: …")`, stops capture and clears the queue; a `getUserMedia` rejection calls `onError("Microphone unavailable: …")`; a worker `error` with an id drops that id's text and calls `onError` naming its seconds.

- [ ] **Step 5: Run** the three tests → pass. Then `uv run pytest tests/test_client_rules.py -q`.

- [ ] **Step 6: Commit** the three client files, the stub and the tests: `feat(dictation): client capture, chunker and two Whistle workers`.

### Task 4: the button, Alt+M, drafts and errors in the client

**Files:**
- Modify: `src/aegis/client/index.html` (`#mic` before `#send` in `.composer .acts`; `#sp-mic` before `#sp-go`)
- Modify: `src/aegis/client/css/base.css` (mic states)
- Modify: `src/aegis/client/js/app.js` (one `Dictation`, targets, stop on `follow`, errors in `#send-error`/`#sp-error`)
- Modify: `src/aegis/client/js/keys.js` (Alt+M row, action `dictate`)
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: `Dictation`, `micSource` from Task 3; `conn.call("dictation.prepare")`.

- [ ] **Step 1: Failing tests.** A browser launched with Chromium's fake mic on a generated WAV (4 s of tone, 1 s of silence), and the `dict_server` fixture:

```python
@pytest.fixture
def mic_page(tmp_path):
    import wave, math
    wav = tmp_path / "speech.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        frames = [int(9000 * math.sin(i / 5)) if i < 4 * 16000 else 0 for i in range(5 * 16000)]
        w.writeframes(b"".join(f.to_bytes(2, "little", signed=True) for f in frames))
    with playwright.sync_playwright() as p:
        b = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", f"--use-file-for-fake-audio-capture={wav}"])
        ctx = b.new_context(viewport={"width": 1280, "height": 800}, permissions=["microphone"])
        errors: list = []
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.errors = errors
        yield pg
        b.close()


def test_the_mic_button_inserts_dictated_text_without_sending(dict_server, mic_page):
    pg = mic_page
    pg.goto(dict_server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    spawn(pg)
    pg.fill("#input", "Look at")
    pg.click("#mic")
    pg.wait_for_selector("#mic[data-state=listening]", timeout=10000)
    pg.wait_for_timeout(2500)
    pg.click("#mic")
    pg.wait_for_selector("#mic[data-state=idle]", timeout=10000)
    v = pg.input_value("#input")
    assert v.startswith("Look at [") and "kw=" in v
    assert turns_done_count(pg) == 0 and pg.errors == []


def test_alt_m_toggles_and_a_tab_switch_sends_late_text_to_the_first_draft(dict_server, mic_page):
    pg = mic_page
    pg.goto(dict_server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(pg)
    spawn(pg)
    pg.click(f"#tablist .tab[data-id='{a}']")
    pg.focus("#input")
    pg.keyboard.press("Alt+KeyM")
    pg.wait_for_selector("#mic[data-state=listening]", timeout=10000)
    pg.wait_for_timeout(2500)
    pg.click("#tablist .tab:not([data-id='" + a + "'])")
    pg.wait_for_function("document.querySelector('#mic').dataset.state === 'idle'", timeout=10000)
    assert "[" not in pg.input_value("#input")
    assert "kw=" in pg.evaluate("(a) => localStorage.getItem(`aegis.draft.${a}`) || ''", a)


def test_the_mic_is_disabled_where_the_browser_has_no_microphone_api(dict_server, page):
    page.add_init_script("Object.defineProperty(navigator, 'mediaDevices', { value: undefined })")
    page.goto(dict_server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    assert page.locator("#mic").is_disabled()
    assert "https" in page.get_attribute("#mic", "title")


def test_a_missing_model_says_so_in_the_composer(server, mic_page):
    # `server` has an empty dictation dir and the test env has no network
    # route to Hugging Face; AEGIS_DICTATION_DIR points at a temp dir.
    ...
```

The last test needs a failing download without the network: give the server a dictation dir whose pin directory is a regular file (so `mkdir` fails with an `OSError`). Catch `OSError` in `Store._download_all` as `Unavailable(f"cache directory: {e}")`, and add that case to Task 1's tests. The test then clicks `#mic` and waits for `#send-error` to contain "Dictation model unavailable". Write `turns_done_count(pg)` as the count of `done in` rows, from `turns_done`'s selector.

- [ ] **Step 2: Run** them → fail (no `#mic`).

- [ ] **Step 3: Markup and CSS.** In `.composer .acts`, before `#send`:

```html
<button class="mic" id="mic" type="button" data-state="idle" title="Dictate (Alt+M)" aria-label="Dictate"><svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5.5" y="1.5" width="5" height="8.5" rx="2.5"/><path d="M3 7.5a5 5 0 0 0 10 0M8 12.5V15" fill="none"/></svg></button>
```

and the same as `#sp-mic` before `#sp-go`. CSS:

```css
#a2 .mic{display:inline-flex;align-items:center;justify-content:center;background:none;border:1px solid var(--rule);border-radius:6px;color:var(--ink);padding:5px 8px;cursor:pointer}
#a2 .mic svg{width:15px;height:15px;fill:currentColor;stroke:currentColor;stroke-width:1.3}
#a2 .mic[data-state=loading]{opacity:.6;animation:mic-pulse 1s ease-in-out infinite}
#a2 .mic[data-state=listening]{color:var(--on-accent);background:var(--err);border-color:transparent;box-shadow:0 0 0 calc(var(--level,0) * 6px) color-mix(in srgb,var(--err) 35%,transparent)}
#a2 .mic[data-state=finishing]{color:var(--err);border-color:var(--err)}
#a2 .mic:disabled{opacity:.35;cursor:default}
#a2 .spawn .mic{margin-left:auto}
#a2 .spawn .mic + .send{margin-left:6px}
@keyframes mic-pulse{50%{opacity:.25}}
```

Check that `--err`, `--on-accent` and `--rule` exist in every theme; use the names the themes define.

- [ ] **Step 4: Wire `app.js`.** Import `{ Dictation }`; build one:

```js
const dictation = new Dictation({
  prepare: () => conn.call("dictation.prepare"),
  onState(state) { for (const b of [$("mic"), $("sp-mic")]) b.dataset.state = dictation.target?.el === (b.id === "mic" ? input : $("sp-text")) ? state : "idle"; },
  onLevel(x) { for (const b of [$("mic"), $("sp-mic")]) b.style.setProperty("--level", Math.min(1, x * 8).toFixed(2)); },
  onError(msg) { $(dictation.target?.el === input ? "send-error" : "sp-error").textContent = msg; },
});
const sessionTarget = () => ({ el: input, key: `aegis.draft.${shown}`, current: () => `aegis.draft.${shown}` });
const spawnTarget = () => ({ el: $("sp-text"), key: null, current: () => null });
if (!navigator.mediaDevices) for (const b of [$("mic"), $("sp-mic")]) { b.disabled = true; b.title = "Dictation needs https or localhost"; }
$("mic").addEventListener("click", () => dictation.toggle(sessionTarget()));
$("sp-mic").addEventListener("click", () => dictation.toggle(spawnTarget()));
```

In `follow(id)`, before `shown = id`: `if (dictation.target?.el === input) dictation.stop();`. The keys action: `dictate() { if (navigator.mediaDevices) dictation.toggle(route().view === "spawn" ? spawnTarget() : sessionTarget()); }`, but only in the session and spawn views. Sending the composer while a recording targets it stops the recording first, so text cannot land in an emptied box after a send.

- [ ] **Step 5: The key row** in `keys.js`, after Alt+S: `{ scope: "global", label: "Alt+M", desc: "Dictate into the message box; again to stop", action: "dictate", match: alt("KeyM") },`.

- [ ] **Step 6: Run** the dictation browser tests, `tests/test_client_rules.py`, and the existing key-list test (`-k "keys or ?"`) → pass.

- [ ] **Step 7: Commit** `index.html base.css app.js keys.js tests/test_browser.py` (+ `dictation.py` and its test for the `OSError` case): `feat(dictation): mic button in both composers, Alt+M`.

### Task 5: live test, docs, hand checks

**Files:**
- Create: `tests/fixtures/dictation/librispeech-1272-128104-0000.wav` (a LibriSpeech test-clean clip, CC BY 4.0) and its reference in the test
- Test: `tests/test_dictation_live.py`
- Modify: `DESIGN.md` (one rule), `docs/superpowers/specs/2026-10-08-dictation-design.md` (status), create `changelog.d/89-dictation.added.md`

- [ ] **Step 1: The live test**, marked `live`, `browser` and `slow`. It starts `Server` with `AEGIS_DICTATION_DIR` set to the real cache (`~/.cache/aegis/dictation`, so repeated runs download once), plays the clip through Chromium's fake mic with `%noloop`, presses `#mic`, waits the clip's length plus 1 s, presses again, waits for `idle` (up to 300 s, which covers a first download), and asserts WER under 15% against the reference, with a normalizer that lower-cases and strips punctuation.

Pick the clip from `.playground/cactus-eval/data/ls_clean/` (it has references in its manifest) with a length between 8 and 15 s, copy it with its id in the name, and paste its reference text into the test.

- [ ] **Step 2: Run** `uv run pytest tests/test_dictation_live.py --run-live -q` on zion → pass. Report the WER and the time.

- [ ] **Step 3: Docs.** DESIGN.md, after "A sent file's URL is its key…": a rule "**Audio never leaves the browser.**" saying the browser transcribes with Whistle in two workers, the server only keeps the pinned files and the keywords (`dictation.py`), and why (no audio on the wire, works through any proxy that serves the page). Changelog fragment:

```markdown
A mic button in the composer and the new-session box, and Alt+M: speech is transcribed in your browser by Cactus Whistle and lands in the message box for you to review. No audio leaves the browser. The first use downloads the 17.8 MB model to the server's cache.
```

Spec status: "built, 2026-10-08 (PR #<n>)".

- [ ] **Step 4: Gates.** `make check`, then `make test-browser` (dictation tests included), `rift check`, `make bench` (its table goes in the PR body).

- [ ] **Step 5: Hand checks** against an `aegis serve` started from this worktree after the change, on an isolated root under `.playground/`: press mic in desktop Chromium with Alex's `alex_es/1.wav` through the fake mic and read the text; open it in Firefox and confirm the button reaches `listening`; and report the phone as not checked unless Alex tries it through a tunnel.

- [ ] **Step 6: Commit** the docs, fixture and live test: `test(dictation): live WER check on a LibriSpeech clip` and `docs(dictation): design rule, changelog, spec status`; push; open the PR with `Closes #89`, the bench table, what was measured, and what was checked by hand.
