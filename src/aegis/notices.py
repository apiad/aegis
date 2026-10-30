"""Notices: what a session is told about the host's disk, memory, CPU and quota.

A notice never starts a turn and never goes into the system prompt: it rides
on the session's next send or, during a turn, its next tool call
(``Session._steer_owed``) or, on a harness without ``steers_notices``, its next
aegis tool result. Reading a signal
never raises; a missing /proc or PSI file reads as None, and None means no
notice of that kind.

Each session is told a kind once per episode: ``Session.notices_sent`` holds
the level it last got, so a block is pending when the level differs and a
clear is owed when the kind is no longer active. That record is in the meta;
the episodes and their askers live here, in memory, and are re-read from the
signals after a restart. One session the person opened is the asker per kind
and owner, so nine agents do not ask about the same disk.

An announcement is a notice a person or an admin's agent writes
(``notice_ops.py``). It is kept in ``<state>/announcements.json``, since no
signal can re-read it, and is active until it is cleared or its ``until``
passes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from . import host
from .notice_sources import Item
from .monitors import iso_now
from .quota_gate import pool_for
from .transcript.entries import NOTICE_HEADER

if TYPE_CHECKING:
    from .app import App
    from .session import Session

log = logging.getLogger("aegis.notices")

EVERY_S = 30.0  # the loop that turns levels and toasts at critical
CACHE_S = 5.0  # a reading is reused this long

Role = Literal["asker", "person", "spawned", "worker"]

# Per-session notices: what a subsystem says about one session (its plan, its
# monitors, its queue tasks). Warnings ride like the host kinds; info lines
# make one status block that rides on prompts only.
TEXT_MAX = 160
STATUS_KEY = "status"
NOTHING_TRACKED = "nothing tracked: no plan items open, no monitors, no tasks out"
__all__ = ["Item", "Notices"]


def _cut(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= TEXT_MAX else line[: TEXT_MAX - 1] + "…"


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
            best = {
                "pct": d["pct"],
                "free_gb": free,
                "total_gb": d["total_gb"],
                "mount": host.mount_of(p),
            }
    return best


def elsewhere(full: dict, disks: list[dict], clear: int) -> dict | None:
    """Another of the host's disks a session can put new work on while
    ``full`` fills: below the disk's clear level, writable by aegis's user,
    and with more free space than ``full``. The freest such one (#745)."""
    best: dict | None = None
    for d in disks:
        m = d["mount"]
        if m == full.get("mount") or d["pct"] >= clear or not os.access(m, os.W_OK):
            continue
        try:
            st = os.statvfs(m)
        except OSError:
            continue
        free = round(st.f_bavail * st.f_frsize / host.GB, 1)
        if free > full["free_gb"] and (best is None or free > best["free_gb"]):
            best = {"mount": m, "free_gb": free}
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
DISK_OTHER = "Free what this session created and no longer needs (your merged worktrees, build output, temporary files), re-measuring with df before you delete. Do not touch shared caches, another session's work or anything tracked by git. {mount} has {free_gb} GB free: as far as you can, put new large files there (clones, build output, caches, downloads) instead of on this disk, and move nothing another session or git owns."
QUOTA_CRITICAL = " Tell the person before you enqueue anything."
# The session's own share (load.py, #762), and who holds the most. The
# heaviest is named only above a floor: on a quiet host the top session may
# hold a tenth of a core, and telling it to stop would be noise.
OWN = {
    "cpu": " Your session's processes used {own} cores over the last minute",
    "memory": " Your session's processes hold {own} GB",
}
TOP_SELF = ", the most of any session here."
TOP_OTHER = {
    "cpu": "; {top_handle} uses the most, {top} cores.",
    "memory": "; {top_handle} holds the most, {top} GB.",
}
ACT_TOP = {
    "cpu": "Slow your own work first: stop a parallel test run or build you started, or run it with fewer workers. Re-run a timing failure or a make bench number before you trust it, and say the host was loaded when you report one.",
    "memory": "Free your own first: stop the servers, watchers, test runs and builds you started and no longer use. Run no parallel test lane or build, and enqueue no more workers.",
}
FLOOR_CORES = 1.0
FLOOR_RAM_SHARE = 0.1
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
# A kind turned off in the config mid-episode: no reading says it is over.
OFF = "aegis no longer watches {what}."
WATCHED = {
    "disk": "the disk",
    "memory": "memory",
    "cpu": "the CPU",
    "quota": "the {label} quota",
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
    not ``quota:claude``); ``level`` is warn, critical, clear or off, the
    clear of a kind turned off."""
    r = _shown(reading)
    if level == "off":
        return OFF.format(what=WATCHED[kind].format(**r))
    if level == "clear":
        return CLEAR[kind].format(**r)
    if kind == "announce":
        return f"{ANNOUNCED.format(**r)}\n{ONCE}"
    lead = LEAD[kind].format(**r)
    act = ACT[kind]
    if kind in OWN and reading.get("own") is not None:
        lead += OWN[kind].format(**r)
        if reading.get("is_top"):
            lead += TOP_SELF
            act = ACT_TOP[kind]
        elif reading.get("top_handle"):
            lead += TOP_OTHER[kind].format(**r)
        else:
            lead += "."
    if kind == "disk" and reading.get("other"):
        act = DISK_OTHER.format(**_shown(reading["other"]))
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
    shown = "clear" if level == "off" else level
    return f"{NOTICE_HEADER}{key} · {shown} · {iso_now()}\n" + body(
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


def _quota(providers: list[dict]) -> dict[str, tuple[str | None, dict]]:
    """Each provider with a fresh reading: its level (None when every window
    is normal) and its worst window as the reading. A provider that is
    missing or not ``ok`` is left out, so its episode keeps its level."""
    rank = {"warning": 1, "critical": 2}
    out: dict[str, tuple[str | None, dict]] = {}
    for p in providers:
        if p.get("state") != "ok":
            continue
        reading = {"label": p["label"], "harness": p["harness"]}
        ws = [w for w in p.get("windows", ()) if w.get("severity") in rank]
        if not ws:
            out[f"quota:{p['name']}"] = (None, reading)
            continue
        w = max(ws, key=lambda w: (rank[w["severity"]], w.get("percent") or 0))
        reading |= {
            "window": w["label"],
            "percent": w.get("percent") or 0,
            "projected": w.get("projected") or w.get("percent") or 0,
        }
        level = "critical" if w["severity"] == "critical" else "warn"
        out[f"quota:{p['name']}"] = (level, reading)
    return out


class Announcements:
    """The announcements in force, in one JSON file written atomically. A
    file that cannot be read starts empty; a write that fails keeps the
    list in memory, so a full disk never stops a notice."""

    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            self._items: list[dict] = json.loads(path.read_text())
        except (OSError, ValueError):
            self._items = []

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._items, ensure_ascii=False, indent=1))
            os.replace(tmp, self._path)
        except OSError:
            log.warning("aegis: could not save %s", self._path)

    def add(self, text: str, level: str, author: str, until: float | None) -> dict:
        known = {a["id"] for a in self._items}
        id = secrets.token_hex(3)
        while id in known:
            id = secrets.token_hex(3)
        a = {
            "id": id,
            "text": text,
            "level": level,
            "author": author,
            "at": time.time(),
            "until": until,
        }
        self._items.append(a)
        self._save()
        return a

    def clear(self, id: str) -> bool:
        kept = [a for a in self._items if a["id"] != id]
        if len(kept) == len(self._items):
            return False
        self._items = kept
        self._save()
        return True

    def active(self, now: float) -> list[dict]:
        """Those in force at ``now``; an expired one is dropped for good."""
        kept = [a for a in self._items if a["until"] is None or a["until"] > now]
        if len(kept) != len(self._items):
            self._items = kept
            self._save()
        return [dict(a) for a in kept]


RANK = {"warn": 1, "critical": 2}


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
        # Why each ended episode ended, "clear" or "off", and the reading a
        # clear prints: the one below clear, or the last one when turned off.
        self._ended: dict[str, tuple[str, dict]] = {}
        self._askers: dict[tuple[str, str | None], str] = {}
        self._toasted: set[str] = set()
        # The first levels() seeds the episodes from what live sessions hold.
        self._restored = False
        # The memory episode reached psi_full, so it holds at half of it.
        self._psi = False
        self._task: asyncio.Task | None = None
        self.announcements = Announcements(app.roots.state_root / "announcements.json")
        # Per-session items: log id -> "<source>:<key>" -> Item, and each
        # session's ended items' clear texts, owed until told. In memory:
        # every source sets its items again at boot.
        self._items: dict[str, dict[str, Item]] = {}
        self._clears: dict[str, dict[str, str]] = {}
        self._order: list[str] = []  # sources, in the order they first set
        # While the server boots, no source has set its items yet: a prompt
        # then (a restart's continue) must neither forget what a session was
        # told nor say it tracks nothing. App.boot releases it.
        self._booting = False

    def boot_begin(self) -> None:
        self._booting = True

    def boot_end(self) -> None:
        """Every source has set its items: deliver the wakes owed."""
        self._booting = False
        for log_id in list(self._items):
            self._wake(log_id)

    def cfg(self, source: str) -> dict | None:
        """A source's entry under ``notices:``, or None when it is off."""
        got = self._app.config.current().notices.get(source)
        if got is False:
            return None
        return got if isinstance(got, dict) else {}

    def set(self, log_id: str, source: str, items: list[Item]) -> None:
        """Replace every item ``source`` had for ``log_id``; [] clears them.
        A source turned off in .aegis.yaml sets nothing."""
        if source not in self._order:
            self._order.append(source)
        if self.cfg(source) is None:
            items = []
        mine = self._items.setdefault(log_id, {})
        prefix = f"{source}:"
        new = {
            prefix + i.key: Item(i.key, i.level, _cut(i.text), _cut(i.clear), i.wake)
            for i in items
        }
        s = self._app.sessions.sessions.get(log_id)
        sent = s.items_sent if s is not None else {}
        for full in [k for k in mine if k.startswith(prefix) and k not in new]:
            gone = mine.pop(full)
            # Told ends here, so the same condition turning on again is told
            # (and wakes) again; the clear is owed only to a session told.
            if sent.pop(full, None) in RANK and gone.clear:
                self._clears.setdefault(log_id, {})[full] = gone.clear
        for full, item in new.items():
            self._clears.get(log_id, {}).pop(full, None)
            mine[full] = item
        self._wake(log_id, source)

    def _wake(self, log_id: str, source: str | None = None) -> None:
        """Deliver at once, through the inbox, each warning with ``wake`` the
        session has not been told, when it is idle or asleep: a turn running
        carries it on its next tool result or prompt, and its end calls this
        again. Recorded as told before the delivery, so it wakes once per
        level. A clear never wakes."""
        s = self._app.sessions.sessions.get(log_id)
        if self._booting or s is None or s.archived or s.in_turn:
            return
        if s.status == "working":
            return
        due = []
        for full, item in self._items.get(log_id, {}).items():
            src = full.partition(":")[0]
            if source is not None and src != source:
                continue
            if not item.wake or item.level not in RANK:
                continue
            if s.items_sent.get(full) == item.level:
                continue
            if (self.cfg(src) or {}).get("wake") is False:
                continue
            s.items_sent[full] = item.level
            due.append(
                f"{NOTICE_HEADER}{full} · {item.level} · {iso_now()}\n{item.text}"
            )
        if not due:
            return
        try:
            s._metas.write_soon(s.log_id, s.meta)
        except OSError:  # a full disk keeps the in-memory record (#395)
            log.warning("aegis: could not record notices for %s", s.handle)
        # One message for all of them: two deliveries to an asleep session
        # would both start it, and the second would land mid-turn.
        header, _, first = due[0].partition("\n")
        body = "\n\n".join([first, *due[1:]])
        asyncio.get_running_loop().create_task(self._deliver(s, header, body))

    @staticmethod
    async def _deliver(s: Session, header: str, text: str) -> None:
        try:
            # Like a monitor's own outcome, it reaches a workflow node too.
            await s.deliver(header, text, through_lock=True)
        except Exception:
            log.exception("aegis: could not deliver a notice to %s", s.handle)

    def turn_ended(self, s: Session) -> None:
        """A turn ended: a wake item it was not told comes now."""
        self._wake(s.log_id)

    def drop_source(self, source: str) -> None:
        """A source went away (a plugin's connection closed): its items go,
        for every session."""
        for log_id in list(self._items):
            self.set(log_id, source, [])

    def forget(self, log_id: str) -> None:
        """The session's context was compacted: nothing it was told by a
        source counts any more, so its next prompt tells it all again."""
        s = self._app.sessions.sessions.get(log_id)
        if s is not None:
            s.items_sent.clear()
            self._clears.pop(log_id, None)

    def _session_blocks(self, s: Session, at: str) -> list[str]:
        """The per-session warnings ``s`` is owed, their clears, and (on a
        prompt) the status block when its lines changed. Records what it
        returns in ``s.items_sent``."""
        if self._booting:
            return []
        items = self._items.get(s.log_id, {})
        sent = s.items_sent
        blocks = []
        for full, item in items.items():
            if item.level in RANK and sent.get(full) != item.level:
                blocks.append(
                    f"{NOTICE_HEADER}{full} · {item.level} · {iso_now()}\n{item.text}"
                )
                sent[full] = item.level
        for full, text in self._clears.pop(s.log_id, {}).items():
            blocks.append(f"{NOTICE_HEADER}{full} · clear · {iso_now()}\n{text}")
        for full in [k for k in sent if k != STATUS_KEY and k not in items]:
            del sent[full]  # kept from before a restart, its source gone
        if at != "send":
            return blocks
        status = self.cfg(STATUS_KEY)
        # Sources in the order they first set items; sorted() keeps each
        # source's own order.
        rank = {src: n for n, src in enumerate(self._order)}
        info = [
            i
            for full, i in sorted(
                items.items(), key=lambda kv: rank[kv[0].partition(":")[0]]
            )
            if i.level == "info"
        ]
        if status is None or not info:
            if STATUS_KEY in sent:
                del sent[STATUS_KEY]
                if status is not None:
                    blocks.append(
                        f"{NOTICE_HEADER}{STATUS_KEY} · clear · {iso_now()}\n{NOTHING_TRACKED}"
                    )
            return blocks
        cap = status.get("lines", 5)
        lines = [i.text for i in info[:cap]]
        if len(info) > cap:
            lines.append(f"… and {len(info) - cap} more")
        mark = hashlib.sha1("\n".join(lines).encode()).hexdigest()[:12]
        if sent.get(STATUS_KEY) != mark:
            blocks.append(
                f"{NOTICE_HEADER}{STATUS_KEY} · info · {iso_now()}\n" + "\n".join(lines)
            )
            sent[STATUS_KEY] = mark
        return blocks

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
            r = self._read(paths)
            d = r.get("disk")
            cfg = self._app.config.current().notices.get("disk")
            if d and d.get("mount") and cfg:
                other = elsewhere(d, self._app.host.disks(), cfg["clear"])
                r = r | {"disk": d | {"other": other}}
            self._cached = (now, r)
        return self._cached[1]

    def _restore(self) -> None:
        """After a restart, each key's episode starts from the highest level a
        live session holds, so a reading between clear and on neither clears
        it nor repeats it, and a critical one is not toasted again."""
        for s in self._app.sessions.sessions.values():
            if s.archived:
                continue
            for key, level in s.notices_sent.items():
                held = self._active.get(key, (None,))[0]
                if level in RANK and RANK[level] > RANK.get(held or "", 0):
                    self._active[key] = (level, {})
        self._toasted |= {k for k, (lv, _) in self._active.items() if lv == "critical"}

    def _value(self, kind: str, reading: dict, cfg: dict, on: bool) -> float:
        value = reading["pct"]
        full = reading.get("psi_full")
        if kind != "memory" or full is None:
            return value
        if full >= cfg["psi_full"]:
            self._psi = True
            return max(value, cfg["on"])
        if on and self._psi and full >= cfg["psi_full"] / 2:
            return max(value, cfg["clear"])  # held, not escalated
        return value

    def levels(self) -> dict[str, tuple[str, dict]]:
        """Every active kind -> (level, reading). Turns episodes: a level
        only steps up, a failed or stale reading keeps it, an ended episode
        drops its askers, and the first call that sees a kind critical in an
        episode toasts the person."""
        if not self._restored:
            self._restored = True
            self._restore()
        cfg = self._app.config.current().notices
        now: dict[str, tuple[str, dict]] = {}
        ended: dict[str, dict] = {}  # ended below clear -> that reading
        r = self._reading()
        for kind in ("disk", "memory", "cpu"):
            prev = self._active.get(kind)
            if not cfg.get(kind):
                continue
            reading = r.get(kind)
            if reading is None:
                if prev is not None:
                    now[kind] = prev  # a failed read keeps the episode
                continue
            value = self._value(kind, reading, cfg[kind], prev is not None)
            level = next_level(prev and prev[0], value, cfg[kind])
            if level is not None:
                now[kind] = (level, reading)
            elif prev is not None:
                ended[kind] = reading
        if cfg.get("quota"):
            fresh = (
                {}
                if self._app.quota is None
                else _quota(self._app.quota.snapshot()["providers"])
            )
            for key, prev in self._active.items():
                if key.startswith("quota:") and key not in fresh:
                    now[key] = prev  # missing or not ok: keeps its level
            for key, (level, reading) in fresh.items():
                prev = self._active.get(key, (None, None))[0]
                if level is None:
                    if prev is not None:
                        ended[key] = reading
                    continue
                if prev == "critical":
                    level = "critical"  # never steps down within an episode
                now[key] = (level, reading)
        for a in self.announcements.active(self._clock()):
            now[f"announce:{a['id']}"] = (a["level"], a)
        if "memory" not in now:
            self._psi = False
        for key in set(self._active) - set(now):
            self._askers = {k: v for k, v in self._askers.items() if k[0] != key}
            self._toasted.discard(key)
            if key in ended:
                self._ended[key] = ("clear", ended[key])
            elif not key.startswith("announce:"):
                self._ended[key] = ("off", self._active[key][1])
        for key in now:
            self._ended.pop(key, None)
        for key, (level, reading) in now.items():
            if level == "critical" and key not in self._toasted:
                self._toasted.add(key)
                kind = key.partition(":")[0]
                self._app.toast(
                    f"aegis: {kind} is critical: {_summary(kind, reading)}", "error"
                )
        self._active = now
        self._hand_over()
        return dict(now)

    def _hand_over(self) -> None:
        """An asker that closed mid-episode passes the role to a live
        person-opened session of its owner that was already told: it forgets
        the key, so its next pending() tells it again, as the asker."""
        sessions = self._app.sessions.sessions
        for (key, owner), log_id in list(self._askers.items()):
            if self._live(log_id):
                continue
            del self._askers[(key, owner)]
            heir = next(
                (
                    s
                    for s in sessions.values()
                    if not s.archived
                    and s.spec.owner == owner
                    and key in s.notices_sent
                    and self._opener(s) is None
                ),
                None,
            )
            if heir is not None:
                del heir.notices_sent[key]
                self._askers[(key, owner)] = heir.log_id

    def _live(self, log_id: str) -> bool:
        s = self._app.sessions.sessions.get(log_id)
        return s is not None and not s.archived

    def _opener(self, s: Session) -> Role | None:
        """worker or spawned for a session no person opened, else None."""
        if s.worker or s.schedule or self._app.sessions.workflow_lock(s):
            return "worker"
        if s.spec.spawned_by or s.spec.spawned_from:
            return "spawned"
        return None

    def role(self, s: Session, kind: str) -> Role:
        """What the text for ``kind`` asks of ``s``: a worker and a spawned
        session never ask the person, and only one person-opened session per
        kind and owner does."""
        if opener := self._opener(s):
            return opener
        if kind.partition(":")[0] in NO_ASKER:
            return "person"
        asker = self._askers.get((kind, s.spec.owner))
        if asker is None or asker == s.log_id or not self._live(asker):
            return "asker"
        return "person"

    def _own(self, s: Session, kind: str, reading: dict) -> dict:
        """For a cpu or memory notice: the session's own figure as text, and
        whether it or another session holds the most, above the floor."""
        load = getattr(self._app, "load", None)
        if load is None or (mine := load.of(s.log_id)) is None:
            return {}
        field = "cpu" if kind == "cpu" else "rss_gb"
        floor = (
            FLOOR_CORES
            if kind == "cpu"
            else FLOOR_RAM_SHARE * (reading.get("total_gb") or 0)
        )
        loads = load.all()
        out: dict = {"own": f"{mine[field]:.1f}"}
        top_id = max(loads, key=lambda k: loads[k][field])
        top = loads[top_id][field]
        if top < floor or top <= 0:
            return out
        if top_id == s.log_id or top <= mine[field]:
            return out | {"is_top": True}
        other = self._app.sessions.sessions.get(top_id)
        return out | {
            "top_handle": other.handle if other is not None else top_id,
            "top": f"{top:.1f}",
        }

    def pending(self, s: Session, at: Literal["send", "tool"] = "send") -> str:
        """The blocks ``s`` has not been told yet, and the clears it is owed,
        joined by a blank line; "" when there are none. Marks them sent
        before it returns, so each block reaches one result.

        It runs only on the event loop, as levels() does, and never awaits:
        two calls cannot interleave, so it needs no lock. Do not call it from
        a thread."""
        pool = pool_for(s.spec.harness, s.spec.model)
        active = {
            k: v
            for k, v in self.levels().items()
            if not k.startswith("quota:") or k.partition(":")[2] == pool
        }
        blocks = []
        for key, (level, reading) in active.items():
            # A level kept from before a restart has no reading to print yet.
            if s.notices_sent.get(key) == level or not reading:
                continue
            role = self.role(s, key)
            if role == "asker":
                self._askers[(key, s.spec.owner)] = s.log_id
            if key in OWN:
                reading = reading | self._own(s, key, reading)
            blocks.append(block(key, level, reading, role))
            s.notices_sent[key] = level
        for key in [k for k in s.notices_sent if k not in active]:
            blocks.append(self._clear(key))
            del s.notices_sent[key]
        before = dict(s.items_sent)
        blocks += self._session_blocks(s, at)
        if blocks or s.items_sent != before:
            try:
                s._metas.write_soon(s.log_id, s.meta)
            except OSError:  # a full disk keeps the in-memory record (#395)
                log.warning("aegis: could not record notices for %s", s.handle)
        return "\n\n".join(blocks)

    def _clear(self, key: str) -> str:
        """The clear for ``key``: back to its reading below clear, or that
        aegis no longer watches it when it was turned off or never read."""
        why, reading = self._ended.get(key, ("off", {}))
        kind, _, rest = key.partition(":")
        r = dict(reading)
        if kind == "announce":
            r.setdefault("id", rest)
            why = "clear"
        if kind == "quota":
            r.setdefault("label", rest)
        return block(key, "clear" if why == "clear" else "off", r, "person")
