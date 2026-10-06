"""Measure aegis2's transcript path. Reports; never gates.

    uv run python scripts/bench2.py [--out FILE]

Four measurements, all lower-is-better, over the recorded session in
tests/aegis2/fixtures/session.jsonl:

- server cost per Claude line: parse, store, fold, publish and JSON-encode
  the patch, in process, p50 and p95;
- browser latency per tool entry: from the fake claude writing the line to
  the row being in the DOM of headless Chromium, through a real
  ``aegis2 serve``, p50 and p95;
- cold load: the fixture replayed to 2,000+ entries, then a page load timed
  from navigation start to the first frame painted after the snapshot;
- memory after that replay: the server's RSS and the page's JS heap.

scripts/bench2_compare.py turns two of these files into a table and warnings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "aegis2" / "fixtures" / "session.jsonl"
FAKE = ROOT / "tests" / "aegis2" / "fake_claude.py"
COLD_ENTRIES = 2000

sys.path.insert(0, str(ROOT / "src"))


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def claude_lines() -> list[str]:
    return [
        r["line"] for r in map(json.loads, FIXTURE.open()) if r.get("src") == "claude"
    ]


# -- 1. server cost per line --------------------------------------------------
async def server_cost(rounds: int = 40) -> dict:
    from aegis2.meta import MetaStore
    from aegis2.session import Session, SpawnSpec
    from aegis2.transcript.store import Store

    lines = claude_lines()
    samples: list[float] = []
    with tempfile.TemporaryDirectory() as tmp:

        def sink(ch: str, ops: list[dict]) -> None:
            # What the websocket would encode for one subscriber.
            json.dumps(
                {"t": "patch", "channel": ch, "seq": 1, "ops": ops}, ensure_ascii=False
            )

        s = Session(
            log_id="bench",
            spec=SpawnSpec("b", "m", "low", "full", Path(tmp)),
            handle="bench-one",
            store=Store(Path(tmp) / "t.jsonl"),
            stderr_path=Path(tmp) / "e",
            claude_bin="true",
            publish=sink,
            metas=MetaStore(Path(tmp) / "sessions"),
        )
        s.status = "idle"
        for r in range(rounds):
            for line in lines:
                if r:
                    line = line.replace('"toolu_', f'"toolu_r{r}_')
                t0 = time.perf_counter_ns()
                s._on_line(line)
                samples.append((time.perf_counter_ns() - t0) / 1000)
        s.store.close()
    return {
        "server_line_p50_us": pct(samples, 0.5),
        "server_line_p95_us": pct(samples, 0.95),
        "server_lines": len(samples),
    }


# -- 2 and 3: through a real server and a real browser ------------------------
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, tmp: Path, env: dict) -> None:
        (tmp / ".aegis.yaml").write_text(
            "agents:\n  bench: {model: m, effort: low, permission: full}\n"
        )
        fake = tmp / "claude"
        fake.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
        fake.chmod(0o755)
        port = free_port()
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "aegis2",
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
            env={**os.environ, "PYTHONPATH": str(ROOT / "src"), **env},
        )
        self.url = ""
        while not self.url:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("aegis2 serve exited")
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


def browser_runs(playwright) -> dict:
    out: dict = {}
    browser = playwright.chromium.launch(args=["--enable-precise-memory-info"])
    try:
        # Latency: a paced replay, each tool row's DOM time against its emit time.
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "emit.log"
            rounds = 4
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
                emitted = dict(line.split() for line in log.read_text().splitlines())
                lat = [
                    seen[i] - float(t) * 1000 for i, t in emitted.items() if i in seen
                ]
                out.update(
                    browser_entry_p50_ms=pct(lat, 0.5),
                    browser_entry_p95_ms=pct(lat, 0.95),
                    browser_entries=len(lat),
                )
            finally:
                srv.stop()

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
                start_session(page, srv.url)
                t0 = time.monotonic()
                page.wait_for_function(
                    "n => document.querySelectorAll('#entries .row').length >= n",
                    arg=COLD_ENTRIES,
                    timeout=180_000,
                )
                print(
                    f"replayed to {COLD_ENTRIES} rows in {time.monotonic() - t0:.1f}s",
                    file=sys.stderr,
                )
                page.wait_for_function(
                    "document.getElementById('s-status').textContent === 'idle'",
                    timeout=60_000,
                )
                loads = []
                for _ in range(3):
                    page.reload()
                    page.wait_for_function(
                        "window.__a2snapshot && window.__a2snapshot.painted",
                        timeout=60_000,
                    )
                    loads.append(page.evaluate("window.__a2snapshot.painted"))
                out.update(
                    cold_load_ms=min(loads),
                    cold_load_entries=page.evaluate("window.__a2snapshot.count"),
                    server_rss_mb=srv.rss_mb(),
                    browser_heap_mb=page.evaluate("performance.memory.usedJSHeapSize")
                    / 2**20,
                )
            finally:
                srv.stop()
    finally:
        browser.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    metrics = asyncio.run(server_cost())
    with sync_playwright() as p:
        metrics.update(browser_runs(p))
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout.strip()
    result = {"commit": commit, "host": socket.gethostname(), "metrics": metrics}
    for k, v in metrics.items():
        print(f"{k:24} {v:10.2f}" if isinstance(v, float) else f"{k:24} {v:10}")
    if args.out:
        args.out.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
