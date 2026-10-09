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
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import Registry

log = logging.getLogger("aegis.monitors")
CONDITION_LIMIT_S = 30.0
READINGS_KEPT = 100
# What a watched session's attention means to a monitor waiting on it. A
# closed session is finished: its work is over, whatever its last turn said.
FINISHED = frozenset({"done", "review", "closed"})
BLOCKED = frozenset({"needs_you", "error"})


def classify(attention: str) -> str:
    """finished, blocked or running, for a monitor waiting on a session."""
    if attention in FINISHED:
        return "finished"
    if attention in BLOCKED:
        return "blocked"
    return "running"


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
        due = eta(self.started_at, self.readings)
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


class Monitors:
    def __init__(self, registry: Registry, path: Path) -> None:
        self._registry = registry
        self._path = path
        self.items: dict[str, Monitor] = {}
        self._tasks: dict[str, asyncio.Task] = {}

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

    # -- operations ----------------------------------------------------------
    def start(self, owner: str, cwd: str, **kw) -> Monitor:
        m = Monitor(id=f"mon-{secrets.token_hex(4)}", owner=owner, cwd=cwd, **kw)
        self.items[m.id] = m
        self._save()
        self._arm(m)
        self._registry.refresh_card(owner)
        return m

    def cancel(self, monitor_id: str) -> Monitor | None:
        m = self.items.pop(monitor_id, None)
        if m is None:
            return None
        t = self._tasks.pop(monitor_id, None)
        if t:
            t.cancel()
        self._save()
        self._registry.refresh_card(m.owner)
        return m

    def of(self, owner: str) -> list[Monitor]:
        return [m for m in self.items.values() if m.owner == owner]

    def card(self, owner: str) -> list[dict]:
        return [m.card() for m in self.of(owner)]

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

    async def _run(self, cmd: str, cwd: str) -> tuple[int | None, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash",
                "-c",
                cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            return None, "", str(e)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), CONDITION_LIMIT_S)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return None, "", f"took over {CONDITION_LIMIT_S:g}s"
        return (
            proc.returncode,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
        )

    async def _check(self, m: Monitor, kind: str) -> tuple[int | None, str, bool]:
        rc, out, err = await self._run(getattr(m, kind), m.cwd)
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
                await asyncio.sleep(m.interval_s)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # a monitor bug must still wake its owner
            log.exception("aegis: monitor %s crashed", m.id)
            await self._end(
                m, "fail", f"the monitor itself failed: {type(e).__name__}: {e}"
            )

    def _read_sessions(self, m: Monitor) -> list[dict]:
        rows = []
        for r in m.sessions:
            s = self._registry.sessions.get(r["log_id"])
            if s is None:
                attention, handle, line = "closed", r["handle"], ""
            else:
                c = self._registry.card(s)
                attention, handle = c["attention"], s.handle
                line = c["attention_line"]
            rows.append(
                {
                    "log_id": r["log_id"],
                    "handle": handle,
                    "attention": attention,
                    "state": classify(attention),
                    "line": line,
                }
            )
        return rows

    async def _watch_sessions(self, m: Monitor) -> None:
        while True:
            if time.time() - m.started_at > m.timeout_s:
                await self._end(
                    m, "timeout", f"it ran out of time after {_elapsed(m.timeout_s)}"
                )
                return
            rows = self._read_sessions(m)
            if rows != m.sessions:
                m.sessions = rows
                done = sum(1 for r in rows if r["state"] == "finished")
                m.last_progress = round(100 * done / len(rows))
                m.read(time.time(), m.last_progress)
                self._publish(m)
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
        self._tasks.pop(m.id, None)
        self._save()
        self._registry.refresh_card(m.owner)
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
            await session.deliver(
                f"> from monitor:{m.id} · {outcome} · {iso_now()}", "\n".join(lines)
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
    why = "needs you" if row["attention"] == "needs_you" else "hit an error"
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
