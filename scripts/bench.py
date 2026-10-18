"""Measure aegis's transcript path. Reports; never gates.

    uv run python scripts/bench.py [--out FILE]

Four measurements, all lower-is-better, over the recorded session in
tests/fixtures/session.jsonl:

- server cost per Claude line: parse, store, fold, publish and JSON-encode
  the patch, in process, p50 and p95;
- browser latency per tool entry: from the fake claude writing the line to
  the row being in the DOM of headless Chromium, through a real
  ``aegis serve``, p50 and p95;
- cold load: the fixture replayed to 2,000+ entries, then a page load timed
  from navigation start to the first frame painted after the snapshot, with
  nothing kept on the device; and the transcript bytes a reload receives when
  the device kept a copy, so it asks only for what changed (#342);
- memory after that replay: the server's RSS and the page's JS heap;
- typing in that session: the renderer main thread's time per keystroke in
  the composer, from a trace. Every layout pays a hover hit test and a paint
  over the mounted rows, so this is what grows if the transcript's DOM does
  (#157);
- the Files view (#458) on a generated tree of 25,000 files: its first open of
  a Python file in a fresh browser context, the time from Alt+O to a listed
  picker (the first, which walks the tree, and the warm ones), and the
  CodeMirror bytes the client loads for that file (core plus Python).

Each figure is the median of several samples taken after a warm-up, and
the samples are kept in the result's ``samples``: one run's figures moved by
40 to 140% between identical runs when each was a single sample or the
minimum of three (#145, #282). scripts/bench_compare.py turns these files
into a table, and warns only when head is 20% worse and its best sample is
worse than base's worst.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "session.jsonl"
FAKE = ROOT / "tests" / "fake_claude.py"
COLD_ENTRIES = 2000

sys.path.insert(0, str(ROOT / "src"))


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


# Each metric's samples, by name; main() writes them beside the medians.
SAMPLES: dict[str, list[float]] = {}


def median(name: str, xs: list[float]) -> float:
    """Record a metric's samples and return their median."""
    SAMPLES[name] = [round(x, 3) for x in xs]
    return statistics.median(xs)


OPENCODE_FIXTURE = ROOT / "tests" / "fixtures" / "opencode" / "tool.jsonl"


def opencode_lines() -> list[str]:
    return [ln for ln in OPENCODE_FIXTURE.read_text().splitlines() if ln]


CODEX_FIXTURE = ROOT / "tests" / "fixtures" / "codex" / "tool.jsonl"


def codex_lines() -> list[str]:
    return [ln for ln in CODEX_FIXTURE.read_text().splitlines() if ln]


def claude_lines() -> list[str]:
    return [
        r["line"] for r in map(json.loads, FIXTURE.open()) if r.get("src") == "claude"
    ]


# -- 1. server cost per line --------------------------------------------------
async def server_cost(
    rounds: int = 40, harness: str = "claude-code", repeats: int = 7
) -> dict:
    """Per-line cost, p50 and p95, each the median over ``repeats`` replays."""
    runs = [await server_cost_once(rounds, harness) for _ in range(repeats)]
    tag = "" if harness == "claude-code" else f"_{harness}"
    key = f"server_line{tag}"
    return {
        f"{key}_p50_us": median(f"{key}_p50_us", [r[0] for r in runs]),
        f"{key}_p95_us": median(f"{key}_p95_us", [r[1] for r in runs]),
        (f"server{tag}_lines" if tag else "server_lines"): runs[0][2],
    }


async def server_cost_once(rounds: int, harness: str) -> tuple[float, float, int]:
    from aegis.meta import MetaStore
    from aegis.session import Session, SpawnSpec
    from aegis.transcript.store import Store

    lines = {"opencode": opencode_lines, "codex": codex_lines}.get(
        harness, claude_lines
    )()
    ids = {
        "opencode": ('"call_', '"prt_', '"msg_'),
        "codex": ('"call-', '"msg_', '"rs_'),
    }.get(harness, ('"toolu_',))
    samples: list[float] = []
    with tempfile.TemporaryDirectory() as tmp:

        def sink(ch: str, ops: list[dict]) -> None:
            # What the websocket would encode for one subscriber.
            json.dumps(
                {"t": "patch", "channel": ch, "seq": 1, "ops": ops}, ensure_ascii=False
            )

        s = Session(
            log_id="bench",
            spec=SpawnSpec("b", "m", "low", "full", Path(tmp), harness=harness),
            handle="bench-one",
            store=Store(Path(tmp) / "t.jsonl"),
            stderr_path=Path(tmp) / "e",
            claude_bin="true",
            opencode_bin="true",
            codex_bin="true",
            publish=sink,
            metas=MetaStore(Path(tmp) / "sessions"),
        )
        s.status = "idle"
        for r in range(rounds):
            for line in lines:
                if r:
                    for prefix in ids:
                        line = line.replace(prefix, f"{prefix}r{r}_")
                t0 = time.perf_counter_ns()
                s._on_line(line)
                samples.append((time.perf_counter_ns() - t0) / 1000)
        s.store.close()
    return pct(samples, 0.5), pct(samples, 0.95), len(samples)


# -- 2 and 3: through a real server and a real browser ------------------------
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_env(tmp: Path, env: dict) -> dict:
    """The bench server's environment. The quota credentials and cache point
    into ``tmp``: on a dev machine the real token is there, and a bench run
    would poll the vendors and write the cache the running aegis shows."""
    off = tmp / "quota"
    return {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "CLAUDE_CREDS": str(off / "claude-credentials.json"),
        "OPENCODE_AUTH": str(off / "opencode-auth.json"),
        "AEGIS_QUOTA_CACHE": str(off / "cache"),
        **env,
    }


class Server:
    def __init__(self, tmp: Path, env: dict, keep_config: bool = False) -> None:
        if not keep_config:
            (tmp / ".aegis.yaml").write_text(
                "agents:\n  bench: {harness: claude-code, model: m, effort: low, permission: full}\n"
            )
        fake = tmp / "claude"
        fake.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
        fake.chmod(0o755)
        port = free_port()
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "aegis",
                "serve",
                "--root",
                str(tmp),
                "--port",
                str(port),
                "--claude",
                str(fake),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=server_env(tmp, env),
        )
        self.url = ""
        while not self.url:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("aegis serve exited")
            if m := re.search(r"open (http://\S+)", line):
                self.url = m.group(1)
        for _ in range(200):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
                return
            except OSError:
                time.sleep(0.05)

    def rss_mb(self) -> float:
        import psutil

        return psutil.Process(self.proc.pid).memory_info().rss / 2**20

    def stop(self) -> None:
        self.proc.terminate()
        self.proc.wait(10)


def start_session(page, url: str) -> None:
    page.goto(url)
    page.wait_for_selector("#a2[data-view=fleet]")
    page.click("#tab-add")
    page.wait_for_selector("#a2[data-view=spawn]")
    page.click("#sp-go")
    page.wait_for_selector("#a2[data-view=session]")
    page.fill("#input", "replay")
    page.press("#input", "Enter")


WATCH = """
window.__seen = {};
new MutationObserver(() => {
  for (const r of document.querySelectorAll('#entries .row[data-id^="toolu_"]'))
    if (!(r.dataset.id in window.__seen)) window.__seen[r.dataset.id] = Date.now();
}).observe(document, {childList: true, subtree: true});
"""


# Rows the transcript mounted, by id, whether or not they are still mounted:
# the transcript keeps only its tail in the DOM.
ROWS_SEEN = """
window.__rows = new Set();
new MutationObserver((ms) => {
  for (const m of ms) for (const n of m.addedNodes)
    if (n.nodeType === 1 && n.matches('#entries > .row')) window.__rows.add(n.dataset.id);
}).observe(document, {childList: true, subtree: true});
"""


def key_cost(browser, page, keys: int = 40) -> float:
    """Main-thread milliseconds per keystroke typed into the composer."""
    page.click("#input")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "trace.json"
        browser.start_tracing(
            page=page,
            path=str(path),
            categories=["devtools.timeline", "disabled-by-default-devtools.timeline"],
        )
        page.keyboard.type("x" * keys, delay=30)
        page.wait_for_timeout(200)
        browser.stop_tracing()
        events = json.loads(path.read_text())
    events = events["traceEvents"] if isinstance(events, dict) else events
    # The page's main thread: the one that recalculated style.
    threads: dict = {}
    for e in events:
        if e.get("name") == "UpdateLayoutTree":
            t = (e.get("pid"), e.get("tid"))
            threads[t] = threads.get(t, 0) + 1
    main = max(threads, key=threads.get)
    busy = sum(
        e.get("dur", 0)
        for e in events
        if (e.get("pid"), e.get("tid")) == main
        and e.get("name") == "RunTask"
        and e.get("ph") == "X"
    )
    page.fill("#input", "")
    return busy / 1000 / keys


def heap_mb(page) -> float:
    """The page's JS heap after two forced collections. Read without one, the
    figure is whatever V8 had not collected yet, and doubled between identical
    runs (#145)."""
    cdp = page.context.new_cdp_session(page)
    try:
        for _ in range(2):
            cdp.send("HeapProfiler.collectGarbage")
        return page.evaluate("performance.memory.usedJSHeapSize") / 2**20
    finally:
        cdp.detach()


def entry_latency(browser, rounds: int = 4) -> list[float]:
    """Milliseconds from each tool line's emit to its row in the DOM."""
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "emit.log"
        srv = Server(
            Path(tmp),
            {
                "FAKE_CLAUDE_REPLAY": str(FIXTURE),
                "FAKE_CLAUDE_PACE": "0.01",
                "FAKE_CLAUDE_REPEAT": str(rounds),
                "FAKE_CLAUDE_EMIT_LOG": str(log),
            },
        )
        try:
            page = browser.new_page()
            page.add_init_script(WATCH)
            start_session(page, srv.url)
            calls = sum('"tool_use"' in ln for ln in claude_lines()) * rounds
            page.wait_for_function(
                "n => Object.keys(window.__seen).length >= n",
                arg=calls,
                timeout=120_000,
            )
            seen = page.evaluate("window.__seen")
            page.close()
            emitted = dict(line.split() for line in log.read_text().splitlines())
            return [seen[i] - float(t) * 1000 for i, t in emitted.items() if i in seen]
        finally:
            srv.stop()


def browser_runs(playwright) -> dict:
    out: dict = {}
    browser = playwright.chromium.launch(args=["--enable-precise-memory-info"])
    try:
        # Latency: a paced replay, each tool row's DOM time against its emit
        # time. One replay gave 32 samples, and their p95 was the second
        # worst; four fresh sessions of the same length are pooled instead.
        lat: list[float] = []
        p95s: list[float] = []
        for _ in range(4):
            one = entry_latency(browser)
            lat += one
            p95s.append(pct(one, 0.95))
        SAMPLES["browser_entry_p95_ms"] = p95s
        out.update(
            browser_entry_p50_ms=pct(lat, 0.5),
            browser_entry_p95_ms=pct(lat, 0.95),
            browser_entries=len(lat),
        )

        # Cold load of 2,000+ entries, then memory.
        with tempfile.TemporaryDirectory() as tmp:
            # The init and spawn lines show once, so a round adds about 23.
            rounds = COLD_ENTRIES // 20 + 1
            srv = Server(
                Path(tmp),
                {"FAKE_CLAUDE_REPLAY": str(FIXTURE), "FAKE_CLAUDE_REPEAT": str(rounds)},
            )
            try:
                page = browser.new_page()
                page.add_init_script(ROWS_SEEN)
                start_session(page, srv.url)
                t0 = time.monotonic()
                page.wait_for_function(
                    "n => window.__rows.size >= n",
                    arg=COLD_ENTRIES,
                    timeout=180_000,
                )
                print(
                    f"replayed to {COLD_ENTRIES} rows in {time.monotonic() - t0:.1f}s",
                    file=sys.stderr,
                )
                page.wait_for_function(
                    "document.getElementById('s-status').textContent.trim() === 'done'",
                    timeout=60_000,
                )
                frames: list[str] = []
                # A lambda: Playwright marks its handlers, and a builtin
                # takes no attribute.
                page.on(
                    "websocket",
                    lambda ws: ws.on("framereceived", lambda f: frames.append(f)),
                )

                # The page keeps the transcript on the device 2 s after it
                # lands; these loads measure the page holding nothing.
                def forget() -> None:
                    page.wait_for_timeout(2500)
                    page.evaluate(
                        "import('/static/js/devicestore.js').then(s => s.clearDevice())"
                    )

                # Two loads warm the page's code; the next five are measured.
                loads = []
                for _ in range(7):
                    forget()
                    frames.clear()
                    page.reload()
                    page.wait_for_function(
                        "window.__a2snapshot && window.__a2snapshot.painted",
                        timeout=60_000,
                    )
                    loads.append(page.evaluate("window.__a2snapshot.painted"))
                loads = loads[2:]
                snap = [
                    f for f in frames if '"t": "snapshot"' in f and "transcript:" in f
                ]
                # A cold start with the copy on the device: it asks since the
                # copy's revision and receives only what changed.
                page.wait_for_timeout(2500)
                frames.clear()
                page.reload()
                page.wait_for_function(
                    "window.__a2snapshot && window.__a2snapshot.painted",
                    timeout=60_000,
                )
                page.wait_for_timeout(500)
                saved = [f for f in frames if "transcript:" in f]
                here = page.evaluate("location.hash")
                page.evaluate("location.hash = '#fleet'")
                page.wait_for_selector("#a2[data-view=fleet]")
                frames.clear()
                page.evaluate("h => (location.hash = h)", here)
                page.wait_for_function(
                    "document.querySelector('#a2').dataset.view === 'session'"
                )
                page.wait_for_timeout(500)
                back = [f for f in frames if "transcript:" in f]
                out.update(
                    cold_load_ms=median("cold_load_ms", loads),
                    cold_load_entries=page.evaluate("window.__a2snapshot.count"),
                    cold_snapshot_kb=sum(map(len, snap)) / 1000,
                    cold_saved_kb=sum(map(len, saved)) / 1000,
                    return_tab_kb=sum(map(len, back)) / 1000,
                    server_rss_mb=srv.rss_mb(),
                    browser_heap_mb=median(
                        "browser_heap_mb", [heap_mb(page) for _ in range(3)]
                    ),
                )
                out.update(
                    cold_key_ms=median(
                        "cold_key_ms", [key_cost(browser, page) for _ in range(3)]
                    )
                )
            finally:
                srv.stop()
    finally:
        browser.close()
    return out


# -- 4. many sessions: registry boot and the Fleet view ------------------------
def make_world(root: Path, n_open: int = 20, n_archived: int = 80) -> None:
    """A state directory with the fixture's store copied into many sessions."""
    (root / ".aegis.yaml").write_text(
        "agents:\n  bench: {harness: claude-code, model: m, effort: low, permission: full}\n"
    )
    state = root / ".aegis" / "state"
    (state / "transcripts").mkdir(parents=True)
    (state / "sessions").mkdir(parents=True)
    store = FIXTURE.read_bytes()
    for i in range(n_open + n_archived):
        log_id = f"20261006-0000{i:02}-bench{i:03}"
        (state / "transcripts" / f"{log_id}.jsonl").write_bytes(store)
        meta = {
            "log_id": log_id,
            "handle": f"bench-{i}",
            "title": f"Session {i}",
            "profile": "bench",
            "model": "m",
            "effort": "low",
            "permission": "full",
            "cwd": str(root),
            "resume_id": f"cs-{i}",
            "archived": i >= n_open,
            "created_at": 1_000_000.0 + i,
            "last_activity": 1_000_000.0 + i,
            "last_status": "idle",
            "cost_usd": 0.05,
            "context_tokens": 27000,
            "context_window": 200000,
            "activity": "Bash · Run tests",
        }
        (state / "sessions" / f"{log_id}.json").write_text(json.dumps(meta))


def boot_cost() -> dict:
    from aegis.registry import Registry
    from aegis.roots import make_roots

    with tempfile.TemporaryDirectory() as tmp:
        make_world(Path(tmp))
        roots = make_roots(Path(tmp), None)
        times = []
        for _ in range(23):
            gc.collect()
            t0 = time.perf_counter()
            r = Registry(roots, lambda ch, ops: None)
            r.boot()
            times.append((time.perf_counter() - t0) * 1000)
            assert len(r.sessions) == 20 and len(r.archived) == 80
    # The first three warm the imports and the page cache.
    return {"boot_100_ms": median("boot_100_ms", times[3:])}


def fleet_load(playwright) -> dict:
    browser = playwright.chromium.launch()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            make_world(Path(tmp))
            srv = Server(Path(tmp), {}, keep_config=True)
            try:
                page = browser.new_page()
                # The first loads compile the page's code and warm the
                # server: they fell from about 800 to 300 ms over five loads.
                loads = []
                for _ in range(12):
                    page.goto(srv.url + "#fleet")
                    loads.append(
                        page.wait_for_function(
                            "() => document.querySelectorAll('.card').length >= 20 && performance.now()",
                            timeout=60_000,
                        ).json_value()
                    )
                    page.goto("about:blank")
            finally:
                srv.stop()
    finally:
        browser.close()
    return {"fleet_load_ms": median("fleet_load_ms", loads[5:])}


FILES_TREE = 25_000


def files_view(playwright) -> dict:
    """The Files view on a tree of FILES_TREE files (#458). Each first open
    is a fresh context with the service worker blocked, so nothing is cached;
    the bytes are what that context fetched from vendor/codemirror/."""
    browser = playwright.chromium.launch()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(FILES_TREE):
                d = root / f"pkg{i // 1000:02d}" / f"mod{i // 50 % 20:02d}"
                d.mkdir(parents=True, exist_ok=True)
                (d / f"f{i:05d}.py").write_text(f"x = {i}\n")
            srv = Server(root, {})
            base = srv.url.split("#")[0]
            try:
                opens, kb = [], 0.0
                for _ in range(6):
                    ctx = browser.new_context(service_workers="block")
                    page = ctx.new_page()
                    page.goto(f"{base}#files=pkg00/mod00/f00000.py")
                    opens.append(
                        page.wait_for_function(
                            "() => document.querySelector('.v-files .cm-content') && performance.now()",
                            timeout=60_000,
                        ).json_value()
                    )
                    kb = page.evaluate(
                        "performance.getEntriesByType('resource')"
                        ".filter(e => e.name.includes('/vendor/codemirror/'))"
                        ".reduce((n, e) => n + e.encodedBodySize, 0) / 1000"
                    )
                    ctx.close()
                page = browser.new_page()
                page.goto(base + "#fleet")
                page.wait_for_selector("#a2[data-view=fleet]")
                picks = []
                for _ in range(8):
                    t0 = time.perf_counter()
                    page.keyboard.press("Alt+o")
                    page.wait_for_selector(
                        "#fpick:not([hidden]) .file-row", timeout=60_000
                    )
                    picks.append((time.perf_counter() - t0) * 1000)
                    page.keyboard.press("Escape")
                    page.wait_for_selector("#fpick", state="hidden")
            finally:
                srv.stop()
    finally:
        browser.close()
    return {
        "files_open_ms": median("files_open_ms", opens[1:]),
        "files_picker_cold_ms": picks[0],
        "files_picker_ms": median("files_picker_ms", picks[1:]),
        "files_view_kb": kb,
    }


async def _term_echo(samples: int = 200) -> dict:
    """A keystroke's echo in one terminal while another prints at full speed
    (terminal.py's READ_PER_S cap)."""
    import os
    import statistics
    import tempfile

    from aegis.term_store import CastStore
    from aegis.terminal import Terminal

    got = asyncio.Event()

    def heard(t, ops):
        if any("x" in o.get("o", "") for o in ops):
            got.set()

    def meta(name):
        return {
            "id": f"t-bench-{name}",
            "name": name,
            "profile": name,
            "cols": 120,
            "rows": 40,
        }

    env = {"PATH": os.environ["PATH"], "TERM": "xterm-256color"}
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        flood = Terminal(
            meta("flood"), CastStore(root / "f"), lambda t, ops: None, lambda t: None
        )
        echo = Terminal(meta("echo"), CastStore(root / "e"), heard, lambda t: None)
        await flood.start(
            ["yes", "a line of output from a busy program"], root, env, None
        )
        await echo.start(["cat"], root, env, None)
        await asyncio.sleep(0.5)
        loop = asyncio.get_running_loop()
        began, read0 = loop.time(), flood.bytes_read
        times = []
        for _ in range(samples):
            got.clear()
            t0 = time.perf_counter()
            echo.input("x")
            await asyncio.wait_for(got.wait(), 5)
            times.append((time.perf_counter() - t0) * 1000)
            await asyncio.sleep(0.01)
        rate = (flood.bytes_read - read0) / (loop.time() - began) / 1e6
        await flood.stop(1)
        await echo.stop(1)
    SAMPLES["term_echo_p95_ms"] = [round(x, 3) for x in times]
    return {
        "term_echo_p95_ms": statistics.quantiles(times, n=20)[18],
        "term_flood_mb_s": rate,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    metrics = asyncio.run(server_cost())
    metrics.update(asyncio.run(server_cost(harness="opencode")))
    metrics.update(asyncio.run(server_cost(harness="codex")))
    metrics.update(boot_cost())
    metrics.update(asyncio.run(_term_echo()))
    with sync_playwright() as p:
        metrics.update(browser_runs(p))
        metrics.update(fleet_load(p))
        metrics.update(files_view(p))
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout.strip()
    result = {
        "commit": commit,
        "host": socket.gethostname(),
        "metrics": metrics,
        "samples": SAMPLES,
    }
    for k, v in metrics.items():
        print(f"{k:24} {v:10.2f}" if isinstance(v, float) else f"{k:24} {v:10}")
    if args.out:
        args.out.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
