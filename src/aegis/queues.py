"""Queues of worker sessions.

A task waits FIFO for a free slot in its queue (``max_parallel``), then a worker
session is spawned from the queue's agent with the payload as its first
prompt. The worker is an ordinary session with a tab, marked ``worker``.

**A turn ending is not completion.** Ending a turn is how an agent waits, so a
task finishes only when its worker's turn has ended *and* it has no live
monitor, no held inbox message and no open Claude task (a background Bash
still running). Reading a turn boundary as done closed a worker mid-wait in
the old tree on 2026-08-10. The result is the worker's last prose; with a
callback, the enqueuer gets it in its inbox; the worker is archived.

A worker whose process exits on its own fails its task and keeps its tab.

Tasks are an append-only log, ``<state>/tasks.jsonl``, replayed at boot:
pending tasks are dispatched again, and a running task's worker is resumed
with "the server restarted; continue your task".

The queues are read from ``.aegis.yaml`` as it is at each dispatch (config.py),
so a queue added or changed on disk takes tasks with no restart, and the app
dispatches again on every change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .agents import ConfigError, read_config, resolve
from .monitors import iso_now
from .names import default_title
from .ops import OpError

if TYPE_CHECKING:
    from .config import Config
    from .monitors import Monitors
    from .registry import Registry
    from .session import Session

log = logging.getLogger("aegis.queues")
RESTARTED = "The server restarted while you were working on your task. Continue it where you left off."
CONTINUE = "Continue your task where you left off."


@dataclass
class Task:
    id: str
    queue: str
    payload: str
    callback: bool
    enqueuer: str | None  # a log id, or None for a person
    cwd: str
    status: str = "pending"  # pending | running | completed | failed | cancelled
    worker: str | None = None
    result: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def public(self) -> dict:
        d = asdict(self)
        d["position"] = None
        return d


def queues_from(raw: Any) -> dict[str, dict]:
    """Every queue in a parsed ``queues:`` map. A queue that does not name its
    agent and a positive ``max_parallel`` is kept with an ``error``, so
    enqueueing on it says what is wrong; nothing in .aegis.yaml is a default
    (agents.py)."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for name, q in raw.items():
        q = q if isinstance(q, dict) else {}
        agent, limit = q.get("agent"), q.get("max_parallel")
        missing = [
            k for k, v in (("agent", agent), ("max_parallel", limit)) if v in (None, "")
        ]
        if missing:
            verb = "is" if len(missing) == 1 else "are"
            out[str(name)] = {"error": f"{', '.join(missing)} {verb} missing"}
        elif isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            out[str(name)] = {
                "error": f"max_parallel {limit!r} is not a positive integer"
            }
        else:
            out[str(name)] = {"agent": str(agent), "max_parallel": limit}
    return out


def load_queues(config_root: Path) -> dict[str, dict]:
    try:
        raw = read_config(config_root).get("queues")
    except ConfigError:
        return {}
    return queues_from(raw)


class Queues:
    def __init__(
        self, registry: Registry, monitors: Monitors, path: Path, config: Config
    ) -> None:
        self._registry = registry
        self._monitors = monitors
        self._path = path
        self._config = config
        self.tasks: dict[str, Task] = {}
        self._dispatching = False
        self._again = False

    @property
    def queues(self) -> dict[str, dict]:
        """The queues in .aegis.yaml as it is now (config.py)."""
        return self._config.current().queues

    # -- the log --------------------------------------------------------------
    def _log(self, t: Task, event: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a") as f:
            f.write(json.dumps({"event": event, "at": time.time(), **asdict(t)}) + "\n")

    def boot(self) -> list[Task]:
        """Replay the log; return the running tasks whose workers must resume."""
        if self._path.exists():
            for raw in self._path.open(errors="replace"):
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                rec.pop("event", None)
                rec.pop("at", None)
                try:
                    t = Task(**rec)
                except TypeError:
                    continue
                self.tasks[t.id] = t
        resume = []
        for t in self.tasks.values():
            if t.status == "running":
                if t.worker in self._registry.sessions:
                    resume.append(t)
                else:
                    self._fail(t, "its worker was gone after a restart")
        return resume

    # -- position and status ------------------------------------------------------
    def position(self, t: Task) -> int | None:
        if t.status != "pending":
            return None
        pending = [
            x
            for x in self.tasks.values()
            if x.queue == t.queue and x.status == "pending"
        ]
        pending.sort(key=lambda x: x.created_at)
        return pending.index(t) + 1

    def status(self, t: Task) -> dict:
        d = t.public()
        d["position"] = self.position(t)
        if t.worker and t.worker in self._registry.sessions:
            d["worker_handle"] = self._registry.sessions[t.worker].handle
        return d

    def task_of_worker(self, log_id: str) -> Task | None:
        return next(
            (
                t
                for t in self.tasks.values()
                if t.worker == log_id and t.status == "running"
            ),
            None,
        )

    # -- operations ------------------------------------------------------------------
    async def enqueue(
        self, queue: str, payload: str, callback: bool, enqueuer: str | None, cwd: str
    ) -> Task:
        t = Task(
            id=f"task-{secrets.token_hex(4)}",
            queue=queue,
            payload=payload,
            callback=callback,
            enqueuer=enqueuer,
            cwd=cwd,
        )
        self.tasks[t.id] = t
        self._log(t, "enqueued")
        await self.dispatch()
        return t

    async def dispatch(self) -> None:
        """Start what the queues allow. A call that arrives while a dispatch is
        running (a config change, a task finishing) makes that dispatch run
        again, rather than being dropped: its loop read the old limits."""
        if self._dispatching:
            self._again = True
            return
        self._dispatching = True
        try:
            while True:
                self._again = False
                await self._dispatch_once()
                if not self._again:
                    break
        finally:
            self._dispatching = False

    async def _dispatch_once(self) -> None:
        # A task logged on a queue that has since lost a field, or been
        # removed, would wait forever for a slot; fail it with the reason.
        # A moment with no file on disk is an editor saving (Vim and Emacs
        # rename the old file away, a git checkout unlinks it), not a decision
        # to drop the backlog: tasks wait for the file to come back.
        present = self._config.current().exists
        for t in list(self.tasks.values()):
            q = self.queues.get(t.queue)
            if present and t.status == "pending" and (q is None or "error" in q):
                why = q["error"] if q else "it is no longer configured"
                self._fail(t, f"its queue {t.queue!r} in .aegis.yaml: {why}")
        for name, q in self.queues.items():
            if "error" in q:
                continue
            while True:
                running = sum(
                    1
                    for t in self.tasks.values()
                    if t.queue == name and t.status == "running"
                )
                pending = sorted(
                    (
                        t
                        for t in self.tasks.values()
                        if t.queue == name and t.status == "pending"
                    ),
                    key=lambda t: t.created_at,
                )
                if not pending or running >= q["max_parallel"]:
                    break
                await self._start(pending[0], q)

    async def _start(self, t: Task, q: dict) -> None:
        try:
            agents = list(self._config.current().agents)
            spec = resolve(agents, None, q["agent"], {}, Path(t.cwd))
        except OpError as e:
            self._fail(t, f"the queue's agent {q['agent']!r} cannot start: {e.message}")
            return
        try:
            s = await self._registry.spawn(
                spec,
                worker={"task_id": t.id, "queue": t.queue},
                title=default_title(t.payload),
            )
        except Exception as e:
            self._fail(t, f"its worker could not start: {e}")
            return
        t.status, t.worker = "running", s.log_id
        self._log(t, "dispatched")
        await s.send(t.payload)

    async def cancel(self, t: Task) -> None:
        if t.status == "pending":
            t.status, t.finished_at = "cancelled", time.time()
            self._log(t, "cancelled")
        elif t.status == "running":
            t.status, t.finished_at = "cancelled", time.time()
            self._log(t, "cancelled")
            if t.worker in self._registry.sessions:
                await self._registry.close(t.worker)
        if t.enqueuer:
            self._registry.refresh_card(t.enqueuer)
        await self.dispatch()

    async def resume(self, t: Task) -> None:
        s = self._registry.sessions.get(t.worker or "")
        if s is None:
            if t.worker in self._registry.archived:
                s = self._registry.reopen(t.worker)
            else:
                raise LookupError("its worker no longer exists")
        t.status, t.error, t.finished_at = "running", None, None
        self._log(t, "resumed")
        self._registry.refresh_card(s.log_id)
        await s.deliver(
            f"> from queue:{t.queue} · task#{t.id} · resume · {iso_now()}", CONTINUE
        )

    async def resume_after_boot(self, tasks: list[Task]) -> None:
        for t in tasks:
            s = self._registry.sessions.get(t.worker or "")
            if s is None:
                self._fail(t, "its worker was gone after a restart")
                continue
            try:
                await s.deliver(
                    f"> from queue:{t.queue} · task#{t.id} · restart · {iso_now()}",
                    RESTARTED,
                )
            except Exception as e:
                self._fail(t, f"its worker could not resume after a restart: {e}")
        await self.dispatch()

    # -- completion ----------------------------------------------------------------
    def finished(self, s: Session) -> bool:
        return not s.busy and not self._monitors.of(s.log_id) and not s.open_tasks

    def turn_ended(self, s: Session) -> None:
        t = self.task_of_worker(s.log_id)
        if t is None or not self.finished(s):
            return
        asyncio.get_running_loop().create_task(self._complete(t, s))

    async def _complete(self, t: Task, s: Session) -> None:
        if t.status != "running" or not self.finished(s):
            return
        prose = [e["md"] for e in s.entries() if e["kind"] == "prose" and e.get("md")]
        t.status, t.result, t.finished_at = (
            "completed",
            prose[-1] if prose else "",
            time.time(),
        )
        self._log(t, "completed")
        # Archive first, so the enqueuer reacting to its callback finds the
        # worker already put away.
        try:
            await self._registry.close(s.log_id)
        except Exception:
            log.exception("aegis: could not archive worker %s", s.handle)
        await self._callback(t, "ok", t.result or "(the worker said nothing)")
        await self.dispatch()

    def exited(self, s: Session, code: int, stderr_tail: list[str]) -> None:
        t = self.task_of_worker(s.log_id)
        if t is None:
            return
        tail = "\n".join(stderr_tail[-10:])
        self._fail(
            t,
            f"its worker {s.handle} exited with code {code}"
            + (f":\n{tail}" if tail else ""),
        )

    def _fail(self, t: Task, why: str) -> None:
        t.status, t.error, t.finished_at = "failed", why, time.time()
        self._log(t, "failed")
        loop = asyncio.get_running_loop()
        loop.create_task(self._callback(t, "error", f"The task failed: {why}"))
        loop.create_task(self.dispatch())

    async def _callback(self, t: Task, outcome: str, body: str) -> None:
        if not t.callback or not t.enqueuer:
            return
        s = self._registry.sessions.get(t.enqueuer)
        if s is None:
            return
        try:
            await s.deliver(
                f"> from queue:{t.queue} · task#{t.id} · {outcome} · {iso_now()}", body
            )
        except Exception:
            log.exception("aegis: could not deliver the callback of %s", t.id)
