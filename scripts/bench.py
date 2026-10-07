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
  from navigation start to the first frame painted after the snapshot;
- memory after that replay: the server's RSS and the page's JS heap.

scripts/bench_compare.py turns two of these files into a table and warnings.
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
FIXTURE = ROOT / "tests" / "fixtures" / "session.jsonl"
FAKE = ROOT / "tests" / "fake_claude.py"
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
    from aegis.meta import MetaStore
    from aegis.session import Session, SpawnSpec
    from aegis.transcript.store import Store

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


# -- 4. many sessions: registry boot and the Fleet view ------------------------
def make_world(root: Path, n_open: int = 20, n_archived: int = 80) -> None:
    """A state directory with the fixture's store copied into many sessions."""
    (root / ".aegis.yaml").write_text(
        "agents:\n  bench: {model: m, effort: low, permission: full}\n"
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
            "claude_session_id": f"cs-{i}",
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
        for _ in range(5):
            t0 = time.perf_counter()
            r = Registry(roots, lambda ch, ops: None)
            r.boot()
            times.append((time.perf_counter() - t0) * 1000)
            assert len(r.sessions) == 20 and len(r.archived) == 80
    return {"boot_100_ms": min(times)}


def fleet_load(playwright) -> dict:
    browser = playwright.chromium.launch()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            make_world(Path(tmp))
            srv = Server(Path(tmp), {}, keep_config=True)
            try:
                page = browser.new_page()
                loads = []
                for _ in range(3):
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
    return {"fleet_load_ms": min(loads)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    metrics = asyncio.run(server_cost())
    metrics.update(boot_cost())
    with sync_playwright() as p:
        metrics.update(browser_runs(p))
        metrics.update(fleet_load(p))
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
