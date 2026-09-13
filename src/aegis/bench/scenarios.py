"""Scenario timelines. Each drives aegis the way an operator does.

A scenario never computes metrics. It writes what it did and when into
``events.jsonl`` (the window, resize times, echo samples), and
``metrics.repeat_metrics`` folds that together with the rig's frames, the
fake agents' emits and the probe.
"""
from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from aegis.bench import BenchError
from aegis.bench.launcher import Target
from aegis.bench.records import Recorder, read_jsonl
from aegis.bench.rig import Rig, pump, wait_until
from aegis.bench.script import make_script, synthetic_blocks, synthetic_fill
from aegis.bench.world import (
    World, build_world, client_argv, start_daemon, teardown)

READY_TEXT = "type a message"
F3 = b"\x1bOR"
CTRL_T = b"\x14"


class ScenarioContext:
    def __init__(self, rep_dir: Path, target: Target, *, cols: int,
                 rows: int, speed: float, sabotage_ms: int, profile: bool,
                 keep: bool) -> None:
        self.rep_dir = Path(rep_dir)
        self.target = target
        self.cols, self.rows, self.speed = cols, rows, speed
        self.sabotage_ms, self.profile, self.keep = sabotage_ms, profile, keep
        self.events = Recorder(self.rep_dir / "events.jsonl")
        self.frames = Recorder(self.rep_dir / "frames.jsonl")
        self.world: World | None = None
        self.rigs: list[Rig] = []        # attached now; what pump reads
        self._all_rigs: list[Rig] = []   # ever attached; what gates cover

    # --- recording -----------------------------------------------------
    def metric(self, name: str, value: float) -> None:
        self.events.write({"k": "metric", "name": name,
                           "value": round(float(value), 3)})

    def sample(self, name: str, value: float) -> None:
        self.events.write({"k": "sample_ms", "name": name,
                           "value": round(float(value), 3)})

    def gate(self, name: str, ok: bool, detail: str = "") -> None:
        self.events.write({"k": "gate", "name": name, "ok": bool(ok),
                           "detail": detail})

    def expect_markers(self, client: str = "a") -> None:
        self.events.write({"k": "expect_markers", "client": client})

    @contextlib.contextmanager
    def window(self):
        start = time.monotonic_ns()
        try:
            yield
        finally:
            self.events.write({"k": "window", "start_ns": start,
                               "end_ns": time.monotonic_ns()})

    # --- world ---------------------------------------------------------
    def _wrap(self) -> list[str] | None:
        if not self.profile:
            return None
        return ["uvx", "py-spy", "record", "--format", "speedscope",
                "--output", str(self.rep_dir / "profile.speedscope.json"),
                "--"]

    def boot(self, script: dict, *, default_agent: str = "bench") -> Rig:
        script = dict(script, speed=self.speed)
        self.world = build_world(self.rep_dir, self.target, script=script,
                                 default_agent=default_agent,
                                 sabotage_ms=self.sabotage_ms)
        if self.target.topology == "daemon":
            self.metric("startup.daemon_boot_ms",
                        start_daemon(self.world, wrap=self._wrap()))
        return self.attach("a")

    def attach(self, label: str) -> Rig:
        assert self.world is not None
        wrap = self._wrap() if self.target.topology == "in-process" else None
        rig = Rig(client_argv(self.world, view=f"bench-{label}", wrap=wrap),
                  cwd=self.world.root, env=self.world.env, cols=self.cols,
                  rows=self.rows, label=label, recorder=self.frames)
        rig.start()
        self.rigs.append(rig)
        self._all_rigs.append(rig)
        if not wait_until(self.rigs, lambda: rig.first_frame_ns is not None,
                          90):
            raise BenchError(f"client {label} drew no frame in 90 s "
                             f"(sync negotiated: {rig.saw_sync})")
        if not wait_until(self.rigs, lambda: rig.contains(READY_TEXT), 60):
            raise BenchError(f"client {label} never drew {READY_TEXT!r}")
        now = time.monotonic_ns()
        if label == "a":
            self.metric("startup.first_frame_ms",
                        (rig.first_frame_ns - rig.t_start_ns) / 1e6)
            self.metric("startup.ready_ms", (now - rig.t_start_ns) / 1e6)
        self.events.write({"k": "client_ready", "client": label,
                           "t_ns": now})
        return rig

    def detach(self, rig: Rig) -> None:
        rig.close()
        if rig in self.rigs:
            self.rigs.remove(rig)

    def emits(self, kind: str) -> list[dict]:
        return [r for r in read_jsonl(self.rep_dir / "emit.jsonl")
                if r["k"] == kind]

    def send_prompt(self, rig: Rig, word: str, *,
                    timeout_s: float = 20) -> None:
        """Type ``word`` and submit it, confirmed by the fake agent.

        A cold attach leaves focus on the tab bar, so this clicks the input
        first, as a user must. Enter is retried because a keystroke can
        land while the pane is still wiring its session; the fake agent's
        ``prompt`` record, not the screen, is the confirmation.
        """
        before = len(self.emits("prompt"))
        if not rig.click_text(READY_TEXT):
            raise BenchError(f"client {rig.label} has no {READY_TEXT!r} "
                             "to click")
        wait_until(self.rigs, lambda: False, 0.1)
        rig.write(word.encode())
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            rig.write(b"\r")
            if wait_until(self.rigs,
                          lambda: len(self.emits("prompt")) > before, 3):
                return
        raise BenchError(f"prompt {word!r} never reached the fake agent")

    def wait_turns(self, n: int, *, timeout_s: float) -> None:
        if not wait_until(self.rigs, lambda: len(self.emits("turn_end")) >= n,
                          timeout_s):
            raise BenchError(f"fewer than {n} turns finished in {timeout_s}s")

    def pump_for(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            pump(self.rigs)

    def wait_frame_after(self, rig: Rig, t_ns: int,
                         timeout_s: float = 10) -> int | None:
        ok = wait_until(self.rigs, lambda: (rig.last_frame_ns or 0) > t_ns,
                        timeout_s)
        return rig.last_frame_ns if ok else None

    def wait_quiet(self, rig: Rig, quiet_ms: float = 300,
                   timeout_s: float = 20) -> int:
        """Pump until ``rig`` draws nothing for ``quiet_ms``; the time of
        its last frame, which is when the screen settled."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            pump(self.rigs)
            last = rig.last_frame_ns or 0
            if time.monotonic_ns() - last > quiet_ms * 1e6:
                return last
        return rig.last_frame_ns or 0

    def close(self) -> None:
        for rig in self._all_rigs:
            self.gate(f"sync_seen_{rig.label}", rig.saw_sync,
                      "rig saw \\e[?2026h")
        try:
            # The probe's sampler flushes once a second; let it write the
            # tail of the window before the daemon goes.
            self.pump_for(1.2)
            for rig in list(self.rigs):
                self.detach(rig)
        finally:
            if self.world is not None:
                teardown(self.world, keep=self.keep)
            self.events.close()
            self.frames.close()


@dataclass(frozen=True)
class Scenario:
    name: str
    fn: Callable[[ScenarioContext], None]
    description: str
    topologies: tuple[str, ...] = ("daemon", "in-process")


def _fill_script(**prompts: list[dict]) -> dict:
    return make_script({"fill": synthetic_fill(150), **prompts})


def startup(ctx: ScenarioContext) -> None:
    rig = ctx.boot(make_script({}))
    if ctx.target.topology != "daemon":
        return
    ctx.detach(rig)
    t0 = time.monotonic_ns()
    warm = ctx.attach("a2")
    ctx.metric("startup.warm_first_frame_ms",
               (warm.first_frame_ns - t0) / 1e6)


def claude_blocks(ctx: ScenarioContext) -> None:
    rig = ctx.boot(make_script({"go": synthetic_blocks(120, 50)}))
    ctx.expect_markers()
    with ctx.window():
        ctx.send_prompt(rig, "go")
        ctx.wait_turns(1, timeout_s=180)
        ctx.pump_for(1.0)


def deep_stream(ctx: ScenarioContext) -> None:
    rig = ctx.boot(_fill_script(go=synthetic_blocks(80, 50)))
    ctx.send_prompt(rig, "fill")
    ctx.wait_turns(1, timeout_s=180)
    ctx.wait_quiet(rig, 500)
    ctx.expect_markers()
    with ctx.window():
        ctx.send_prompt(rig, "go")
        ctx.wait_turns(2, timeout_s=180)
        ctx.pump_for(1.0)


def _time_change(ctx: ScenarioContext, rig: Rig, t: int, group: str) -> None:
    first = ctx.wait_frame_after(rig, t)
    if first is None:
        raise BenchError(f"no frame after {group} change")
    settle = ctx.wait_quiet(rig, 300)
    ctx.sample(f"{group}.first_frame_ms", (first - t) / 1e6)
    ctx.sample(f"{group}.settle_ms", (settle - t) / 1e6)


def resize(ctx: ScenarioContext) -> None:
    rig = ctx.boot(_fill_script())
    ctx.send_prompt(rig, "fill")
    ctx.wait_turns(1, timeout_s=180)
    ctx.wait_quiet(rig, 500)
    with ctx.window():
        for i in range(5):
            cols = ctx.cols - 20 if i % 2 == 0 else ctx.cols
            _time_change(ctx, rig, rig.resize(cols, ctx.rows), "resize")
        for _ in range(5):
            _time_change(ctx, rig, rig.write(F3), "sidebar")


SCENARIOS: dict[str, Scenario] = {s.name: s for s in (
    Scenario("startup", startup, "cold boot, first frame, warm re-attach"),
    Scenario("claude-blocks", claude_blocks,
             "whole text blocks, as aegis receives claude today"),
    Scenario("deep-stream", deep_stream, "stream after ~300 mounted blocks"),
    Scenario("resize", resize, "resizes and sidebar toggles at ~300 blocks"),
)}
DEFAULT = ["startup", "claude-blocks", "deep-stream", "resize"]
QUICK = ["startup", "claude-blocks", "resize"]
