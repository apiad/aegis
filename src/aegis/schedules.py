"""The schedules engine: the clock task, the four layers, the run watcher and
the close rule (docs/superpowers/specs/2026-10-14-schedules-design.html).

A tick passes four layers in order and fires only if it passes all of them.
The clock (clock.py) makes it. The overlap rule holds it (wait), drops it
(skip) or lets it through (parallel) while a run of the schedule is live; at
most one tick is held. The bash check, when there is one, fires only on exit 0
with a stdout whose hash differs from the last fire's; exit 1 means not yet,
anything else is an error, and three errors in a row pause the schedule with a
toast. The fire spawns a session from the schedule's agent, or delivers into a
session's inbox, under a ``> from schedule:`` header.

A run is live until Registry.finished says its session is done, the rule a
queue worker's task follows, which also waits for working children. A new run
is then archived or kept by its close rule, read from its last turn_end, and
its children by the same rule from their own. A run in an existing session is
never closed: that session is someone's.

The clock task wakes at the next tick or after a minute, whichever is first,
and compares wall time. asyncio's sleep counts monotonic time, which stops
while the machine is suspended, so one long sleep would delay a tick by the
length of the suspend. The ticks missed while aegis was down or the machine
slept become one tick, logged as ``missed`` with how many it stands for.

Everything is level-triggered: each pass of the clock also looks at every live
run and every held tick, so a missed hook or a restart changes only how soon.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Coroutine
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from .agents import PERMISSION_ORDER, resolve
from .clock import Clock, clock_from
from .monitors import run_bash
from .names import valid_handle
from .ops import OpError
from .quota_gate import decide, pool_for
from .schedule_log import (
    Check,
    Definition,
    RunLog,
    Schedule,
    State,
    apply,
    compact,
    fold,
    iso,
    rows,
)

if TYPE_CHECKING:
    from .app import App
    from .session import Session, SpawnSpec

log = logging.getLogger("aegis.schedules")
STDOUT_MAX = 8192
ERRORS_TO_PAUSE = 3
FINISHED_KEEP_S = 7 * 86400
WAKE_MAX_S = 60.0
NAME_HINT = "two or three lowercase words joined by hyphens, like a session handle"
# The pause an agent's schedule is born with, and gets again when an agent
# edits it: only a person's resume lifts it (schedule_ops, spec 2026-10-15 §09).
APPROVAL = "approval"
# A fire that fails this way fails on every tick until a person acts.
FATAL = (
    "archived",
    "no_session",
    "not_allowed",
    "unknown_agent",
    "bad_agent",
    "no_agent",
)


def header(name: str, run: int, tick: int) -> str:
    hhmm = datetime.fromtimestamp(tick).strftime("%H:%M")
    return f"> from schedule:{name} · run#{run} · tick {hhmm}"


def message(prompt: str, stdout: str | None) -> str:
    """The prompt, with the check's stdout below it, cut at 8 KB."""
    if not stdout or not stdout.strip():
        return prompt
    cut = stdout.encode()[:STDOUT_MAX].decode("utf-8", "ignore").rstrip()
    return f"{prompt}\n\n```text\n{cut}\n```"


def last_mark(standing: dict) -> str:
    """What the close rule reads: error, none (no turn_end), or the last
    turn_end's attention."""
    if standing.get("turn_error"):
        return "error"
    report = standing.get("report")
    return report["attention"] if report else "none"


def closes(rule: str, mark: str) -> bool:
    return rule == "always" or (rule == "done" and mark == "done")


def _last_line(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1][:300] if lines else ""


class Schedules:
    def __init__(self, app: App) -> None:
        self._app = app
        self._reg = app.sessions
        self._defs = app.roots.state_root / "schedules.json"
        self.log = RunLog(app.roots.state_root / "schedule-runs.jsonl")
        self.items: dict[str, Schedule] = {}
        self.state: dict[str, State] = {}
        # Wall time; tests move it forward instead of waiting for it.
        self.now: Callable[[], float] = time.time
        self._busy: set[str] = set()  # schedules a pass of the layers is in
        self._finishing: set[tuple[str, int]] = set()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._bg: set[asyncio.Task] = set()

    # -- boot and the clock task -----------------------------------------------
    def boot(self) -> None:
        """Load the definitions, compact and replay the run log, and end the
        runs whose session is gone."""
        raw: Any = {}
        if self._defs.exists():
            try:
                raw = json.loads(self._defs.read_text())
            except ValueError:
                log.warning("aegis: %s does not parse; no schedules loaded", self._defs)
        for name, r in (raw if isinstance(raw, dict) else {}).items():
            try:
                self.items[name] = Schedule.load(name, r)
            except (ValidationError, KeyError, TypeError, ValueError) as e:
                log.warning("aegis: skipping schedule %s: %s", name, e)
        now = self.now()
        for name, s in list(self.items.items()):
            if s.finished_at and now - s.finished_at > FINISHED_KEEP_S:
                del self.items[name]
        records = compact(self.log.read(), set(self.items))
        self.log.rewrite(records)
        self.state = fold(records)
        for name, s in self.items.items():
            st = self.state.setdefault(name, State())
            st.last_tick = st.last_tick or s.edited_at
            for run in list(st.live.values()):
                if run.log_id not in self._reg.sessions:
                    self._event(
                        name,
                        "finished",
                        run=run.run,
                        attention="lost",
                        closed=False,
                        secs=round(now - run.started),
                        cost_usd=None,
                    )
        self._save()

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        tasks = [t for t in (self._task, *self._bg) if t is not None]
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    async def _loop(self) -> None:
        while True:
            self._spawn(self.step())
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.wait_s())

    def wait_s(self) -> float:
        """Until the next tick of any schedule, and never more than a minute,
        so a machine that slept is caught up within a minute of waking."""
        now = self.now()
        ticks = [
            t
            for s in self.items.values()
            if not s.paused
            and not s.finished_at
            and (t := clock_from(s.d.clock).next_after(now, s.edited_at)) is not None
        ]
        return max(1.0, min([WAKE_MAX_S, *(t - now for t in ticks)]))

    def quota_opened(self) -> None:
        """A quota reading came in: a tick held for the gate may go."""
        self._wake.set()

    def _spawn(self, coro: Coroutine) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return
        t = loop.create_task(coro)
        self._bg.add(t)

        def done(t: asyncio.Task) -> None:
            self._bg.discard(t)
            if not t.cancelled() and (e := t.exception()) is not None:
                log.error("aegis: schedules: %s", e, exc_info=e)

        t.add_done_callback(done)

    async def step(self) -> None:
        """One pass: finish the live runs that are done, then bring every
        schedule up to now. The clock task runs it; tests call it with
        ``now`` moved forward."""
        self.changed()
        await asyncio.gather(*(self._advance(n) for n in list(self.items)))

    async def _advance(self, name: str) -> None:
        if name in self._busy:
            return
        self._busy.add(name)
        try:
            s, st = self.items.get(name), self.state.get(name)
            if s is None or st is None or s.paused or s.finished_at:
                return
            clock = clock_from(s.d.clock)
            latest, n = clock.due(st.last_tick, self.now(), s.edited_at)
            if latest is not None:
                if n > 1:
                    self._tick(name, latest, "missed", n=n)
                await self._layers(name, latest)
            elif st.held and self._may_release(s, st):
                await self._layers(name, st.held_tick or st.last_tick)
        finally:
            self._busy.discard(name)

    def _may_release(self, s: Schedule, st: State) -> bool:
        if st.held == "overlap":
            return not st.live
        try:
            if s.d.fire.target == "existing":
                t = self._target(s).spec
                return self._line(s, t.harness, t.model) is None
            spec = self._spec(s)
        except OpError:
            return True  # the fire says what is wrong
        return (
            self._app.gate(spec.owner, spec.harness, spec.model).may_start
            and self._line(s, spec.harness, spec.model) is None
        )

    def _line(self, s: Schedule, harness: str, model: str | None) -> str | None:
        """Why the schedule's own launch line for the pool a fire draws on
        holds it, or None when every line is clear (quota_gate.decide). A
        pool aegis has never read has no line: there is nothing to measure,
        and holding every tick would stop the schedule for good. A stale
        reading holds, as it does a token's gate."""
        pool = pool_for(harness, model)
        lines = s.d.quota.get(pool or "")
        snap = self._app.quota.snapshot()
        read = any(
            p.get("name") == pool and p.get("read_at") is not None
            for p in snap.get("providers", [])
        )
        if not pool or not lines or not read:
            return None
        policy = {
            pool: {
                "mode": "lines",
                "lines": {k: {"launch": v, "stop": 100} for k, v in lines.items()},
            }
        }
        g = decide(policy, pool, snap, time.time())
        return None if g.may_start else g.why

    # -- the layers ---------------------------------------------------------------
    async def _layers(self, name: str, tick: int, manual: bool = False) -> str:
        """Overlap, check and fire for one tick; returns what happened."""
        s, st = self.items[name], self.state[name]
        extra = {"manual": True} if manual else {}
        if st.live and s.d.overlap != "parallel":
            if s.d.overlap == "skip":
                return self._tick(name, tick, "skipped", **extra)
            return self._tick(name, tick, "held", why="overlap", **extra)
        stdout, sha = None, None
        if s.d.check is not None and not manual:
            rc, out, err = await run_bash(
                s.d.check.bash, self._cwd(s), s.d.check.timeout_s
            )
            if rc == 0:
                sha = hashlib.sha256(out.encode()).hexdigest()
                if sha == st.sha:
                    return self._tick(name, tick, "quiet", rc=0, sha=sha)
                stdout = out
            elif rc == 1:
                return self._tick(name, tick, "quiet", rc=1)
            else:
                why = _last_line(err) or f"exit {rc}"
                return self._tick(name, tick, "error", rc=rc, err=why)
        return await self._fire(name, tick, stdout, sha, extra)

    async def _fire(
        self, name: str, tick: int, stdout: str | None, sha: str | None, extra: dict
    ) -> str:
        s, st = self.items[name], self.state[name]
        run = st.runs + 1
        head, text = header(name, run, tick), message(s.d.prompt, stdout)
        try:
            if s.d.fire.target == "new":
                spec = self._spec(s)
                if not self._app.gate(spec.owner, spec.harness, spec.model).may_start:
                    return self._tick(name, tick, "held", why="quota", **extra)
                if line := self._line(s, spec.harness, spec.model):
                    return self._tick(
                        name, tick, "held", why="quota", line=line, **extra
                    )
                ses = await self._reg.spawn(
                    spec,
                    title=f"{name} · run {run}",
                    schedule={"name": name, "run": run, "spawn": s.d.fire.spawn},
                )
                await ses.send(f"{head}\n{text}")
            else:
                ses = self._target(s)
                if line := self._line(s, ses.spec.harness, ses.spec.model):
                    return self._tick(
                        name, tick, "held", why="quota", line=line, **extra
                    )
                await ses.deliver(head, text)
        except OpError as e:
            self._tick(name, tick, "error", err=e.message, **extra)
            if e.code in FATAL:
                self._pause(name, "errors", e.message)
            return "error"
        except Exception as e:
            log.exception("aegis: schedule %s could not fire", name)
            return self._tick(name, tick, "error", err=f"the fire failed: {e}", **extra)
        self._tick(name, tick, "fired", sha=sha, **extra)
        self._event(
            name,
            "fired",
            run=run,
            log_id=ses.log_id,
            target=s.d.fire.target,
            tick=tick,
            stdout=(stdout or "")[:STDOUT_MAX],
        )
        return "fired"

    def _spec(self, s: Schedule) -> SpawnSpec:
        """The spawn a new fire runs; refused above the creator's ceiling."""
        agents, default = self._app._agents()
        cwd = Path(s.d.fire.cwd) if s.d.fire.cwd else self._app.roots.harness_cwd
        spec = resolve(
            agents, default, s.d.fire.agent, {}, cwd, owner=s.owner.get("principal")
        )
        ceiling = s.owner.get("ceiling")
        if ceiling and PERMISSION_ORDER.index(spec.permission) > PERMISSION_ORDER.index(
            ceiling
        ):
            raise OpError(
                "not_allowed",
                f"{s.name} was made by an agent running with {ceiling}, so it cannot "
                f"start {spec.agent}, which runs with {spec.permission}",
            )
        return spec

    def _target(self, s: Schedule) -> Session:
        ref = s.d.fire.session or ""
        ses = self._reg.sessions.get(ref)
        if ses is not None:
            return ses
        if ref in self._reg.archived:
            handle = self._reg.archived[ref].get("handle") or ref
            raise OpError("archived", f"its session {handle} is archived")
        raise OpError("no_session", f"its session {ref} no longer exists")

    def _cwd(self, s: Schedule) -> str:
        if s.d.fire.target == "existing":
            ses = self._reg.sessions.get(s.d.fire.session or "")
            return str(ses.spec.cwd if ses else self._app.roots.harness_cwd)
        return s.d.fire.cwd or str(self._app.roots.harness_cwd)

    def workflow_changed(self, wid: str) -> None:
        """A saved workflow was saved or deleted. Filled in by slice 6's
        task 9: a schedule pinned to it pauses for a person's approval."""

    # -- runs ------------------------------------------------------------------------
    def changed(self) -> None:
        """A status changed, a turn ended or a session closed: finish each live
        run whose session is done. Every live run is looked at, so a child's
        change reaches the run it belongs to."""
        for name, st in list(self.state.items()):
            for run in list(st.live.values()):
                if (name, run.run) in self._finishing:
                    continue
                ses = self._reg.sessions.get(run.log_id)
                if ses is None or self._reg.finished(ses):
                    self._spawn(self._finish(name, run.run))

    async def _finish(self, name: str, k: int) -> None:
        st = self.state.get(name)
        run = st.live.get(k) if st else None
        key = (name, k)
        if run is None or key in self._finishing:
            return
        self._finishing.add(key)
        try:
            ses = self._reg.sessions.get(run.log_id)
            if ses is not None and not self._reg.finished(ses):
                return
            s = self.items.get(name)
            mark = last_mark(ses.standing) if ses is not None else "closed"
            cost = self._cost(run.log_id)
            closed = False
            if ses is not None and run.target == "new" and s is not None:
                rule = s.d.fire.close
                for kid in self._descendants(run.log_id):
                    if closes(rule, last_mark(kid.standing)):
                        await self._close(kid)
                if closes(rule, mark):
                    closed = await self._close(ses)
            self._event(
                name,
                "finished",
                run=k,
                attention=mark,
                closed=closed,
                secs=round(self.now() - run.started),
                cost_usd=cost,
            )
        finally:
            self._finishing.discard(key)
        if name in self.items:
            await self._advance(name)  # a tick held for this run goes now

    async def _close(self, ses: Session) -> bool:
        try:
            await self._reg.close(ses.log_id)
            return True
        except Exception:
            log.exception("aegis: could not archive %s after its run", ses.handle)
            return False

    def _descendants(self, log_id: str) -> list[Session]:
        """The open sessions a run started, transitively, deepest first."""
        out: list[Session] = []
        frontier = {log_id}
        while frontier:
            kids = [
                s for s in self._reg.sessions.values() if s.spec.spawned_by in frontier
            ]
            out += kids
            frontier = {s.log_id for s in kids}
        return out[::-1]

    def _cost(self, log_id: str) -> float:
        """The run session's cost plus its children's, open or archived."""
        metas = [s.meta() for s in self._reg.sessions.values()]
        metas += list(self._reg.archived.values())
        kids: dict[str, list[dict]] = {}
        for m in metas:
            kids.setdefault(m.get("spawned_by") or "", []).append(m)
        todo = [m for m in metas if m["log_id"] == log_id]
        total = 0.0
        while todo:
            m = todo.pop()
            total += m.get("cost_usd") or 0.0
            todo += kids.get(m["log_id"], [])
        return round(total, 4)

    # -- the log and the definitions --------------------------------------------------
    def _tick(self, name: str, tick: int, out: str, **kw: Any) -> str:
        self._event(name, "tick", tick=tick, out=out, **kw)
        s, st = self.items.get(name), self.state[name]
        if s is None:
            return out
        if out == "error" and st.errors >= ERRORS_TO_PAUSE:
            self._pause(
                name,
                "errors",
                f"{ERRORS_TO_PAUSE} errors in a row, the last: {kw.get('err')}",
            )
        if "at" in s.d.clock and out not in ("held", "missed") and not kw.get("manual"):
            s.finished_at = int(self.now())
            self._save()
            self._publish(name)
        return out

    def _event(self, name: str, ev: str, **kw: Any) -> None:
        rec = {"t": iso(self.now()), "s": name, "ev": ev, **kw}
        self.log.append(rec)
        apply(self.state.setdefault(name, State()), rec)
        self._publish(name)

    def _save(self) -> None:
        self._defs.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._defs.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({n: s.dump() for n, s in self.items.items()}, indent=1)
        )
        os.replace(tmp, self._defs)

    def _publish(self, name: str) -> None:
        op = {"upsert": self.wire(name)} if name in self.items else {"remove": name}
        self._app.publish("schedules", [op])

    def _pause(self, name: str, by: str, why: str | None = None) -> None:
        s = self.items[name]
        if s.paused:
            return
        s.paused = {"by": by, "at": iso(self.now())}
        self._save()
        self._event(name, "paused", by=by, **({"why": why} if why else {}))
        if by == "errors":
            self._app.toast(f"Schedule {name} is paused: {why}", "error")

    def _get(self, name: str) -> Schedule:
        s = self.items.get(name)
        if s is None:
            raise OpError("no_schedule", f"no schedule {name!r}")
        return s

    def _settle(self, d: Definition, cwd: Path) -> Definition:
        """Store an existing target by log id, so a rename does not lose it,
        and a new fire with the directory it runs in."""
        f = d.fire
        if f.target == "existing":
            ref = f.session or ""
            ses = self._reg.sessions.get(ref) or self._reg.by_handle(ref)
            if ses is None:
                raise OpError("no_session", f"no open session {ref!r}")
            f = f.model_copy(update={"session": ses.log_id})
        else:
            f = f.model_copy(update={"cwd": str(cwd)})
        return d.model_copy(update={"fire": f})

    # -- what the operations call ---------------------------------------------------
    def create(self, name: str, d: Definition, owner: dict, cwd: Path) -> Schedule:
        if not valid_handle(name):
            raise OpError("bad_name", f"a schedule's name is {NAME_HINT}")
        if name in self.items:
            raise OpError("name_taken", f"there is already a schedule named {name}")
        now = int(self.now())
        s = Schedule(name, owner, now, now, self._settle(d, cwd))
        if s.d.fire.target == "new":
            self._spec(s)  # an unknown agent, or one above the ceiling, is refused now
        if owner.get("kind") == "agent":
            s.paused = {"by": APPROVAL, "at": iso(self.now())}
        self.items[name] = s
        self.state[name] = State()
        self._save()
        self._event(name, "created", tick=now)
        if s.paused:
            self._event(name, "paused", by=APPROVAL)
        self._wake.set()
        return s

    def update(
        self, name: str, d: Definition, cwd: Path, by_agent: bool = False
    ) -> Schedule:
        old = self._get(name)
        now = int(self.now())
        paused = old.paused
        if by_agent:
            paused = {"by": APPROVAL, "at": iso(self.now())}
        s = Schedule(name, old.owner, old.created_at, now, self._settle(d, cwd), paused)
        if s.d.fire.target == "new":
            self._spec(s)
        self.items[name] = s
        self._save()
        self._event(name, "edited", tick=now)
        if by_agent and (old.paused or {}).get("by") != APPROVAL:
            self._event(name, "paused", by=APPROVAL)
        self._wake.set()
        return s

    def awaiting(self, name: str) -> bool:
        """Whether a person has yet to approve ``name``."""
        p = self._get(name).paused
        return bool(p and p.get("by") == APPROVAL)

    def delete(self, name: str) -> None:
        """A live run keeps going, and its sessions are left as they are."""
        self._get(name)
        del self.items[name]
        self.state.pop(name, None)
        self.log.append({"t": iso(self.now()), "s": name, "ev": "deleted"})
        self._save()
        self._publish(name)

    def pause(self, name: str) -> None:
        self._get(name)
        self._pause(name, "user")

    def resume(self, name: str) -> None:
        """Ticks that fell during the pause are not missed ticks: the clock
        starts again from now."""
        s = self._get(name)
        s.paused = None
        self._save()
        self._event(name, "resumed", tick=int(self.now()))
        self._wake.set()

    async def fire_now(self, name: str) -> str:
        """A tick that skips the clock and the check and still respects overlap."""
        self._get(name)
        if name in self._busy:
            raise OpError("busy", f"{name} is in the middle of a tick; try again")
        self._busy.add(name)
        try:
            return await self._layers(name, int(self.now()), manual=True)
        finally:
            self._busy.discard(name)

    async def test(
        self, clock: Clock, check: Check | None, cwd: Path, name: str | None
    ) -> dict:
        """The next three ticks, and what the check would do now. Writes nothing."""
        now = int(self.now())
        out: dict = {
            "next": [iso(t) for t in clock.upcoming(now, now)],
            "tz": time.strftime("%Z"),
        }
        if check is not None:
            rc, stdout, err = await run_bash(check.bash, str(cwd), check.timeout_s)
            st = self.state.get(name or "")
            sha = hashlib.sha256(stdout.encode()).hexdigest() if rc == 0 else None
            fires = rc == 0 and sha != (st.sha if st else None)
            if fires:
                why = "exit 0 with a stdout different from the last fire's"
            elif rc == 0:
                why = "exit 0 with the same stdout as the last fire"
            elif rc == 1:
                why = "exit 1: not yet"
            else:
                why = f"an error: {_last_line(err) or f'exit {rc}'}"
            out["check"] = {
                "rc": rc,
                "stdout": stdout[:STDOUT_MAX],
                "stderr": err[-2000:],
                "would_fire": fires,
                "why": why,
            }
        return out

    def runs(
        self, name: str, offset: int = 0, limit: int = 50, quiet: bool = False
    ) -> dict:
        self._get(name)

        def handle(log_id: str) -> str | None:
            s = self._reg.sessions.get(log_id)
            return (
                s.handle if s else (self._reg.archived.get(log_id) or {}).get("handle")
            )

        every = rows(self.log.of(name), handle)[::-1]
        for r in every:
            if "log_id" in r:
                r["open"] = r["log_id"] in self._reg.sessions
        shown = [r for r in every if quiet or r["kind"] != "quiet"]
        return {
            "rows": shown[offset : offset + limit],
            "total": len(shown),
            "quiet": sum(1 for r in every if r["kind"] == "quiet"),
        }

    def wire(self, name: str) -> dict:
        s, st = self.items[name], self.state.get(name) or State()
        nxt = None
        if not s.paused and not s.finished_at:
            nxt = clock_from(s.d.clock).next_after(self.now(), s.edited_at)
        by = self._reg.sessions.get(s.owner.get("log_id") or "")
        return {
            "name": name,
            **s.dump(),
            "owner_handle": by.handle if by else None,
            "next": iso(nxt) if nxt else None,
            "held": st.held,
            "errors": st.errors,
            "runs": st.runs,
            "live": [
                {
                    "run": r.run,
                    "log_id": r.log_id,
                    "handle": h.handle
                    if (h := self._reg.sessions.get(r.log_id))
                    else None,
                }
                for r in st.live.values()
            ],
            "tz": time.strftime("%Z"),
        }

    def wire_all(self) -> list[dict]:
        return [self.wire(n) for n in sorted(self.items)]
