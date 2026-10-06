"""The operations open to agents, which the MCP endpoint projects as tools.

Agents can read and message anything they can see, and change only what they
created: their own monitors, their own session's names, the tasks they
enqueued (the vision's security model). People can do anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .monitors import iso_now
from .ops import Caller, OpError
from .session import Archived

if TYPE_CHECKING:
    from .app import App


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class MonitorStart(_Strict):
    description: str = Field(
        min_length=1,
        description="What is being waited on, shown on the card and in the wake.",
    )
    done: str = Field(min_length=1, description="Bash; exit 0 means finished.")
    fail: str | None = Field(None, description="Bash; exit 0 means failed.")
    progress: str | None = Field(None, description="Bash; prints 0 to 100.")
    interval_s: float = Field(10, ge=1, le=3600)
    timeout_s: float = Field(3600, gt=0, le=7 * 86400)
    cwd: str | None = Field(
        None, description="Where conditions run; relative to your session's cwd."
    )


class MonitorId(_Strict):
    monitor_id: str


class Enqueue(_Strict):
    queue: str
    payload: str = Field(
        min_length=1, description="The worker's first prompt; self-contained."
    )
    callback: bool = Field(
        True, description="Deliver the worker's result to your inbox."
    )


class TaskId(_Strict):
    task_id: str


class Handoff(_Strict):
    target: str = Field(description="The receiving session's handle.")
    context: str = Field(min_length=1)
    interrupt: bool = Field(False, description="Cut the target's current turn first.")


class Read(_Strict):
    target: str = Field(description="A session's handle.")
    last: int = Field(30, ge=1, le=200, description="How many entries back.")
    tools: bool = Field(False, description="Include tool calls.")


class NoArgs(_Strict):
    pass


def _render(e: dict, tools: bool) -> str | None:
    kind = e["kind"]
    md = (e.get("md") or "").strip()
    if kind == "user":
        return f"user: {md}"
    if kind == "prose":
        return f"agent: {md}"
    if kind == "inbox":
        return f"from {e['title']}: {md.split(chr(10), 1)[1] if chr(10) in md else ''}".rstrip()
    if kind == "tool":
        if not tools:
            return None
        result = e["detail"].get("result") or e["status"]
        return f"tool {e['title']} {e['summary']} -> {result}"
    if kind == "thinking":
        return None
    return f"· {e['summary']}"


def register_agent_ops(app: App) -> None:
    r = app.registry
    reg = app.sessions

    def own(caller: Caller):
        if not caller.is_agent or caller.log_id not in reg.sessions:
            raise OpError("agents_only", "only an agent's session can do this")
        return reg.sessions[caller.log_id]

    def target(handle: str):
        s = reg.by_handle(handle) or reg.sessions.get(handle)
        if s is None:
            if any(m.get("handle") == handle for m in reg.archived.values()):
                raise OpError("archived", f"{handle} is archived")
            raise OpError("no_session", f"no open session {handle!r}")
        return s

    def task(task_id: str):
        t = app.queues.tasks.get(task_id)
        if t is None:
            raise OpError("no_task", f"no task {task_id!r}")
        return t

    def mine(t, caller: Caller):
        if caller.is_agent and t.enqueuer != caller.log_id:
            raise OpError("not_yours", "agents can change only the tasks they enqueued")

    def roster(owner: str, but: str | None = None) -> list[dict]:
        return [
            {"monitor_id": m.id, "description": m.description}
            for m in app.monitors.of(owner)
            if m.id != but
        ]

    @r.op("monitor.start", MonitorStart, agent=True)
    async def monitor_start(p: MonitorStart, caller):
        """Watch a long-running process without polling: bash conditions run every
        interval_s, and you are woken through your inbox when `done` or `fail` exits 0
        or the timeout passes. Returns at once; end your turn after calling it. The
        reply lists your other live monitors: cancel any you no longer need."""
        s = own(caller)
        cwd = Path(p.cwd).expanduser() if p.cwd else s.spec.cwd
        if not cwd.is_absolute():
            cwd = s.spec.cwd / cwd
        m = app.monitors.start(
            s.log_id,
            str(cwd),
            description=p.description,
            done=p.done,
            fail=p.fail,
            progress=p.progress,
            interval_s=p.interval_s,
            timeout_s=p.timeout_s,
        )
        return {"monitor_id": m.id, "other_live_monitors": roster(s.log_id, m.id)}

    @r.op("monitor.cancel", MonitorId, agent=True)
    async def monitor_cancel(p: MonitorId, caller):
        """Stop one of your monitors. No wake is sent."""
        m = app.monitors.items.get(p.monitor_id)
        if m is None:
            raise OpError("no_monitor", f"no live monitor {p.monitor_id!r}")
        if caller.is_agent and m.owner != caller.log_id:
            raise OpError("not_yours", "agents can cancel only their own monitors")
        app.monitors.cancel(m.id)
        return {"cancelled": m.id, "remaining": roster(m.owner)}

    @r.op("monitor.list", NoArgs, agent=True)
    async def monitor_list(_, caller):
        """Your live monitors."""
        items = (
            app.monitors.of(caller.log_id)
            if caller.is_agent
            else list(app.monitors.items.values())
        )
        return [
            {
                "monitor_id": m.id,
                "description": m.description,
                "progress": m.last_progress,
            }
            for m in items
        ]

    @r.op("queue.enqueue", Enqueue, agent=True)
    async def queue_enqueue(p: Enqueue, caller):
        """Hand a self-contained task to a fresh worker on a queue. Returns at once
        with the task id; the worker's final message reaches your inbox when it
        finishes (with callback). Keep working meanwhile."""
        if p.queue not in app.queues.queues:
            known = ", ".join(sorted(app.queues.queues)) or "none configured"
            raise OpError("unknown_queue", f"no queue {p.queue!r}; queues: {known}")
        enqueuer = own(caller) if caller.is_agent else None
        cwd = str(enqueuer.spec.cwd if enqueuer else app.roots.harness_cwd)
        t = await app.queues.enqueue(
            p.queue, p.payload, p.callback, enqueuer.log_id if enqueuer else None, cwd
        )
        return {"task_id": t.id, "status": t.status, "position": app.queues.position(t)}

    @r.op("task.status", TaskId, agent=True)
    async def task_status(p: TaskId, caller):
        """A task's status, its worker, and its result once finished."""
        return app.queues.status(task(p.task_id))

    @r.op("task.cancel", TaskId, agent=True)
    async def task_cancel(p: TaskId, caller):
        """Cancel a task you enqueued: drop it if pending, stop and archive its worker if running."""
        t = task(p.task_id)
        mine(t, caller)
        await app.queues.cancel(t)
        return app.queues.status(t)

    @r.op("task.resume", TaskId, agent=True)
    async def task_resume(p: TaskId, caller):
        """Put a failed or cancelled task's worker back to work, with its conversation intact."""
        t = task(p.task_id)
        mine(t, caller)
        if t.status not in ("failed", "cancelled"):
            raise OpError("not_resumable", f"the task is {t.status}")
        try:
            await app.queues.resume(t)
        except LookupError as e:
            raise OpError("no_worker", str(e)) from e
        return app.queues.status(t)

    @r.op("peer.handoff", Handoff, agent=True)
    async def peer_handoff(p: Handoff, caller):
        """Give another session context or an instruction; it arrives as a user turn
        headed `> from agent:<you>`. A busy target gets it when its turn ends, unless
        interrupt cuts that turn first."""
        to = target(p.target)
        sender = (
            f"agent:{reg.sessions[caller.log_id].handle}"
            if caller.is_agent and caller.log_id in reg.sessions
            else "user"
        )
        if p.interrupt and to.status == "working":
            await to.interrupt()
        held = to.busy
        try:
            await to.deliver(f"> from {sender} · {iso_now()}", p.context)
        except Archived as e:
            raise OpError("archived", f"{p.target} is archived") from e
        return f"{'held for' if held else 'landed at'} {to.handle}"

    @r.op("peer.read", Read, agent=True)
    async def peer_read(p: Read, caller):
        """The last entries of another session's transcript, one line each."""
        s = target(p.target)
        lines = [x for e in s.entries()[-p.last * 3 :] if (x := _render(e, p.tools))]
        return "\n".join(lines[-p.last :]) or "(nothing yet)"

    @r.op("session.list", NoArgs, agent=True)
    async def session_list(_, caller):
        """The open sessions on this server."""
        return [
            {
                "handle": s.handle,
                "title": s.title,
                "state": s.status,
                "cwd": str(s.spec.cwd),
                "worker": s.worker,
                "you": caller.log_id == s.log_id,
            }
            for s in reg.open_sessions()
        ]

    @r.op("meta", NoArgs, agent=True)
    async def meta(_, caller):
        """How aegis works for you, and its tools."""
        s = reg.sessions.get(caller.log_id or "")
        from .mcp import primer

        tools = ", ".join(op.tool_name for op in r.agent_ops())
        head = primer(s, reg.server_name) if s else "aegis"
        return f"{head}\n\nTools: {tools}."
