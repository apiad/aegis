"""In-process instrumentation for aegis bench.

Standalone on purpose: the launcher copies this file next to the run and
imports it before aegis, so it must load into an aegis release older than
the bench itself. It never imports aegis at module level.

What it wraps is Textual's frame path: ``Screen._on_timer_update`` is the
frame tick; inside it ``Screen._refresh_layout`` is the layout pass,
``Compositor.render_update`` builds the update and ``App._display`` encodes
and writes it. A missing required hook raises, because a probe that
silently wraps nothing produces a green run that measured nothing.

``get_content_height`` and ``render_lines`` are counted on ``Widget``
itself; subclasses that override them are not counted.

Records are buffered in a deque and written by a sampler thread once a
second, so the event loop never does file I/O. The cost that remains is
the sampler's JSON encoding, which holds the GIL for about a millisecond
per second of busy streaming.
"""

from __future__ import annotations

import asyncio
import atexit
import collections
import gc
import importlib
import json
import os
import sys
import threading
import time

REQUIRED = (
    ("textual.screen", "Screen", "_on_timer_update", "tick"),
    ("textual.screen", "Screen", "_refresh_layout", "layout"),
    ("textual._compositor", "Compositor", "render_update", "compose"),
    ("textual.app", "App", "_display", "display"),
)
COUNTED = (
    ("textual.widget", "Widget", "get_content_height", "n_height"),
    ("textual.widget", "Widget", "render_lines", "n_render_lines"),
)
PAINT = ("aegis.tui.pane", "ConversationPane", "_paint_streaming", "paint")


class ProbeError(RuntimeError):
    pass


class _Sink:
    def __init__(self, path: str) -> None:
        self._fh = open(path, "a", encoding="utf-8")  # noqa: SIM115
        self._buf: collections.deque[dict] = collections.deque()
        self._lock = threading.Lock()

    def write(self, rec: dict) -> None:
        self._buf.append(rec)

    def flush(self) -> None:
        with self._lock:
            out = []
            while True:
                try:
                    out.append(json.dumps(self._buf.popleft()))
                except IndexError:
                    break
            if out:
                self._fh.write("\n".join(out) + "\n")
                self._fh.flush()


_SINK: _Sink | None = None
_CUR: dict | None = None
_COUNTS = {"n_height": 0, "n_render_lines": 0}
_GC = {"start": 0, "total": 0, "max": 0}
_GATE: dict | None = None
_LAG_TASK: asyncio.Task | None = None


def _span(name: str, fn):
    def wrapper(*args, **kwargs):
        global _CUR
        t0 = time.monotonic_ns()
        if name == "tick":
            outer, _CUR = (
                _CUR,
                {
                    "layout": 0,
                    "compose": 0,
                    "display": 0,
                    "n_height": 0,
                    "n_render_lines": 0,
                },
            )
            try:
                return fn(*args, **kwargs)
            finally:
                cur, _CUR = _CUR, outer
                _SINK.write(
                    {"k": "tick", "t0": t0, "dur_ns": time.monotonic_ns() - t0, **cur}
                )
        if name == "display" and args:
            _observe_app(args[0])
        try:
            return fn(*args, **kwargs)
        finally:
            dur = time.monotonic_ns() - t0
            if _CUR is not None and name in _CUR:
                _CUR[name] += dur
            else:
                _SINK.write({"k": name, "t0": t0, "dur_ns": dur})

    wrapper.__wrapped__ = fn
    return wrapper


def _counter(name: str, fn):
    def wrapper(*args, **kwargs):
        _COUNTS[name] += 1
        if _CUR is not None:
            _CUR[name] += 1
        return fn(*args, **kwargs)

    wrapper.__wrapped__ = fn
    return wrapper


def _sabotaged(ms: float, fn):
    def wrapper(*args, **kwargs):
        time.sleep(ms / 1000.0)
        return fn(*args, **kwargs)

    wrapper.__wrapped__ = fn
    return wrapper


def _observe_app(app) -> None:
    global _GATE, _LAG_TASK
    aegis = sys.modules.get("aegis")
    gate = {
        "k": "gate",
        "sync": bool(getattr(app, "_sync_available", False)),
        "headless": bool(getattr(app, "is_headless", False)),
        "aegis_file": getattr(aegis, "__file__", "") or "",
    }
    if gate != _GATE:
        _GATE = gate
        _SINK.write(dict(gate, t_ns=time.monotonic_ns()))
    if _LAG_TASK is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        _LAG_TASK = loop.create_task(_lag())


async def _lag() -> None:
    """How late a 5 ms sleep wakes: time the loop spent on something else."""
    interval = 5_000_000
    while True:
        t = time.monotonic_ns()
        await asyncio.sleep(interval / 1e9)
        _SINK.write(
            {
                "k": "lag",
                "t_ns": t,
                "lag_ns": max(0, time.monotonic_ns() - t - interval),
            }
        )


def _gc_cb(phase: str, info: dict) -> None:
    if phase == "start":
        _GC["start"] = time.monotonic_ns()
    elif _GC["start"]:
        d = time.monotonic_ns() - _GC["start"]
        _GC["total"] += d
        _GC["max"] = max(_GC["max"], d)


def _sampler() -> None:
    try:
        import psutil

        proc = psutil.Process()
    except Exception:  # noqa: BLE001 — memory fields stay null
        proc = None
    while True:
        rec = {
            "k": "sample",
            "t_ns": time.monotonic_ns(),
            "cpu_s": time.process_time(),
            "rss": None,
            "uss": None,
            "threads": threading.active_count(),
            "fds": None,
            "n_height": _COUNTS["n_height"],
            "n_render_lines": _COUNTS["n_render_lines"],
            "gc_ns_total": _GC["total"],
            "gc_ns_max": _GC["max"],
        }
        if proc is not None:
            try:
                mem = proc.memory_full_info()
                rec.update(
                    rss=mem.rss,
                    uss=mem.uss,
                    threads=proc.num_threads(),
                    fds=proc.num_fds(),
                )
            except Exception:  # noqa: BLE001
                pass
        _SINK.write(rec)
        _SINK.flush()
        time.sleep(1.0)


def _patch(mod: str, cls: str, meth: str, make) -> str:
    label = f"{cls}.{meth}"
    try:
        klass = getattr(importlib.import_module(mod), cls)
    except (ImportError, AttributeError) as exc:
        raise ProbeError(f"required hook {mod}.{label} not found") from exc
    orig = klass.__dict__.get(meth)
    if orig is None:
        raise ProbeError(f"required hook {mod}.{label} not found")
    setattr(klass, meth, make(orig))
    return label


def install() -> None:
    global _SINK
    path = os.environ.get("AEGIS_BENCH_PROBE")
    if not path:
        raise ProbeError("AEGIS_BENCH_PROBE is not set")
    _SINK = _Sink(path)
    installed = []
    for mod, cls, meth, name in REQUIRED:
        installed.append(_patch(mod, cls, meth, lambda f, n=name: _span(n, f)))
    for mod, cls, meth, name in COUNTED:
        installed.append(_patch(mod, cls, meth, lambda f, n=name: _counter(n, f)))
    optional_missing = []
    sabotage = float(os.environ.get("AEGIS_BENCH_SABOTAGE_MS") or 0)
    mod, cls, meth, name = PAINT
    try:
        # Importing the pane here is the same module the CLI imports next,
        # so the class patched is the class the app uses.
        installed.append(
            _patch(
                mod,
                cls,
                meth,
                lambda f: _span(name, _sabotaged(sabotage, f) if sabotage else f),
            )
        )
    except ProbeError:
        if sabotage:
            raise
        optional_missing.append(f"{cls}.{meth}")
    _SINK.write(
        {
            "k": "hooks",
            "pid": os.getpid(),
            "installed": installed,
            "optional_missing": optional_missing,
            "sabotage_ms": sabotage,
        }
    )
    gc.callbacks.append(_gc_cb)
    atexit.register(_SINK.flush)
    threading.Thread(target=_sampler, name="aegis-bench-probe", daemon=True).start()
