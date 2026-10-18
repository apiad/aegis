"""Notices: what a session is told about the host's disk, memory, CPU and quota.

A notice never starts a turn and never goes into the system prompt: it rides
on the session's next send or its next aegis tool result. Reading a signal
never raises; a missing /proc or PSI file reads as None, and None means no
notice of that kind.

Each session is told a kind once per episode: ``Session.notices_sent`` holds
the level it last got, so a block is pending when the level differs and a
clear is owed when the kind is no longer active. That record is in the meta;
the episodes and their askers live here, in memory, and are re-read from the
signals after a restart. One session the person opened is the asker per kind
and owner, so nine agents do not ask about the same disk.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from . import host
from .monitors import iso_now
from .transcript.entries import NOTICE_HEADER

if TYPE_CHECKING:
    from .app import App
    from .session import Session

log = logging.getLogger("aegis.notices")

EVERY_S = 30.0  # the loop that turns levels and toasts at critical
CACHE_S = 5.0  # a reading is reused this long

Role = Literal["asker", "person", "spawned", "worker"]


def psi(text: str) -> dict | None:
    """``{"some": {"avg10": .., "avg60": .., ...}, "full": {...}}`` from a
    /proc/pressure file, or None when it holds no PSI line."""
    out: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        name, *fields = line.split()
        if name not in ("some", "full"):
            continue
        vals: dict[str, float] = {}
        for f in fields:
            k, _, v = f.partition("=")
            try:
                vals[k] = float(v)
            except ValueError:
                return None
        out[name] = vals
    return out or None


def cpu_pct(psi_cpu: dict | None, loadavg: str | None, ncpu: int) -> float | None:
    """PSI CPU some avg60. Without PSI, the 1-minute load over the CPU count
    times 25, so a load of twice the CPUs reads as 50."""
    if psi_cpu and "avg60" in psi_cpu.get("some", {}):
        return psi_cpu["some"]["avg60"]
    if loadavg is None or ncpu < 1:
        return None
    try:
        return round(float(loadavg.split()[0]) / ncpu * 25, 1)
    except (IndexError, ValueError):
        return None


def next_level(prev: str | None, value: float, levels: dict) -> str | None:
    """The level after ``value``, from ``prev`` (None, warn or critical). An
    episode never steps down from critical, and ends only below ``clear``."""
    critical = levels.get("critical")
    if prev is None:
        if critical is not None and value >= critical:
            return "critical"
        return "warn" if value >= levels["on"] else None
    if value < levels["clear"]:
        return None
    if critical is not None and value >= critical:
        return "critical"
    return prev


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def _disk(paths: list[Path]) -> dict | None:
    """The fullest filesystem holding any of ``paths``, each counted once."""
    best: dict | None = None
    seen: set[int] = set()
    for p in paths:
        try:
            dev = os.stat(p).st_dev
            if dev in seen:
                continue
            seen.add(dev)
            d = host.disk(p)
            st = os.statvfs(p)
        except OSError:
            continue
        if d is None:
            continue
        free = round(st.f_bavail * st.f_frsize / host.GB, 1)
        if best is None or d["pct"] > best["pct"]:
            best = {"pct": d["pct"], "free_gb": free, "total_gb": d["total_gb"]}
    return best


def _memory(proc: Path) -> dict | None:
    text = _read(proc / "meminfo")
    m = None if text is None else host.memory(text)
    if m is None:
        return None
    pressure = _read(proc / "pressure" / "memory")
    p = None if pressure is None else psi(pressure)
    full = (p or {}).get("full", {}).get("avg60")
    return {
        "pct": m["pct"],
        "avail_gb": round(m["total_gb"] - m["used_gb"], 1),
        "total_gb": m["total_gb"],
        "psi_full": full,
    }


def _cpu(proc: Path) -> dict | None:
    pressure = _read(proc / "pressure" / "cpu")
    pct = cpu_pct(
        None if pressure is None else psi(pressure),
        _read(proc / "loadavg"),
        os.cpu_count() or 1,
    )
    return None if pct is None else {"pct": pct}


def read_signals(paths: list[Path], proc: Path = Path("/proc")) -> dict:
    """One reading of every host signal; a kind that cannot be read is None."""
    return {"disk": _disk(paths), "memory": _memory(proc), "cpu": _cpu(proc)}


LEAD = {
    "disk": "The disk holding aegis is {pct}% full: {free_gb} GB free of {total_gb} GB, measured just now.",
    "memory": "Memory is {pct}% used: {avail_gb} GB available of {total_gb} GB, measured just now.",
    "cpu": "The CPU is saturated: tasks waited for a CPU {pct}% of the last minute.",
    "quota": "The {label} quota is at {percent}% of its {window} window, on pace for {projected}%.",
}
CRITICAL = " This is critical."
ACT = {
    "disk": "Free what this session created and no longer needs (your merged worktrees, build output, temporary files), re-measuring with df before you delete. Do not touch shared caches, another session's work or anything tracked by git. Start no large download or build.",
    "memory": "Stop the servers and watchers you started and no longer use. Run no parallel test lane or build, and enqueue no more workers.",
    "cpu": "Run tests with fewer workers. Re-run a timing failure or a make bench number before you trust it, and say the host was loaded when you report one.",
    "quota": "Use the cheapest queue for workers, spawn no new session, and finish what you started before you start more.",
}
QUOTA_CRITICAL = " Tell the person before you enqueue anything."
MEMORY_OWN = " Your session's processes hold {rss_gb} GB."
TAIL = {
    "asker": "If that is not enough, ask the person what to do, in the message that ends your current turn, naming the largest candidates and their sizes. Do not start a task of your own for it.",
    "person": "Another session is asking the person about the rest; do not ask.",
    "spawned": "If that is not enough, say so in your result to the agent that started you; do not ask the person.",
    "worker": "Do not ask anyone; your result goes back to whoever started you.",
}
ONCE = "aegis tells you once, and again when it clears."
CLEAR = {
    "disk": "The disk holding aegis is back to {pct}% full. Work as usual.",
    "memory": "Memory is back to {pct}% used. Work as usual.",
    "cpu": "The CPU is no longer saturated. Run tests as usual.",
    "quota": "The {label} quota is back on pace. Work as usual.",
    "announce": "The announcement {id} has ended.",
}
ANNOUNCED = "{text}\nThis was written by {author}, not measured by aegis."
NO_ASKER = ("cpu", "announce")  # nothing of the person's to decide


def _shown(reading: dict) -> dict:
    """A reading's values as the text prints them: percents whole, GB to one
    decimal."""
    out = dict(reading)
    for k, v in reading.items():
        if isinstance(v, int | float) and not isinstance(v, bool):
            out[k] = f"{v:.1f}" if k.endswith("_gb") else round(v)
    return out


def body(kind: str, level: str, reading: dict, role: Role) -> str:
    """The text under a notice's header. ``kind`` is the bare kind (``quota``,
    not ``quota:claude``); ``level`` is warn, critical or clear."""
    r = _shown(reading)
    if level == "clear":
        return CLEAR[kind].format(**r)
    if kind == "announce":
        return f"{ANNOUNCED.format(**r)}\n{ONCE}"
    lead = LEAD[kind].format(**r)
    if kind == "memory" and reading.get("rss_gb") is not None:
        lead += MEMORY_OWN.format(**r)
    act = ACT[kind]
    if level == "critical":
        lead += CRITICAL
        if kind == "quota":
            act += QUOTA_CRITICAL
    if kind == "cpu" and role == "asker":
        role = "person"
    return "\n".join((lead, act, TAIL[role], ONCE))


def block(key: str, level: str, reading: dict, role: Role) -> str:
    """One notice as a session reads it: the inbox's header shape, then the body."""
    kind = key.partition(":")[0]
    return f"{NOTICE_HEADER}{key} · {level} · {iso_now()}\n" + body(
        kind, level, reading, role
    )


def _summary(kind: str, r: dict) -> str:
    r = _shown(r)
    if kind == "disk":
        return f"{r['pct']}% full, {r['free_gb']} GB free"
    if kind == "memory":
        return f"{r['pct']}% used, {r['avail_gb']} GB available"
    if kind == "quota":
        return f"{r['label']} at {r['percent']}% of its {r['window']} window"
    return str(r.get("text", ""))


def _quota(providers: list[dict]) -> dict[str, tuple[str, dict]]:
    """Each provider whose account reads warning or critical in any window,
    with the worst window as its reading."""
    rank = {"warning": 1, "critical": 2}
    out = {}
    for p in providers:
        ws = [w for w in p.get("windows", ()) if w.get("severity") in rank]
        if not ws:
            continue
        w = max(ws, key=lambda w: (rank[w["severity"]], w.get("percent") or 0))
        reading = {
            "label": p["label"],
            "harness": p["harness"],
            "window": w["label"],
            "percent": w.get("percent") or 0,
            "projected": w.get("projected") or w.get("percent") or 0,
        }
        level = "critical" if w["severity"] == "critical" else "warn"
        out[f"quota:{p['name']}"] = (level, reading)
    return out


class Notices:
    def __init__(
        self,
        app: App,
        *,
        read: Callable[[list[Path]], dict] = read_signals,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._app = app
        self._read = read
        self._clock = clock
        self._cached: tuple[float, dict] | None = None
        # Every active kind: key -> (level, reading). A kind leaves when its
        # episode ends, so a session holding it in notices_sent is owed a clear.
        self._active: dict[str, tuple[str, dict]] = {}
        # The last reading per key, which a clear prints.
        self._last: dict[str, dict] = {}
        self._askers: dict[tuple[str, str | None], str] = {}
        self._toasted: set[str] = set()
        # Several aegis calls in one parallel batch race for the same blocks.
        self._lock = threading.RLock()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        # The critical toast must fire while nobody is talking to anyone.
        while True:
            try:
                self.levels()
            except Exception:  # a tick must never end the loop
                log.exception("aegis: notices tick failed")
            await asyncio.sleep(EVERY_S)

    def _reading(self) -> dict:
        now = self._clock()
        if self._cached is None or now - self._cached[0] >= CACHE_S:
            paths = [self._app.roots.config_root, Path(tempfile.gettempdir())]
            self._cached = (now, self._read(paths))
        return self._cached[1]

    def _signals(self, cfg: dict) -> dict[str, tuple[float, dict]]:
        """Each host kind that is on and read: key -> (value, reading)."""
        r = self._reading()
        out = {}
        for kind in ("disk", "memory", "cpu"):
            levels, reading = cfg.get(kind), r.get(kind)
            if not levels or reading is None:
                continue
            value = reading["pct"]
            full = reading.get("psi_full")
            if kind == "memory" and full is not None and full >= levels["psi_full"]:
                value = max(value, levels["on"])
            out[kind] = (value, reading)
        return out

    def levels(self) -> dict[str, tuple[str, dict]]:
        """Every active kind -> (level, reading). Turns episodes: a level
        only steps up, an ended episode drops its askers, and the first call
        that sees a kind critical in an episode toasts the person."""
        with self._lock:
            cfg = self._app.config.current().notices
            now: dict[str, tuple[str, dict]] = {}
            for kind, (value, reading) in self._signals(cfg).items():
                self._last[kind] = reading  # a clear prints the reading now
                prev = self._active.get(kind, (None, None))[0]
                level = next_level(prev, value, cfg[kind])
                if level is not None:
                    now[kind] = (level, reading)
            if cfg.get("quota") and self._app.quota is not None:
                for key, (level, reading) in _quota(
                    self._app.quota.snapshot()["providers"]
                ).items():
                    if self._active.get(key, (None,))[0] == "critical":
                        level = "critical"  # never steps down within an episode
                    now[key] = (level, reading)
            for key, (_, reading) in now.items():
                self._last[key] = reading
            for key in set(self._active) - set(now):
                self._askers = {k: v for k, v in self._askers.items() if k[0] != key}
                self._toasted.discard(key)
            for key, (level, reading) in now.items():
                if level == "critical" and key not in self._toasted:
                    self._toasted.add(key)
                    kind = key.partition(":")[0]
                    self._app.toast(
                        f"aegis: {kind} is critical: {_summary(kind, reading)}", "error"
                    )
            self._active = now
            return dict(now)

    def _live(self, log_id: str) -> bool:
        s = self._app.sessions.sessions.get(log_id)
        return s is not None and not s.archived

    def role(self, s: Session, kind: str) -> Role:
        """What the text for ``kind`` asks of ``s``: a worker and a spawned
        session never ask the person, and only one person-opened session per
        kind and owner does."""
        if s.worker or s.schedule or self._app.sessions.workflow_lock(s):
            return "worker"
        if s.spec.spawned_by or s.spec.spawned_from:
            return "spawned"
        if kind.partition(":")[0] in NO_ASKER:
            return "person"
        with self._lock:
            asker = self._askers.get((kind, s.spec.owner))
            if asker is None or asker == s.log_id or not self._live(asker):
                return "asker"
            return "person"

    def _own_memory(self, s: Session) -> float | None:
        d = self._app.sessions.cgroup_dir(s)
        if d is None:
            return None
        try:
            return round(int((d / "memory.current").read_text()) / host.GB, 1)
        except (OSError, ValueError):
            return None

    def pending(self, s: Session) -> str:
        """The blocks ``s`` has not been told yet, and the clears it is owed,
        joined by a blank line; "" when there are none. Marks them sent
        before it returns, so each block reaches one result."""
        with self._lock:
            active = {
                k: v
                for k, v in self.levels().items()
                if not k.startswith("quota:") or v[1]["harness"] == s.spec.harness
            }
            blocks = []
            for key, (level, reading) in active.items():
                if s.notices_sent.get(key) == level:
                    continue
                role = self.role(s, key)
                if role == "asker":
                    self._askers[(key, s.spec.owner)] = s.log_id
                if key == "memory":
                    reading = reading | {"rss_gb": self._own_memory(s)}
                blocks.append(block(key, level, reading, role))
                s.notices_sent[key] = level
            for key in [k for k in s.notices_sent if k not in active]:
                blocks.append(block(key, "clear", self._clear_reading(key), "person"))
                del s.notices_sent[key]
            if blocks:
                try:
                    s._metas.write_soon(s.log_id, s.meta)
                except OSError:  # a full disk keeps the in-memory record (#395)
                    log.warning("aegis: could not record notices for %s", s.handle)
            return "\n\n".join(blocks)

    def _clear_reading(self, key: str) -> dict:
        r = dict(self._last.get(key, {}))
        kind, _, rest = key.partition(":")
        if kind == "announce":
            r.setdefault("id", rest)
        if kind == "quota":
            r.setdefault("label", rest)
        if kind in ("disk", "memory") and "pct" not in r:
            r["pct"] = "?"
        return r
