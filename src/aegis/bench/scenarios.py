"""Scenario timelines. Each drives aegis the way an operator does.

A scenario never computes metrics. It writes what it did and when into
``events.jsonl`` (the window, resize times, echo samples), and
``metrics.repeat_metrics`` folds that together with the rig's frames, the
fake agents' emits and the probe.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from aegis.bench import BenchError, ScenarioSkipped
from aegis.bench.host import cores, cpu_times, loadavg
from aegis.bench.launcher import Target
from aegis.bench.records import Recorder, read_jsonl
from aegis.bench.rig import Rig, pump, wait_until
from aegis.bench.script import (
    acp_chunks,
    load_fixture,
    make_script,
    synthetic_blocks,
    synthetic_fill,
)
from aegis.bench.world import World, build_world, client_argv, start_daemon, teardown

READY_TEXT = "type a message"
F3 = b"\x1bOR"
CTRL_T = b"\x14"
CTRL_RIGHT = b"\x1b[1;5C"
F10 = b"\x1b[21~"
FLEET_FOOTER = "esc/F10 close"


class ScenarioContext:
    def __init__(
        self,
        rep_dir: Path,
        target: Target,
        *,
        cols: int,
        rows: int,
        speed: float,
        sabotage_ms: int,
        profile: bool,
        keep: bool,
    ) -> None:
        self.rep_dir = Path(rep_dir)
        self.target = target
        self.cols, self.rows, self.speed = cols, rows, speed
        self.sabotage_ms, self.profile, self.keep = sabotage_ms, profile, keep
        self.events = Recorder(self.rep_dir / "events.jsonl")
        self.frames = Recorder(self.rep_dir / "frames.jsonl")
        self.world: World | None = None
        self.rigs: list[Rig] = []  # attached now; what pump reads
        self._all_rigs: list[Rig] = []  # ever attached; what gates cover
        self._tabs_open = 0

    # --- recording -----------------------------------------------------
    def metric(self, name: str, value: float) -> None:
        self.events.write(
            {"k": "metric", "name": name, "value": round(float(value), 3)}
        )

    def sample(self, name: str, value: float) -> None:
        self.events.write(
            {"k": "sample_ms", "name": name, "value": round(float(value), 3)}
        )

    def gate(self, name: str, ok: bool, detail: str = "") -> None:
        self.events.write({"k": "gate", "name": name, "ok": bool(ok), "detail": detail})

    def expect_markers(self, client: str = "a") -> None:
        self.events.write({"k": "expect_markers", "client": client})

    @contextlib.contextmanager
    def window(self):
        start = time.monotonic_ns()
        self._record_load()
        try:
            yield
        finally:
            self._record_load()
            self.events.write(
                {"k": "window", "start_ns": start, "end_ns": time.monotonic_ns()}
            )

    def _record_load(self) -> None:
        """Host load and CPU jiffies at a window edge: the numbers inside
        only compare with runs made on a similarly quiet machine."""
        idle, total = cpu_times()
        self.events.write(
            {
                "k": "load",
                "t_ns": time.monotonic_ns(),
                "l1": loadavg()[0],
                "cores": cores(),
                "cpu_idle": idle,
                "cpu_total": total,
            }
        )

    # --- world ---------------------------------------------------------
    def _wrap(self) -> list[str] | None:
        if not self.profile:
            return None
        return [
            "uvx",
            "py-spy",
            "record",
            "--format",
            "speedscope",
            "--output",
            str(self.rep_dir / "profile.speedscope.json"),
            "--",
        ]

    def boot(self, script: dict, *, default_agent: str = "bench") -> Rig:
        script = dict(script, speed=self.speed)
        self.world = build_world(
            self.rep_dir,
            self.target,
            script=script,
            default_agent=default_agent,
            sabotage_ms=self.sabotage_ms,
        )
        if self.target.topology == "daemon":
            self.metric(
                "startup.daemon_boot_ms", start_daemon(self.world, wrap=self._wrap())
            )
        rig = self.attach("a")
        self._tabs_open = 1
        return rig

    def attach(self, label: str) -> Rig:
        assert self.world is not None
        wrap = self._wrap() if self.target.topology == "in-process" else None
        rig = Rig(
            client_argv(self.world, view=f"bench-{label}", wrap=wrap),
            cwd=self.world.root,
            env=self.world.env,
            cols=self.cols,
            rows=self.rows,
            label=label,
            recorder=self.frames,
        )
        rig.start()
        self.rigs.append(rig)
        self._all_rigs.append(rig)
        if not wait_until(self.rigs, lambda: rig.first_frame_ns is not None, 90):
            raise BenchError(
                f"client {label} drew no frame in 90 s "
                f"(sync negotiated: {rig.saw_sync})"
            )
        if not wait_until(self.rigs, lambda: rig.contains(READY_TEXT), 60):
            raise BenchError(f"client {label} never drew {READY_TEXT!r}")
        now = time.monotonic_ns()
        if label == "a":
            self.metric(
                "startup.first_frame_ms", (rig.first_frame_ns - rig.t_start_ns) / 1e6
            )
            self.metric("startup.ready_ms", (now - rig.t_start_ns) / 1e6)
        self.events.write({"k": "client_ready", "client": label, "t_ns": now})
        return rig

    def detach(self, rig: Rig) -> None:
        rig.close()
        if rig in self.rigs:
            self.rigs.remove(rig)

    def tabs(self) -> list[str]:
        """Tab handles in order, from the daemon's workspace snapshot."""
        assert self.world is not None
        path = self.world.root / ".aegis" / "state" / "workspace.json"
        try:
            tabs = json.loads(path.read_text()).get("tabs", [])
        except (OSError, ValueError):
            return []
        return [t["handle"] for t in sorted(tabs, key=lambda t: t["order"])]

    def new_tab(self, rig: Rig, *, old_text: str = "compositor") -> str:
        """Open a tab with Ctrl+T, falling back to Ctrl+Right to reach it.

        Ctrl+T foregrounds its own tab, so the fallback is for older
        targets: they mount the new pane in the background, and a new tab
        is appended after the current one, so one Ctrl+Right reaches it.
        Keys, not a click on the label: seven labels do not fit in 120
        columns. ``tabs.switch_ms`` is therefore sampled only on a target
        that needs the key.

        The switch is confirmed on screen, never assumed. ``old_text`` is in
        every script's text, so the previous tab's transcript shows it and a
        fresh tab's does not. The count of open tabs is tracked here rather
        than read from ``workspace.json`` beforehand, because the daemon
        writes that snapshot lazily and a stale baseline once named the old
        tab as the new one. The time from key to switch is sampled as
        ``tabs.switch_ms``.
        """
        if not wait_until(self.rigs, lambda: self._shows(rig, old_text), 10):
            raise BenchError(
                f"the current tab shows no {old_text!r}, so a "
                "switch away from it cannot be confirmed"
            )
        opened = self._tabs_open
        rig.write(CTRL_T)
        if not wait_until(self.rigs, lambda: len(self.tabs()) > opened, 15):
            raise BenchError("Ctrl+T opened no tab")
        self._tabs_open = opened + 1
        handle = self.tabs()[opened]
        # Let the view mount the pane before moving to it; the label may
        # never be drawn when the tab bar is full, so this does not insist.
        wait_until(self.rigs, lambda: rig.contains(handle), 3)
        self.wait_quiet(rig, 300, timeout_s=1.0)
        if not self._shows(rig, old_text):
            return handle
        t = rig.write(CTRL_RIGHT)
        if not wait_until(self.rigs, lambda: not self._shows(rig, old_text), 10):
            raise BenchError(f"Ctrl+Right did not bring tab {handle} on screen")
        self.sample("tabs.switch_ms", (time.monotonic_ns() - t) / 1e6)
        self.wait_quiet(rig, 300, timeout_s=1.0)
        return handle

    @staticmethod
    def _shows(rig: Rig, text: str) -> bool:
        return any(text in line for line in rig.screen())

    def dump_screens(self) -> None:
        """Write each client's reconstructed screen beside the repeat, so a
        failure leaves evidence of what was on screen when it happened."""
        for rig in self._all_rigs:
            (self.rep_dir / f"screen-{rig.label}.txt").write_text(
                "\n".join(rig.screen()) + "\n"
            )

    def emits(self, kind: str) -> list[dict]:
        return [r for r in read_jsonl(self.rep_dir / "emit.jsonl") if r["k"] == kind]

    def send_prompt(self, rig: Rig, word: str, *, timeout_s: float = 20) -> int:
        """Type ``word`` and submit it, confirmed by the fake agent.

        A cold attach leaves focus on the tab bar, so this clicks the input
        first, as a user must. Enter is retried because a keystroke can
        land while the pane is still wiring its session; the fake agent's
        ``prompt`` record, not the screen, is the confirmation. Returns the
        pid of the fake agent that received it.
        """
        before = len(self.emits("prompt"))
        if not rig.click_text(READY_TEXT):
            raise BenchError(f"client {rig.label} has no {READY_TEXT!r} to click")
        wait_until(self.rigs, lambda: False, 0.1)
        rig.write(word.encode())
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            rig.write(b"\r")
            if wait_until(self.rigs, lambda: len(self.emits("prompt")) > before, 3):
                return self.emits("prompt")[before]["pid"]
        raise BenchError(f"prompt {word!r} never reached the fake agent")

    def send_to_new_tab(self, rig: Rig, word: str, pids: set[int]) -> int:
        """Send ``word`` and insist a fresh agent received it.

        A prompt that lands in an existing tab still reaches *an* agent, so
        without this check a failed tab switch passes and measures the
        wrong thing. It did, twice, before this check existed.
        """
        pid = self.send_prompt(rig, word)
        if pid in pids:
            raise BenchError(
                f"prompt {word!r} landed in an existing tab "
                f"(pid {pid}), not the tab just opened"
            )
        pids.add(pid)
        return pid

    def wait_turns(self, n: int, *, timeout_s: float) -> None:
        if not wait_until(
            self.rigs, lambda: len(self.emits("turn_end")) >= n, timeout_s
        ):
            raise BenchError(f"fewer than {n} turns finished in {timeout_s}s")

    def pump_for(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            pump(self.rigs)

    def wait_frame_after(
        self, rig: Rig, t_ns: int, timeout_s: float = 10
    ) -> int | None:
        ok = wait_until(self.rigs, lambda: (rig.last_frame_ns or 0) > t_ns, timeout_s)
        return rig.last_frame_ns if ok else None

    def wait_quiet(self, rig: Rig, quiet_ms: float = 300, timeout_s: float = 20) -> int:
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
            self.gate(f"sync_seen_{rig.label}", rig.saw_sync, "rig saw \\e[?2026h")
        try:
            # The probe's sampler flushes once a second; let it write the
            # tail of the window before the daemon goes.
            self.pump_for(1.2)
            for rig in list(self.rigs):
                self.detach(rig)
        finally:
            if self.world is not None:
                teardown(self.world, keep=self.keep)
            if self.profile:
                # A profiler that died without writing must fail the run,
                # not hand back a profiled run with no profile.
                prof = self.rep_dir / "profile.speedscope.json"
                self.gate(
                    "profile_written",
                    prof.exists() and prof.stat().st_size > 0,
                    str(prof),
                )
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
    ctx.metric("startup.warm_first_frame_ms", (warm.first_frame_ns - t0) / 1e6)


def claude_blocks(ctx: ScenarioContext) -> None:
    """A recorded claude session, replayed as aegis receives it today: a
    few large Markdown blocks and a tool call, at their recorded delays."""
    rig = ctx.boot(make_script({"go": load_fixture("claude-blocks")}))
    ctx.expect_markers()
    with ctx.window():
        ctx.send_prompt(rig, "go")
        ctx.wait_turns(1, timeout_s=300)
        ctx.pump_for(1.0)


def block_stream(ctx: ScenarioContext) -> None:
    """120 short text blocks at 50 ms.

    A real session's handful of large blocks yields two to four markers,
    too few for a percentile to mean anything; this yields 120.
    """
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


def acp_stream(ctx: ScenarioContext) -> None:
    rig = ctx.boot(make_script({"go": acp_chunks(500, 50)}), default_agent="bench-acp")
    ctx.expect_markers()
    with ctx.window():
        ctx.send_prompt(rig, "go")
        ctx.wait_turns(1, timeout_s=120)
        ctx.pump_for(1.0)


def typing(ctx: ScenarioContext) -> None:
    """Keystroke echo while a stream is running.

    A keystroke counts as echoed when a frame draws the last eight typed
    characters: Textual rewrites the input line as one span, and eight
    random consonants never occur in the streamed text.
    """
    import random

    rig = ctx.boot(make_script({"go": acp_chunks(900, 50)}), default_agent="bench-acp")
    typed = "".join(random.Random(7).choice("bcdfghjkmnpqrstvwxz") for _ in range(200))
    pending: list[tuple[str, int]] = []

    def on_frame(frame) -> None:
        while pending and pending[0][0] in frame.text:
            _, t_sent = pending.pop(0)
            ctx.sample("latency.echo_ms", (frame.t_ns - t_sent) / 1e6)

    with ctx.window():
        ctx.send_prompt(rig, "go")
        ctx.pump_for(1.0)
        rig.listeners.append(on_frame)
        for i, ch in enumerate(typed):
            t = rig.write(ch.encode())
            pending.append((typed[max(0, i - 7) : i + 1], t))
            ctx.pump_for(0.05)
        ctx.pump_for(1.0)
        rig.listeners.remove(on_frame)
    ctx.gate(
        "echo_lost",
        not pending,
        f"{len(pending)} of {len(typed)} keystrokes never echoed",
    )


def idle(ctx: ScenarioContext) -> None:
    """Three tabs with finished turns, then nothing: the CPU and wakeup
    floor an operator pays for leaving aegis open."""
    rig = ctx.boot(make_script({"ack": synthetic_blocks(3, 0, mark=False)}))
    pids = {ctx.send_prompt(rig, "ack")}
    for n in (1, 2):
        ctx.wait_turns(n, timeout_s=60)
        ctx.new_tab(rig)
        ctx.send_to_new_tab(rig, "ack", pids)
    ctx.wait_turns(3, timeout_s=60)
    ctx.wait_quiet(rig, 1000)
    with ctx.window():
        ctx.pump_for(20.0)


def many_tabs(ctx: ScenarioContext) -> None:
    """One visible stream while six background tabs stream too.

    Claude-shaped streams, not ACP: an ACP reply renders only when its
    turn ends, so a background ACP tab would cost nothing in the window.
    """
    # 1800 blocks at 50 ms is 90 s, so every background stream is still
    # running when the measured window opens, however slow the tab setup.
    rig = ctx.boot(
        make_script(
            {
                "bg": synthetic_blocks(1800, 50, mark=False),
                "go": synthetic_blocks(150, 50),
            }
        )
    )
    pids = {ctx.send_prompt(rig, "bg")}
    for _ in range(5):
        ctx.new_tab(rig)
        ctx.send_to_new_tab(rig, "bg", pids)
    ctx.new_tab(rig)
    ctx.expect_markers()
    with ctx.window():
        go_pid = ctx.send_to_new_tab(rig, "go", pids)
        if not wait_until(
            ctx.rigs,
            lambda: any(r["pid"] == go_pid for r in ctx.emits("turn_end")),
            120,
        ):
            raise BenchError("the visible stream did not finish")
        ctx.pump_for(1.0)


def fleet(ctx: ScenarioContext) -> None:
    """``many-tabs`` with F10's fleet grid up over the visible stream.

    The same seven Claude-shaped streams, so the two read against each
    other. The grid hides every transcript, so no marker can be drawn and
    none is expected: this measures what the grid costs to keep redrawing.
    Then it checks what the pty client drew: the band and a card per tab
    by handle, and after ``2`` the fleet gone with tab 2 active.
    """
    rig = ctx.boot(
        make_script(
            {
                "bg": synthetic_blocks(1800, 50, mark=False),
                "go": synthetic_blocks(150, 50, mark=False),
            }
        )
    )
    pids = {ctx.send_prompt(rig, "bg")}
    for _ in range(5):
        ctx.new_tab(rig)
        ctx.send_to_new_tab(rig, "bg", pids)
    ctx.new_tab(rig)
    with ctx.window():
        go_pid = ctx.send_to_new_tab(rig, "go", pids)
        rig.write(F10)
        if not wait_until(ctx.rigs, lambda: ctx._shows(rig, FLEET_FOOTER), 10):
            raise BenchError("F10 drew no fleet screen")
        if not wait_until(
            ctx.rigs,
            lambda: any(r["pid"] == go_pid for r in ctx.emits("turn_end")),
            120,
        ):
            raise BenchError("the stream under the fleet did not finish")
        ctx.pump_for(1.0)
    ctx.wait_quiet(rig, 1000, timeout_s=5)
    handles = ctx.tabs()
    screen = rig.screen()
    (ctx.rep_dir / "screen-fleet.txt").write_text("\n".join(screen) + "\n")
    text = "\n".join(screen)
    if not any(" agents " in line for line in screen[:4]):
        raise BenchError("the fleet screen shows no band")
    missing = [h for n, h in enumerate(handles, 1) if f"─ {n} {h} " not in text]
    if missing:
        raise BenchError(f"the fleet screen shows no card for {missing}")
    rig.write(b"2")
    if not wait_until(ctx.rigs, lambda: not ctx._shows(rig, FLEET_FOOTER), 10):
        raise BenchError("pressing 2 left the fleet screen up")
    ctx.wait_quiet(rig, 500, timeout_s=5)
    screen = rig.screen()
    (ctx.rep_dir / "screen-after-2.txt").write_text("\n".join(screen) + "\n")
    if _drawn_reversed(rig, f"2 {handles[1]}") is not True:
        raise BenchError(f"after 2, tab 2 ({handles[1]}) is not the active tab")
    if _drawn_reversed(rig, f"3 {handles[2]}") is not False:
        raise BenchError(f"after 2, tab 3 ({handles[2]}) is drawn as active too")


def _drawn_reversed(rig: Rig, label: str) -> bool | None:
    """Whether the newest frame that drew ``label`` drew it in reverse
    video, which is how the tab bar marks the active tab; None when no kept
    frame drew it. The grid keeps no attributes, so this reads raw frames.
    """
    for raw in reversed(rig._raw_frames):
        text = raw.decode(errors="replace")
        at = text.rfind(label)
        if at < 0:
            continue
        sgrs = _SGR.findall(text, 0, at)
        if not sgrs:
            return False
        params = sgrs[-1].split(";")
        i = 0
        while i < len(params):
            if params[i] in ("38", "48"):  # extended colour and its arguments
                i += 5 if params[i + 1 : i + 2] == ["2"] else 3
                continue
            if params[i] == "7":
                return True
            i += 1
        return False
    return None


_SGR = re.compile(r"\x1b\[([0-9;]*)m")


def two_clients(ctx: ScenarioContext) -> None:
    rig = ctx.boot(make_script({"go": synthetic_blocks(200, 50)}))
    ctx.expect_markers("a")
    ctx.expect_markers("b")
    with ctx.window():
        ctx.send_prompt(rig, "go")
        ctx.pump_for(2.0)
        ctx.attach("b")
        ctx.wait_turns(1, timeout_s=180)
        ctx.pump_for(1.0)


def claude_stream(ctx: ScenarioContext) -> None:
    """Token deltas, when aegis asks claude for them.

    The fake records its argv when aegis starts it, so the skip is decided
    by what aegis actually passed, not by reading aegis's source.
    """
    try:
        steps = load_fixture("claude-stream")
    except FileNotFoundError as exc:
        raise ScenarioSkipped("fixture claude-stream is not recorded") from exc
    rig = ctx.boot(make_script({"go": steps}))
    ctx.expect_markers()
    with ctx.window():
        ctx.send_prompt(rig, "go")
        argv = ctx.emits("argv")
        if argv and not argv[-1]["partial"]:
            raise ScenarioSkipped(
                "aegis does not pass --include-partial-messages to claude"
            )
        ctx.wait_turns(1, timeout_s=300)
        ctx.pump_for(1.0)


def soak(ctx: ScenarioContext) -> None:
    """Ten minutes of repeated turns: does memory keep growing?"""
    rig = ctx.boot(
        make_script(
            {"soak": synthetic_fill(100) + synthetic_blocks(50, 20, mark=False)}
        )
    )
    turns = 0
    with ctx.window():
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            ctx.send_prompt(rig, "soak")
            turns += 1
            ctx.wait_turns(turns, timeout_s=300)
    lines = sum(r["lines"] for r in ctx.emits("turn_end"))
    samples = [
        r
        for r in read_jsonl(ctx.rep_dir / "probe.jsonl")
        if r["k"] == "sample" and r.get("rss")
    ]
    if len(samples) >= 2 and lines:
        growth_mb = (samples[-1]["rss"] - samples[0]["rss"]) / 2**20
        ctx.metric("mem.rss_growth_mb_per_1k_lines", growth_mb / (lines / 1000))


def selftest_stream(ctx: ScenarioContext) -> None:
    """Used by ``aegis bench selftest``: blocks 100 ms apart, so each one is
    its own streaming paint and a sleep added to the paint shows up once
    per marker."""
    rig = ctx.boot(make_script({"go": synthetic_blocks(60, 100)}))
    ctx.expect_markers()
    with ctx.window():
        ctx.send_prompt(rig, "go")
        ctx.wait_turns(1, timeout_s=120)
        ctx.pump_for(1.0)


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario("startup", startup, "cold boot, first frame, warm re-attach"),
        Scenario(
            "claude-blocks",
            claude_blocks,
            "a recorded claude session, whole blocks as aegis gets them",
        ),
        Scenario(
            "block-stream",
            block_stream,
            "120 short blocks at 50 ms, for latency percentiles",
        ),
        Scenario("deep-stream", deep_stream, "stream after ~300 mounted blocks"),
        Scenario("resize", resize, "resizes and sidebar toggles at ~300 blocks"),
        Scenario(
            "acp-stream", acp_stream, "chunk-by-chunk streaming through the ACP driver"
        ),
        Scenario("typing", typing, "keystroke echo while a stream runs"),
        Scenario("idle", idle, "three finished tabs, nothing happening, 20 s"),
        Scenario("many-tabs", many_tabs, "one visible stream, six background streams"),
        Scenario("fleet", fleet, "many-tabs' streams under the F10 fleet grid"),
        Scenario(
            "two-clients",
            two_clients,
            "a second client attaches mid-stream",
            topologies=("daemon",),
        ),
        Scenario(
            "claude-stream", claude_stream, "token deltas, if aegis requests them"
        ),
        Scenario("soak", soak, "ten minutes of turns; memory growth"),
        Scenario("selftest-stream", selftest_stream, "used by aegis bench selftest"),
    )
}
DEFAULT = [
    "startup",
    "idle",
    "claude-blocks",
    "block-stream",
    "claude-stream",
    "acp-stream",
    "deep-stream",
    "resize",
    "typing",
    "many-tabs",
    "two-clients",
]
QUICK = ["startup", "block-stream", "acp-stream", "resize"]
