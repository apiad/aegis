"""One Claude Code session: its store, its fold, its status, and a process when
it has one.

A session exists without a process. ``stopped`` means there is none; the first
prompt starts ``claude``, with ``--resume`` once the harness has given the session
an id, and the store and the fold simply continue (Claude prints nothing old on
resume, measured in issue #127). Only shutdown, Stop and Close end a process:
a session waiting on its own background task wakes itself, so nothing stops an
idle-looking one.

The spec is the session's model, effort and permission. ``configure`` changes
them, live through control requests when there is a process, and the next
``--resume`` is built from the spec, so a change outlives the process. Each
process start also asks ``claude`` for its commands and models (``initialize``)
and hands the answer to the host, which keeps it by cwd.

The session is a continuous fold. Every stdout line is stored, parsed once,
folded into entry patches and published, whatever the turn state. Status: only a
sent prompt or a turn-bearing event moves it to ``working``, and only ``result``
moves it back to ``idle``; system notices never do. ``error`` is an interrupt
that went unanswered. A child that exits leaves the session ``stopped``.

A clean server shutdown records nothing in the store. The meta keeps the last
status, and the registry marks a session that was ``working`` at its next boot,
so a crash and a clean stop read the same.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .claude.control import Catalog
from .claude.process import ControlError
from .claude.stream import TURN_BEARING, Delta, Init, Notice, Result, Title
from .harness import Launch, Process, harness_for
from .meta import MetaStore
from .names import default_title
from .transcript.entries import EMPTY_STANDING, Fold, fold_records
from .transcript.store import Store, read_store

Publish = Callable[[str, list[dict]], None]

# What a fleet card shows. A change to one of _NOW publishes the session at
# once; the others change on nearly every line of a turn (context grows with
# each message, activity with each call), so they wait up to PUBLISH_EVERY_S
# and go out together. Publishing them per line doubled the server's cost per
# line in the bench (issue #127).
_NOW = ("status", "handle", "title", "model_id", "standing")
_SOON = (
    "activity",
    "cost_usd",
    "context_tokens",
    "context_window",
    "unread",
    "recap_cost_usd",
)
PUBLISH_EVERY_S = 0.25


class Host:
    """What a session asks of the server around it. The registry implements
    it; the default does nothing, which is what a bare session in a test or
    the bench needs."""

    def spawn_args(
        self, session: "Session"
    ) -> tuple[tuple[str, str] | None, str | None]:
        """The aegis MCP URL and this session's token, and the appended system
        prompt, for a new process."""
        return None, None

    def turn_ended(self, session: "Session") -> None: ...

    def status_changed(self, session: "Session") -> None:
        """The session's status, or what it waits on, changed: a parent waiting
        on it re-derives."""

    def exited(self, session: "Session", code: int, stderr_tail: list[str]) -> None: ...

    def card(self, session: "Session") -> dict:
        """Extra fields for the session's card on the ``sessions`` channel."""
        return {}

    def catalog_ready(self, session: "Session", catalog: Catalog) -> None:
        """A process answered ``initialize``: its cwd's commands and models."""


NO_HOST = Host()


class Archived(Exception):
    """A message was sent to an archived session."""


@dataclass(frozen=True)
class SpawnSpec:
    """What a session's process runs with, fixed at spawn and recorded in the
    spawn record and the meta. A resume reads it from there, never from
    ``.aegis.yaml``: Claude Code does not keep the system prompt in its own
    session file, so editing an agent must not change its old sessions. Only
    model, effort and permission change later, through ``Session.configure``,
    which records each change for the meta and a rebuild."""

    agent: str
    model: str
    effort: str
    permission: str
    cwd: Path
    harness: str = "claude-code"
    priming: str | None = None
    # The fields this spawn changed from its agent, such as ("model",).
    overridden: tuple[str, ...] = ()
    # The log id of the agent session that spawned this one.
    spawned_by: str | None = None

    def record(self) -> dict:
        """The fields a spawn record and a meta carry."""
        d: dict = {
            "agent": self.agent,
            "harness": self.harness,
            "model": self.model,
            "effort": self.effort,
            "permission": self.permission,
            "cwd": str(self.cwd),
        }
        if self.priming:
            d["priming"] = self.priming
        if self.overridden:
            d["overridden"] = list(self.overridden)
        if self.spawned_by:
            d["spawned_by"] = self.spawned_by
        return d

    @classmethod
    def from_record(cls, d: dict, cwd_default: Path) -> "SpawnSpec":
        # Records from before #155 carry `profile` and no harness; every one of
        # them ran Claude Code.
        return cls(
            agent=str(d.get("agent") or d.get("profile") or ""),
            model=str(d.get("model") or ""),
            effort=str(d.get("effort") or "high"),
            permission=str(d.get("permission") or "auto"),
            cwd=Path(d.get("cwd") or cwd_default),
            harness=str(d.get("harness") or "claude-code"),
            priming=d.get("priming") or None,
            overridden=tuple(d.get("overridden") or ()),
            spawned_by=d.get("spawned_by"),
        )


class Session:
    def __init__(
        self,
        *,
        log_id: str,
        spec: SpawnSpec,
        handle: str,
        store: Store,
        stderr_path: Path,
        claude_bin: str,
        publish: Publish,
        metas: MetaStore,
        title: str = "",
        resume_id: str | None = None,
        archived: bool = False,
        created_at: float | None = None,
        last_activity: float | None = None,
        last_status: str = "stopped",
        cost_usd: float | None = None,
        context_tokens: int | None = None,
        context_window: int | None = None,
        activity: str = "",
        model_id: str | None = None,
        interrupt_timeout: float = 10.0,
        host: Host = NO_HOST,
        held: list[dict] | None = None,
        worker: dict | None = None,
        opencode_bin: str = "opencode",
        title_set: bool = False,
        standing: dict | None = None,
        unread: list[str] | None = None,
        last_read_at: float | None = None,
        recap_cost_usd: float = 0.0,
    ) -> None:
        self.log_id = log_id
        self.spec = spec
        self.handle = handle
        self.title = title
        # A person or an agent named it; the harness's own title then waits.
        self.title_set = title_set
        self.store = store
        self.channel = f"transcript:{log_id}"
        self.resume_id = resume_id
        self.archived = archived
        self.created_at = created_at or time.time()
        self.last_activity = last_activity or self.created_at
        self.last_status = last_status
        self.cost_usd = cost_usd
        self.context_tokens = context_tokens
        self.context_window = context_window
        self.activity = activity
        self.model_id = model_id
        self.status = "stopped"
        self._stderr_path = stderr_path
        self.harness = harness_for(spec.harness, claude_bin, opencode_bin)
        self._publish = publish
        self._metas = metas
        self._interrupt_timeout = interrupt_timeout
        self._fold: Fold | None = None
        self._proc: Process | None = None
        self._stopping = False
        self._interrupt_timer: asyncio.Task | None = None
        self._publish_timer: asyncio.TimerHandle | None = None
        self._host = host
        # Inbox messages waiting for this session's turn to end.
        self.held: list[dict] = list(held or [])
        self._flushing = False
        # One start at a time: an OpenCode start takes seconds, and two sends
        # in that window must not start two children.
        self._starting = asyncio.Lock()
        # Bumped per process start, so a stale process's exit changes nothing.
        self._generation = 0
        # Claude's own tasks started and not yet notified (Bash, background).
        self.open_tasks: set[str] = set()
        self.worker = worker
        # The fold's view of the agent's plan and last report; persisted so a
        # card at boot needs no store (DESIGN.md, boot reads meta files).
        self.standing: dict = standing or EMPTY_STANDING
        # Agent messages (prose entries) no person has read yet, on any browser,
        # and when someone last read. In the meta, so boot needs no store; a meta
        # from before this has neither, and nothing old turns up unread.
        self.unread: set[str] = set(unread or ())
        self.last_read_at = last_read_at
        # What this session's recaps cost (recaps.py), apart from its own turns.
        self.recap_cost_usd = recap_cost_usd
        # The current process's catalog; None as a result when it did not answer.
        self.catalog_task: asyncio.Task[Catalog | None] | None = None

    # -- what the outside sees -------------------------------------------
    def meta(self) -> dict:
        """The persisted form (``<state>/sessions/<log_id>.json``)."""
        s = self.spec
        return {
            "log_id": self.log_id,
            "handle": self.handle,
            "title": self.title,
            "title_set": self.title_set,
            **s.record(),
            "model_id": self.model_id,
            "resume_id": self.resume_id,
            "archived": self.archived,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "last_status": self.status if self._proc else self.last_status,
            "cost_usd": self.cost_usd,
            "context_tokens": self.context_tokens,
            "context_window": self.context_window,
            "activity": self.activity,
            "held": self.held,
            "worker": self.worker,
            "standing": self.standing,
            "unread": sorted(self.unread),
            "last_read_at": self.last_read_at,
            "recap_cost_usd": self.recap_cost_usd,
        }

    def wire(self) -> dict:
        """What the ``sessions`` channel carries."""
        m = self.meta()
        m.pop("held")
        m.pop("standing")
        m.pop("priming", None)  # the agent's text stays on the server
        m["unread"] = len(self.unread)
        m["held_count"] = len(self.held)
        m["state"] = self.status
        m["model"] = self.model_id or self.spec.model
        m["harness_label"] = self.harness.label
        m.update(self._host.card(self))
        return m

    @property
    def busy(self) -> bool:
        """Mid-turn, or about to start one to receive held messages."""
        return self.status == "working" or self._flushing or bool(self.held)

    @property
    def in_turn(self) -> bool:
        """Mid-turn, or starting one to deliver held messages."""
        return self.status == "working" or self._flushing

    def fold(self) -> Fold:
        if self._fold is None:
            if self.store.path.exists():
                records, _ = read_store(self.store.path)
                self._fold = fold_records(records)
            else:
                self._fold = Fold()
        return self._fold

    def entries(self) -> list[dict]:
        return self.fold().entries()

    def view(self) -> list[dict]:
        """The entries as the transcript channel serves them: each agent message
        carries whether it is unread. The fold's entries never carry it, so a
        refold of the store still equals them."""
        return [self._dress(e) for e in self.entries()]

    def _dress(self, e: dict) -> dict:
        return {**e, "unread": e["id"] in self.unread} if e["kind"] == "prose" else e

    def read(self, ids: list[str]) -> int:
        """A person read these agent messages; ids that are not unread are
        ignored. Publishes the changed entries and the card at once."""
        hit = [i for i in dict.fromkeys(ids) if i in self.unread]
        if not hit:
            return 0
        # _set writes the meta; the card goes out at once below, not in a batch.
        self._set(unread=self.unread - set(hit), last_read_at=time.time())
        by_id = {e["id"]: e for e in self.fold().entries()}
        self._publish(
            self.channel,
            [{"upsert": self._dress(by_id[i])} for i in hit if i in by_id],
        )
        self._publish_now()
        return len(hit)

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.running

    # -- operations --------------------------------------------------------
    async def start(self) -> None:
        """A brand-new session: record the spawn, then start ``claude``."""
        self._record({"kind": "spawn", **self.spec.record()})
        await self.ensure_running()

    async def ensure_running(self) -> None:
        """Start the harness if there is no process. Raises FileNotFoundError
        when its binary is missing, leaving the session stopped."""
        async with self._starting:
            if not self.running:
                await self._start_process()

    async def _start_process(self) -> None:
        self._generation += 1
        generation = self._generation

        def on_exit(code: int, tail: list[str]) -> None:
            if generation == self._generation:
                self._on_exit(code, tail)

        resume = self.resume_id
        mcp, system_prompt = self._host.spawn_args(self)
        proc = self.harness.process(
            Launch(
                cwd=self.spec.cwd,
                model=self.spec.model,
                effort=self.spec.effort,
                permission=self.spec.permission,
                resume_id=resume,
                mcp=mcp,
                system_prompt=system_prompt,
                stderr_path=self._stderr_path,
                on_line=self._on_line,
                on_exit=on_exit,
                on_error=self._on_error,
            )
        )
        # Before the start: a harness can speak while it starts (OpenCode's
        # session.created), and a line read while stopping is dropped.
        self._stopping = False
        await proc.start()
        self._proc = proc
        self.catalog_task = asyncio.create_task(self._fetch_catalog(proc))
        self.open_tasks.clear()
        if resume:
            self._record({"kind": "resume", "resume_id": resume})
        sid = proc.session_id
        if resume and sid and sid != resume:
            self._record(
                {
                    "kind": "reset",
                    "text": f"{self.harness.label} no longer had this conversation; "
                    "it started a new one",
                }
            )
        if sid:
            self._set(resume_id=sid)
        self._set(status="idle")

    async def _fetch_catalog(self, proc: Process) -> Catalog | None:
        try:
            cat = await proc.catalog()
        except (
            ControlError,
            TimeoutError,
            BrokenPipeError,
            ConnectionResetError,
        ):
            return None
        self._host.catalog_ready(self, cat)
        self._window_from(cat)
        return cat

    def _window_from(self, cat: Catalog | None) -> None:
        """A harness whose events name no context window (OpenCode) has it in
        its catalog."""
        m = cat.model(self.spec.model) if cat is not None else None
        if m is not None and m.window:
            self._set(context_window=m.window)

    async def configure(
        self,
        model: str | None = None,
        effort: str | None = None,
        permission: str | None = None,
    ) -> None:
        """Change the model, effort or permission. A live process gets each
        change through a control request first; the spec, which the next
        ``--resume`` is built from, takes only what applied, and a refusal is
        raised after that is recorded."""
        proc = self._proc if self.running else None
        when = (
            "on_resume"
            if proc is None
            else "next_turn"
            if self.status == "working"
            else ""
        )
        applied: dict[str, str] = {}
        try:
            for kind, value in (
                ("model", model),
                ("effort", effort),
                ("permission", permission),
            ):
                if not value:
                    continue
                if proc is not None:
                    await proc.set(kind, value)
                applied[kind] = value
        finally:
            if applied:
                self.spec = dataclasses.replace(self.spec, **applied)
                if "model" in applied:
                    self.model_id = None  # the next init names the resolved id
                    t = self.catalog_task
                    if t is not None and t.done() and not t.cancelled():
                        self._window_from(t.result())
                self._record(
                    {"kind": "configure", **applied, **({"when": when} if when else {})}
                )
                self._publish_now()
                self._metas.write(self.meta())

    async def deliver(self, header: str, body: str) -> None:
        """An inbox message. Idle or stopped: sent now (a stopped session is
        resumed to receive it). Mid-turn: held, and sent with any others when
        the turn ends, because a prompt written mid-turn would be injected at
        the next tool boundary instead of waiting."""
        if self.archived:
            raise Archived(self.log_id)
        msg = {"header": header, "body": body, "ts": time.time()}
        if self.status == "working" or self._flushing:
            self.held.append(msg)
            self._metas.write_soon(self.log_id, self.meta)
            self._publish_now()
            return
        await self.send(_inbox_text([msg]))

    async def _flush_held(self) -> None:
        self._flushing = True
        try:
            batch, self.held = self.held, []
            if batch:
                await self.send(_inbox_text(batch))
        finally:
            self._flushing = False

    async def send(self, text: str) -> None:
        await self.ensure_running()
        assert self._proc is not None
        if not self.title and not text.startswith("/"):
            # A slash command names no task; the first prompt does.
            self._set(title=default_title(text))
        self._record({"kind": "send", "text": text})
        # Working before the send returns: a harness can answer during it (an
        # OpenCode command refused before it starts a turn).
        self._set(status="working")
        try:
            await self._proc.send(text)
        except Exception as e:
            self._on_error(f"the message did not reach the agent: {e}", True, text)
            raise

    async def interrupt(self) -> None:
        if self.status != "working" or self._proc is None:
            return
        self._record({"kind": "interrupt"})
        # The deadline first: an interrupt that cannot reach the child still
        # ends in "error" instead of leaving the session working.
        if self._interrupt_timer is None or self._interrupt_timer.done():
            self._interrupt_timer = asyncio.create_task(self._interrupt_deadline())
        await self._proc.interrupt()

    async def stop(self) -> None:
        """End the process and keep the session."""
        if self._proc is None:
            return
        await self._end_process()
        self._record({"kind": "stop"})
        self._set(status="stopped")
        self._metas.write(self.meta())

    async def shutdown(self) -> None:
        """The server is stopping: end the process, record nothing, and keep the
        last status in the meta so a mid-turn session is marked at next boot."""
        if self._proc is not None:
            self.last_status = self.status
            await self._end_process()
        self.store.close()
        self._metas.write(self.meta())

    # -- internals ---------------------------------------------------------
    async def _end_process(self) -> None:
        if self._interrupt_timer:
            self._interrupt_timer.cancel()
        if self.catalog_task is not None and not self.catalog_task.done():
            self.catalog_task.cancel()
        proc, self._proc = self._proc, None
        if proc is not None:
            self._stopping = True
            await proc.terminate()

    async def _interrupt_deadline(self) -> None:
        await asyncio.sleep(self._interrupt_timeout)
        if self.status == "working" and self._proc is not None:
            self._record(
                {"kind": "interrupt_timeout", "after_s": self._interrupt_timeout}
            )
            self._set(status="error")

    def record_file(self, record: dict) -> None:
        """A file sent by the agent (files.store's record plus a caption)."""
        self._record(record)

    def report(self, record: dict) -> None:
        """A plan or a turn report from the agent (agent_ops)."""
        self._record(record)

    def add_recap_cost(self, cost: float) -> None:
        self._set(recap_cost_usd=round(self.recap_cost_usd + cost, 6))

    def _record(self, record: dict, events: list | None = None) -> None:
        fold = self.fold()
        stored = self.store.append({"ts": time.time(), "src": "aegis", **record})
        self.last_activity = stored["ts"]
        ops = fold.apply(stored, events)
        new = [
            op["upsert"]["id"]
            for op in ops
            if op.get("upsert", {}).get("kind") == "prose"
        ]
        if new:
            # Before dressing the ops, so the new rows go out unread. "unread" is
            # in _SOON: the card's count follows within PUBLISH_EVERY_S.
            self._set(unread=self.unread | set(new))
        self._publish(
            self.channel,
            [
                {"upsert": self._dress(op["upsert"])} if "upsert" in op else op
                for op in ops
            ],
        )
        if fold.standing is not self.standing:
            self._set(standing=fold.standing)
            self.standing = fold.standing
        if any(
            op.get("upsert", {}).get("kind") in ("user", "prose", "tool", "file")
            for op in ops
        ):
            self._set(activity=fold.activity())

    def _set(self, **changes: object) -> None:
        changes = {k: v for k, v in changes.items() if getattr(self, k) != v}
        if not changes:
            return
        for k, v in changes.items():
            setattr(self, k, v)
        if "status" in changes:
            self.last_status = self.status
            self._host.status_changed(self)
        if any(k in _NOW for k in changes):
            self._publish_now()
        elif any(k in _SOON for k in changes) and self._publish_timer is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self._publish_now()
            else:
                self._publish_timer = loop.call_later(
                    PUBLISH_EVERY_S, self._publish_now
                )
        if changes.get("status") in ("idle", "stopped", "error"):
            self._metas.write(self.meta())
        else:
            self._metas.write_soon(self.log_id, self.meta)

    def _publish_now(self) -> None:
        if self._publish_timer is not None:
            self._publish_timer.cancel()
            self._publish_timer = None
        self._publish("sessions", [{"upsert": self.wire()}])

    def _on_line(self, line: str) -> None:
        if self._stopping:
            return
        fold = self.fold()
        events = fold.parse(self.harness.src, line)
        if events and all(isinstance(ev, Delta) for ev in events):
            # Never stored: the part's closing update carries the whole text.
            self._publish(self.channel, fold.live(events))
            self._set(
                activity=fold.activity(),
                **({"status": "working"} if self.status == "idle" else {}),
            )
            return
        self._record({"src": self.harness.src, "line": line}, events)
        changes: dict[str, object] = {}
        tasks = len(self.open_tasks)
        for ev in events:
            if isinstance(ev, TURN_BEARING) and self.status == "idle":
                changes["status"] = "working"
            usage = getattr(ev, "usage", None)
            if usage is not None and getattr(ev, "parent", None) is None:
                changes["context_tokens"] = usage.context
            if isinstance(ev, Title) and not self.title_set:
                changes["title"] = ev.text
            if isinstance(ev, Init):
                if ev.model:
                    changes["model_id"] = ev.model
                if ev.session_id:
                    changes["resume_id"] = ev.session_id
            if isinstance(ev, Notice) and ev.task_id:
                if ev.subtype == "task_started":
                    self.open_tasks.add(ev.task_id)
                elif ev.subtype == "task_notification":
                    self.open_tasks.discard(ev.task_id)
            if isinstance(ev, Result):
                if self._interrupt_timer:
                    self._interrupt_timer.cancel()
                changes["status"] = "idle"
                if ev.cost_usd is not None:
                    changes["cost_usd"] = ev.cost_usd
                if ev.context_window:
                    changes["context_window"] = ev.context_window
        if changes:
            self._set(**changes)
        if len(self.open_tasks) != tasks:
            self._publish_now()  # waiting on a background task shows on the card
            self._host.status_changed(self)
        if any(isinstance(ev, Result) for ev in events):
            if self.held:
                self._flushing = True
                asyncio.get_running_loop().create_task(self._flush_held())
            self._host.turn_ended(self)

    def _on_exit(self, code: int, stderr_tail: list[str]) -> None:
        if self._stopping:
            return
        self._proc = None
        self.open_tasks.clear()
        self._record(
            {
                "kind": "exit",
                "code": code,
                "stderr_tail": stderr_tail,
                "harness": self.harness.label,
            }
        )
        self._set(status="stopped")
        self._host.exited(self, code, stderr_tail)

    def _on_error(self, text: str, idle: bool, line: str | None = None) -> None:
        if self._stopping:
            return
        rec: dict = {"kind": "harness_error", "text": text}
        if line:
            rec["line"] = line
        self._record(rec)
        if idle and self.status == "working":
            self._set(status="idle")
            self._host.turn_ended(self)


def _inbox_text(batch: list[dict]) -> str:
    """Held messages as one prompt, in arrival order, each under its header."""
    return "\n\n".join(f"{m['header']}\n{m['body']}".rstrip() for m in batch)
