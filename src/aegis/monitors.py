"""Monitors: bash conditions polled for an agent until one decides it.

An agent arms a monitor and ends its turn; that is how it waits (the primer
says so). ``done`` exiting 0 completes it, ``fail`` exiting 0 fails it, the
timeout ends it, and ``progress`` prints 0 to 100 for the bar on its card. The
owner is woken through its inbox with ``> from monitor:<id> · <outcome> · …``,
and the wake lists the owner's other live monitors so it can cancel stale ones.

The card carries what a person needs to judge a monitor without reading its
JSON: the commands, the readings, an ETA, and the last result of each check
with its stderr line. A missing command exits 127 on every poll and otherwise
looks exactly like a condition still waiting (#174). The card is republished
only when a reading or a check's verdict changes, never on a poll that changed
nothing, so a 10-second monitor does not patch every browser every 10 seconds;
that is why a check reports ``since`` (when its result began) and not when it
last ran.

A monitor can watch sessions instead of running bash (``monitor_sessions``):
it reads each one's attention card every interval and ends ``ok`` when all
have finished, or ``blocked`` as soon as one needs the person or failed, so a
waiting agent can say what holds it up instead of sitting out the timeout.
Sessions are kept by log id, so a rename does not lose one.

Monitors are kept in ``<state>/monitors.json`` and re-armed at boot; a wake to
a stopped owner resumes it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import signal
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from . import launch, notice_sources
from .notice_sources import Item

if TYPE_CHECKING:
    from .registry import Registry

log = logging.getLogger("aegis.monitors")
CONDITION_LIMIT_S = 30.0
READINGS_KEPT = 100
# What a watched session's attention means to a monitor waiting on it. A
# closed session is finished: its work is over, whatever its last turn said.
FINISHED = frozenset({"done", "review", "closed"})
BLOCKED = frozenset({"needs_you", "error"})
# A watched session idle after a turn with no turn_end is still running for
# this long: ending a turn to wait on a queue task leaves it silent for the few
# seconds a worker takes to close and deliver. Past it the silence is not
# passing (a stop, an interrupt, an agent that skips turn_end), and the waiter
# is told rather than released early or left waiting until the timeout.
SILENT_GRACE_S = 120.0


def classify(attention: str) -> str:
    """finished, blocked or running, for a monitor waiting on a session."""
    if attention in FINISHED:
        return "finished"
    if attention in BLOCKED:
        return "blocked"
    return "running"


async def run_bash(
    cmd: str, cwd: str, timeout: float = CONDITION_LIMIT_S, *, user: str | None
) -> tuple[int | None, str, str]:
    """Run ``cmd`` under bash in ``cwd``, as ``user`` (launch.py): its exit
    code, stdout and stderr. The code is None when bash could not start or the
    command took over ``timeout`` seconds, and stderr then says which. A
    monitor's conditions and a schedule's check both run through here."""
    try:
        proc = await launch.child(
            ["bash", "-c", cmd],
            cwd=cwd,
            stdout=launch.PIPE,
            stderr=launch.PIPE,
            user=user,
            via=launch.resolve_via(),
        )
    except OSError as e:
        return None, "", str(e)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        await launch.signal_process_async(proc.pid, signal.SIGKILL, user=user)
        await proc.wait()
        return None, "", f"took over {timeout:g}s"
    return (
        proc.returncode,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _elapsed(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


@dataclass
class Monitor:
    id: str
    owner: str  # log id
    description: str
    done: str
    cwd: str
    fail: str | None = None
    progress: str | None = None
    interval_s: float = 10.0
    timeout_s: float = 3600.0
    started_at: float = field(default_factory=time.time)
    last_progress: int | None = None
    readings: list[list[float]] = field(default_factory=list)  # [time, percent]
    checks: dict[str, dict] = field(default_factory=dict)  # kind -> last result
    # The tool row this monitor waits on: its entry id, title and summary.
    source: dict | None = None
    # Watched sessions, for a monitor that waits on sessions instead of bash.
    sessions: list[dict] = field(default_factory=list)

    def read(self, t: float, value: int) -> None:
        """Keep a reading when it differs from the last. Trimming drops the
        oldest after the first two, which the ETA's rate is anchored on."""
        if self.readings and self.readings[-1][1] == value:
            return
        self.readings.append([t, value])
        if len(self.readings) > READINGS_KEPT:
            del self.readings[2]

    def record(self, kind: str, rc: int | None, out: str, err: str) -> bool:
        """Keep a check's result; True when its verdict changed."""
        said, bad = verdict(kind, rc, out, err)
        prev = self.checks.get(kind)
        changed = prev is None or (prev["rc"], prev["verdict"]) != (rc, said)
        since = time.time() if prev is None or changed else prev["since"]
        self.checks[kind] = {
            "rc": rc,
            "verdict": said,
            "bad": bad,
            "out": _last_line(out),
            "err": _last_line(err),
            "since": since,
        }
        return changed

    def card(self) -> dict:
        # Sessions of different sizes give finished-over-listed no rate.
        due = None if self.sessions else eta(self.started_at, self.readings)
        checks = []
        if not self.sessions:
            for kind in ("done", "progress", "fail"):
                cmd = getattr(self, kind)
                checks.append(
                    {"kind": kind, "cmd": cmd, **(self.checks.get(kind) or {})}
                )
        return {
            "id": self.id,
            "description": self.description,
            "progress": self.last_progress,
            "source": self.source,
            "cwd": self.cwd,
            "started_at": self.started_at,
            "interval_s": self.interval_s,
            "timeout_s": self.timeout_s,
            "readings": self.readings,
            "eta_at": due[0] if due else None,
            "eta_basis": due[1] if due else None,
            "checks": checks,
            "sessions": [
                {k: r.get(k, "") for k in ("handle", "attention", "state", "line")}
                for r in self.sessions
            ]
            or None,
            "broken": any(c.get("bad") for c in checks),
        }


def find_source(entries: list[dict], text: str) -> dict | None:
    """The latest tool row whose call holds ``text``. An agent never sees a
    row's id, so it names the row by what it wrote there. Its own monitor_start
    is skipped: that row is in the fold already and holds the text too (#870)."""
    for e in reversed(entries):
        if e["kind"] != "tool" or e["title"] in ("monitor_start", "monitor_sessions"):
            continue
        if text in f"{e['summary']}\n{e['detail'].get('args', '')}":
            return {"id": e["id"], "title": e["title"], "summary": e["summary"]}
    return None


# The per-session notices a monitor's owner gets (notices.py): its live
# monitors as a status line, and a warning that wakes it when one has stalled,
# cannot run its check, or has overrun its ETA.
SOURCE = "monitors"
notice_sources.declare(
    SOURCE, {"stall_min": 15.0, "broken_checks": 3, "overdue_min": 10.0, "wake": True}
)


def monitor_items(
    ms: list[Monitor], cfg: dict, now: float, checked: set[str]
) -> list[Item]:
    """The owner's items for its live monitors ``ms``, under ``cfg`` (the
    ``notices.monitors`` block): a status line each, and warnings only for
    those in ``checked``, checked at least once by this process, so a
    restart's downtime never reads as a stall."""
    live = []
    warns = []
    for m in ms:
        row = f"{m.id} {m.description}"
        if m.last_progress is not None:
            row += f" {m.last_progress}%"
        due = None if m.sessions else eta(m.started_at, m.readings)
        if due and due[0] > now:
            row += f" eta {round((due[0] - now) / 60)} min"
        live.append(Item(f"{m.id}:live", "info", f"monitor {row}"))
        if m.sessions or m.id not in checked:
            continue  # a session watcher's own blocked outcome wakes the owner
        name = f'monitor {m.id} "{m.description}"'
        warn = {"level": "warn", "clear": f"monitor {m.id} moves again, or has ended",
                "wake": cfg["wake"]}  # fmt: skip
        if m.progress:
            last = m.readings[-1][0] if m.readings else m.started_at
            if now - last >= max(cfg["stall_min"] * 60, 3 * m.interval_s):
                at = f"at {m.last_progress}% " if m.last_progress is not None else ""
                text = f"{name} {at}for {round((now - last) / 60)} min · look, or monitor_cancel it"
                warns.append(Item(f"{m.id}:stalled", text=text, **warn))
        for kind in ("done", "progress", "fail"):
            c = m.checks.get(kind)
            if (
                c
                and c["bad"]
                and now - c["since"] >= cfg["broken_checks"] * m.interval_s
            ):
                text = f"{name} {kind} check cannot run: {c['verdict']}"
                warns.append(Item(f"{m.id}:broken", text=text, **warn))
                break
        if due and due[1] != "finished":
            late = now - due[0]
            if late >= max((due[0] - m.started_at) / 2, cfg["overdue_min"] * 60):
                at = f" at {m.last_progress}%" if m.last_progress is not None else ""
                text = f"{name} past its eta by {round(late / 60)} min{at}"
                warns.append(Item(f"{m.id}:overdue", text=text, **warn))
    return [*live, *warns]


class Monitors:
    def __init__(self, registry: Registry, path: Path) -> None:
        self._registry = registry
        # A server linked with trust's session list, or None when it cannot
        # be read (app.py sets it), for a monitor watching sessions there.
        self.far_list: Callable[[str], Awaitable[list | None]] | None = None
        self._path = path
        self.items: dict[str, Monitor] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._checked: set[str] = set()  # checked at least once by this process

    # -- persistence ---------------------------------------------------------
    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps([asdict(m) for m in self.items.values()], indent=1))
        os.replace(tmp, self._path)

    def boot(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, ValueError):
            log.warning("aegis: %s is damaged; starting with no monitors", self._path)
            return
        for d in raw if isinstance(raw, list) else []:
            try:
                m = Monitor(**d)
            except TypeError:
                continue
            if m.owner in self._registry.sessions:
                self.items[m.id] = m
        self._save()

    def arm_all(self) -> None:
        for m in self.items.values():
            self._arm(m)
        for owner in {m.owner for m in self.items.values()}:
            self._status(owner)

    # -- operations ----------------------------------------------------------
    def start(self, owner: str, cwd: str, **kw) -> Monitor:
        m = Monitor(id=f"mon-{secrets.token_hex(4)}", owner=owner, cwd=cwd, **kw)
        self.items[m.id] = m
        self._save()
        self._arm(m)
        self._registry.refresh_card(owner)
        self._status(owner)
        return m

    def cancel(self, monitor_id: str) -> Monitor | None:
        m = self.items.pop(monitor_id, None)
        if m is None:
            return None
        self._checked.discard(monitor_id)
        t = self._tasks.pop(monitor_id, None)
        if t:
            t.cancel()
        self._save()
        self._registry.refresh_card(m.owner)
        self._status(m.owner)
        return m

    def of(self, owner: str) -> list[Monitor]:
        return [m for m in self.items.values() if m.owner == owner]

    def card(self, owner: str) -> list[dict]:
        return [m.card() for m in self.of(owner)]

    def _status(self, owner: str) -> None:
        """Set the owner's monitor notices from its live monitors now."""
        notices = self._registry.notices
        if notices is None:
            return
        cfg = notices.cfg(SOURCE)
        items = (
            []
            if cfg is None
            else monitor_items(self.of(owner), cfg, time.time(), self._checked)
        )
        notices.set(owner, SOURCE, items)

    def drop_owner(self, owner: str) -> None:
        for m in self.of(owner):
            self.cancel(m.id)

    async def shutdown(self) -> None:
        for t in self._tasks.values():
            t.cancel()
        self._tasks.clear()

    # -- watching ------------------------------------------------------------
    def _arm(self, m: Monitor) -> None:
        self._tasks[m.id] = asyncio.get_running_loop().create_task(self._watch(m))

    async def _run(
        self, cmd: str, cwd: str, user: str | None = None
    ) -> tuple[int | None, str, str]:
        return await run_bash(cmd, cwd, user=user)

    async def _check(self, m: Monitor, kind: str) -> tuple[int | None, str, bool]:
        # As the owner of the session that armed it (users.py); in multi-user
        # mode never as the server for a session that has no user or is gone.
        s = self._registry.sessions.get(m.owner)
        try:
            if s is None:
                if self._registry.multi_user:
                    raise PermissionError("the session that armed it is gone")
                user = None
            else:
                user = self._registry.user_for(s.spec)
        except PermissionError as e:
            rc, out, err = None, "", str(e)
        else:
            rc, out, err = await self._run(getattr(m, kind), m.cwd, user)
        return rc, out, m.record(kind, rc, out, err)

    def _publish(self, m: Monitor) -> None:
        self._save()
        self._registry.refresh_card(m.owner)

    async def _watch(self, m: Monitor) -> None:
        try:
            if m.sessions:
                await self._watch_sessions(m)
                return
            if not Path(m.cwd).is_dir():
                await self._end(m, "fail", f"its cwd {m.cwd} does not exist")
                return
            while True:
                if time.time() - m.started_at > m.timeout_s:
                    await self._end(
                        m,
                        "timeout",
                        f"it ran out of time after {_elapsed(m.timeout_s)}",
                    )
                    return
                rc, _, changed = await self._check(m, "done")
                if changed:
                    self._publish(m)
                if rc == 0:
                    await self._end(m, "ok", "its done condition passed")
                    return
                if m.fail:
                    rc, _, changed = await self._check(m, "fail")
                    if changed:
                        self._publish(m)
                    if rc == 0:
                        await self._end(m, "fail", "its fail condition passed")
                        return
                if m.progress:
                    rc, out, changed = await self._check(m, "progress")
                    value = _percent(out) if rc == 0 else None
                    if value is not None and value != m.last_progress:
                        m.last_progress = value
                        m.read(time.time(), value)
                        changed = True
                    if changed:
                        self._publish(m)
                self._checked.add(m.id)
                self._status(m.owner)
                await asyncio.sleep(m.interval_s)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # a monitor bug must still wake its owner
            log.exception("aegis: monitor %s crashed", m.id)
            await self._end(
                m, "fail", f"the monitor itself failed: {type(e).__name__}: {e}"
            )

    async def _read_sessions(self, m: Monitor) -> list[dict]:
        rows, now = [], time.time()
        lists: dict[str, list | None] = {}
        for r in m.sessions:
            if r.get("server"):
                server = r["server"]
                if server not in lists:
                    lists[server] = (
                        await self.far_list(server) if self.far_list else None
                    )
                rows.append(self._far_row(r, lists[server], now))
                continue
            s = self._registry.sessions.get(r["log_id"])
            if s is None:
                attention, handle, line = "closed", r["handle"], ""
            else:
                c = self._registry.card(s)
                attention, handle = c["attention"], s.handle
                line = c["attention_line"]
                # The card reads a turn that said nothing as done. A turn ended
                # to wait on a queue task, or interrupted by the person, says
                # nothing too, and releasing the waiter then would be early:
                # only an explicit turn_end finishes a watched session.
                if attention == "done" and not s.standing.get("report"):
                    attention = "idle"
            row = {
                "log_id": r["log_id"],
                "handle": handle,
                "attention": attention,
                "state": classify(attention),
                "line": line,
            }
            if attention == "idle":
                row["silent_since"] = r.get("silent_since") or now
                if now - row["silent_since"] >= SILENT_GRACE_S:
                    row["state"] = "blocked"
            rows.append(row)
        return rows

    @staticmethod
    def _far_row(r: dict, listed: list | None, now: float) -> dict:
        """A watched session on a server linked with trust, read from that
        server's session list. A list that could not be read keeps the last
        reading, so a link that drops never releases the waiter."""
        if listed is None:
            return r
        e = next(
            (x for x in listed if isinstance(x, dict) and x.get("handle") == r["far"]),
            None,
        )
        if e is None:
            attention, line = "closed", ""
        else:
            attention = str(e.get("attention") or "idle")
            line = str(e.get("attention_line") or "")
            if attention == "done" and not e.get("reported"):
                attention = "idle"
        row = {
            "log_id": r["log_id"],
            "handle": r["handle"],
            "server": r["server"],
            "far": r["far"],
            "attention": attention,
            "state": classify(attention),
            "line": line,
        }
        if attention == "idle":
            row["silent_since"] = r.get("silent_since") or now
            if now - row["silent_since"] >= SILENT_GRACE_S:
                row["state"] = "blocked"
        return row

    async def _watch_sessions(self, m: Monitor) -> None:
        while True:
            if time.time() - m.started_at > m.timeout_s:
                await self._end(
                    m, "timeout", f"it ran out of time after {_elapsed(m.timeout_s)}"
                )
                return
            rows = await self._read_sessions(m)
            if rows != m.sessions:
                m.sessions = rows
                done = sum(1 for r in rows if r["state"] == "finished")
                m.last_progress = round(100 * done / len(rows))
                m.read(time.time(), m.last_progress)
                self._publish(m)
                self._status(m.owner)
            blocked = [r for r in rows if r["state"] == "blocked"]
            if blocked:
                await self._end(m, "blocked", "; ".join(map(_said, blocked)))
                return
            if all(r["state"] == "finished" for r in rows):
                ended = ", ".join(f"{r['handle']} {r['attention']}" for r in rows)
                await self._end(m, "ok", f"every session finished: {ended}")
                return
            await asyncio.sleep(m.interval_s)

    async def _end(self, m: Monitor, outcome: str, why: str) -> None:
        self.items.pop(m.id, None)
        self._checked.discard(m.id)
        self._tasks.pop(m.id, None)
        self._save()
        self._registry.refresh_card(m.owner)
        self._status(m.owner)
        others = self.of(m.owner)
        lines = [
            f"{m.description}: {outcome}, {why}, after {_elapsed(time.time() - m.started_at)}."
        ]
        if others:
            lines.append(
                "Your other live monitors: "
                + "; ".join(f"{o.id} ({o.description})" for o in others)
                + "."
            )
        else:
            lines.append("You have no other live monitors.")
        session = self._registry.sessions.get(m.owner)
        if session is None:
            return
        try:
            # The session's own monitor: it reaches a workflow node too.
            await session.deliver(
                f"> from monitor:{m.id} · {outcome} · {iso_now()}",
                "\n".join(lines),
                through_lock=True,
            )
        except Exception:
            log.exception("aegis: could not deliver the wake of %s", m.id)


def eta(started_at: float, readings: list[list[float]]) -> tuple[float, str] | None:
    """When progress reaches 100 at its current rate, and what the rate is.

    The rate runs from the first reading that moved off the starting value to
    the latest. A CI wait sits at 0 while runners set up, and counting that wait
    made the TUI's straight line from the start guess long. With a single move
    there is nothing else to go on, so that line is the fallback."""
    if not readings:
        return None
    t_last, p_last = readings[-1]
    base = readings[0][1]
    moved = next((r for r in readings if r[1] != base), None)
    if moved is None:
        return None
    if p_last >= 100:
        return t_last, "finished"
    if moved is readings[-1]:
        t0, p0, since = started_at, base, "since the start"
    else:
        (t0, p0), since = moved, "since progress first moved"
    gained, took = p_last - p0, t_last - t0
    if gained <= 0 or took <= 0:
        return None
    return (
        t_last + (100 - p_last) * took / gained,
        f"{gained:g} points in {_elapsed(took)}, {since}",
    )


def verdict(kind: str, rc: int | None, out: str, err: str) -> tuple[str, bool]:
    """A check's result in words, and whether it means the check cannot run.

    Only a command bash cannot find or execute, or one that cannot finish, is
    broken: any other exit is a condition answering, and a progress command
    reading a file that does not exist yet is how many monitors start. Bash's
    own "command not found" counts whatever the exit code, since in a pipeline
    (`gh ... | jq`) the code is the last command's."""
    if rc is None:
        return err, True
    if rc == 127 or (rc != 0 and "command not found" in err):
        return "command not found", True
    if rc == 126:
        return "cannot execute", True
    if kind == "progress":
        if rc != 0:
            return f"exit {rc}, no reading", False
        value = _percent(out)
        return (
            ("printed no number", False)
            if value is None
            else (f"printed {value}", False)
        )
    if kind == "done":
        return ("passed" if rc == 0 else "not yet"), False
    return ("failed" if rc == 0 else "not failing"), False


def _said(row: dict) -> str:
    """A blocked session in a wake: who, why, and its own line if it gave one."""
    why = {
        "needs_you": "needs you",
        "idle": "went idle without saying it finished",
    }.get(row["attention"], "hit an error")
    return f"{row['handle']} {why}" + (f": {row['line']}" if row["line"] else "")


def _last_line(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1][:300] if lines else ""


def _percent(out: str) -> int | None:
    try:
        v = float(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return max(0, min(100, int(v)))
