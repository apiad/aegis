"""The operations open to agents, which the MCP endpoint projects as tools.

Agents can read and message anything they can see, and change only what they
created: their own monitors, their own session's names, the tasks they
enqueued (the vision's security model). People can do anything.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from . import files
from .monitors import iso_now
from .names import valid_handle
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
    # Required but nullable: an optional field was left out by 44 of 51 monitors
    # (#165), most of them CI waits whose checks could be counted.
    progress: str | None = Field(
        description="Bash; prints 0 to 100 for the bar on your card. Always give one: "
        "count what is finished over the total (CI checks or jobs done, files "
        "written, test lines passed), or as a last resort elapsed seconds over the "
        "expected duration, capped at 99. Pass null only when nothing about the "
        "process can be counted or estimated."
    )
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


class Sender(_Strict):
    handle: str
    server: str
    user: str


class Deliver(_Strict):
    target: str
    context: str = Field(min_length=1)
    interrupt: bool = False
    sender: Sender


class Read(_Strict):
    target: str = Field(description="A session's handle.")
    last: int = Field(30, ge=1, le=200, description="How many entries back.")
    tools: bool = Field(False, description="Include tool calls.")


class FileSend(_Strict):
    path: str = Field(
        min_length=1,
        description="The file's absolute path. A relative one resolves against "
        "your session's working directory, not your shell's last cd.",
    )
    caption: str | None = Field(
        None, description="A line of Markdown shown above the file."
    )


class PlanItem(_Strict):
    text: str = Field(min_length=1)
    state: Literal["pending", "doing", "done"]


class PlanUpdate(_Strict):
    items: list[PlanItem] = Field(
        description="The whole plan, every time, in order. Mark one item `doing` "
        "while you work on it and `done` when it is finished."
    )

    @model_validator(mode="after")
    def _one_doing(self) -> "PlanUpdate":
        if sum(1 for i in self.items if i.state == "doing") > 1:
            raise ValueError("mark one item `doing` at a time")
        return self


Reply = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[^\n]+$")]


class TurnEnd(_Strict):
    attention: Literal["needs_you", "review", "done"] = Field(
        description="needs_you: your message asks the person something. review: it "
        "presents something for them to read. done: it reports finished work."
    )
    line: str = Field(
        min_length=1,
        max_length=140,
        pattern=r"^[^\n]+$",
        description="One sentence: the question they must answer, what to read, or what got done.",
    )
    replies: list[Reply] = Field(
        default_factory=list,
        max_length=3,
        description="Up to three messages the person might send next, written as they "
        "would type them: their language, lowercase, no final period. Only when you "
        "laid out options or wait for a go-ahead; empty otherwise.",
    )


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
    if kind in ("thinking", "recap"):
        # A recap is aegis talking to the person; another agent never reads it.
        return None
    if kind == "file":
        return (
            f"file: {e['title']} ({e['summary'].split(' · ')[0]}) {e['detail']['url']}"
        )
    if kind == "artifact":
        return f"artifact: {e['title']} ({e['status']})"
    return f"· {e['summary']}"


ACROSS = (
    "an agent on this server reaches a linked server only by handing off to a "
    "session there (peer_handoff to handle@server); reading, spawning and "
    "enqueueing on another server are for people"
)


# What an agent here may learn of a linked server, all of it composed here from
# values checked here: the far server may be hostile, so none of its text is
# passed on (DESIGN.md, "A link is a client").
FAR_STATES = frozenset({"idle", "working", "stopped", "error"})
FAR_ERRORS = {
    "no_session": "no open session {handle}@{server}",
    "archived": "{handle}@{server} is archived",
    "server_offline": "{server} is not linked right now",
    "timeout": "{server} did not answer in time",
}
USER = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def split_address(target: str, own: str) -> tuple[str, str | None]:
    """``knuth@vps`` as ``("knuth", "vps")``; a bare handle, or this server's
    own name, as ``(handle, None)``."""
    handle, at, server = target.partition("@")
    return (handle, None) if not at or server == own else (handle, server)


def register_agent_ops(app: App) -> None:
    r = app.registry
    reg = app.sessions

    def own(caller: Caller):
        if not caller.is_agent or caller.log_id not in reg.sessions:
            raise OpError("agents_only", "only an agent's session can do this")
        return reg.sessions[caller.log_id]

    def here(target: str) -> str:
        """The handle in an address that must be on this server."""
        handle, server = split_address(target, app.server_name)
        if server is not None:
            raise OpError(
                "not_across_links", f"{target} is on another server; {ACROSS}"
            )
        return handle

    def target(handle: str):
        handle = here(handle)
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
        or the timeout passes. `progress` feeds the bar a person watches: measure it
        whenever anything can be counted. Returns at once; end your turn after
        calling it. The reply lists your other live monitors: cancel any you no
        longer need."""
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
        here(p.queue)
        if p.queue not in app.queues.queues:
            known = ", ".join(sorted(app.queues.queues)) or "none configured"
            raise OpError("unknown_queue", f"no queue {p.queue!r}; queues: {known}")
        if err := app.queues.queues[p.queue].get("error"):
            raise OpError("bad_config", f"queue {p.queue!r} in .aegis.yaml: {err}")
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
        interrupt cuts that turn first. `handle@server` reaches a session on a
        server this one links; nothing there can reach you back."""
        handle, server = split_address(p.target, app.server_name)
        if server is not None:
            return await far_handoff(handle, server, p, caller)
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

    async def far_handoff(handle: str, server: str, p: Handoff, caller: Caller):
        link = app.links.get(server)
        if link is None:
            raise OpError(
                "unknown_server", f"this server links no server named {server}"
            )
        me = reg.sessions.get(caller.log_id) if caller.log_id else None
        sender = {
            "handle": me.handle if me else "user",
            "server": app.server_name,
            "user": app.user,
        }
        try:
            r = await link.call(
                "peer.deliver",
                {
                    "target": handle,
                    "context": p.context,
                    "interrupt": p.interrupt,
                    "sender": sender,
                },
            )
        except OpError as e:
            if e.code in FAR_ERRORS:
                raise OpError(
                    e.code, FAR_ERRORS[e.code].format(handle=handle, server=server)
                ) from None
            raise OpError("far_error", f"{server} refused the handoff") from None
        held = isinstance(r, dict) and r.get("held") is True
        return f"{'held for' if held else 'landed at'} {handle}@{server}"

    @r.op("peer.deliver", Deliver)
    async def peer_deliver(p: Deliver, caller):
        """A handoff from an agent on a server that links this one. Only a link
        socket may call it, and only in the name of the server it linked as."""
        if caller.link is None or p.sender.server != caller.link:
            raise OpError(
                "not_a_link", "only a link, for its own server, delivers handoffs"
            )
        # The header is one line a person and an agent trust: nothing in it may
        # start another.
        sender_ok = valid_handle(p.sender.handle) or p.sender.handle == "user"
        if not (sender_ok and USER.match(p.sender.user)):
            raise OpError(
                "bad_sender", "a sender is a handle and a user name, one line each"
            )
        to = target(p.target)
        if p.interrupt and to.status == "working":
            await to.interrupt()
        held = to.busy
        who = f"agent:{p.sender.handle}@{p.sender.server} ({p.sender.user})"
        try:
            await to.deliver(f"> from {who} · {iso_now()}", p.context)
        except Archived as e:
            raise OpError("archived", f"{p.target} is archived") from e
        return {"held": held}

    @r.op("peer.read", Read, agent=True)
    async def peer_read(p: Read, caller):
        """The last entries of another session's transcript, one line each."""
        s = target(p.target)
        lines = [x for e in s.entries()[-p.last * 3 :] if (x := _render(e, p.tools))]
        return "\n".join(lines[-p.last :]) or "(nothing yet)"

    @r.op("session.list", NoArgs, agent=True)
    async def session_list(_, caller):
        """The open sessions on this server, then each linked server's by handle
        and state alone, as `handle@server`: reach one with peer_handoff."""
        out = [
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
        if caller.link is None:  # a link asking is not relayed further
            out += await far_sessions()
        return out

    async def far_sessions() -> list[dict]:
        """Every linked server's open sessions, cut to handle and state: a title
        is text an agent over there wrote, and nothing written there reaches an
        agent here (links.py)."""
        out = []
        for link in app.links.up():
            try:
                listed = await link.call("session.list")
            except OpError:
                continue
            for e in listed if isinstance(listed, list) else []:
                if not isinstance(e, dict) or "server" in e:
                    continue
                handle, state = e.get("handle"), e.get("state")
                ok = isinstance(handle, str) and valid_handle(handle)
                if ok and state in FAR_STATES:
                    out.append({"handle": handle, "server": link.name, "state": state})
        return out

    @r.op("file.send", FileSend, agent=True)
    async def file_send(p: FileSend, caller):
        """Hand a file to the person: it shows in your transcript with a preview
        when the browser can draw one, and Open and Download links. The file is
        copied, so later changes to it are not seen."""
        s = own(caller)
        path = Path(p.path).expanduser()
        relative = not path.is_absolute()
        if relative:
            path = s.spec.cwd / path
        try:
            rec = await asyncio.to_thread(files.store, app.roots.state_root, path)
        except files.FileError as e:
            # Agents write files after a cd in the shell, which does not carry
            # over, then send the bare name (seen in Alex's first smoke test).
            hint = (
                f"; a relative path resolves against your session's working "
                f"directory, {s.spec.cwd}; pass the file's absolute path"
                if relative and e.code == "not_found"
                else ""
            )
            raise OpError(e.code, e.message + hint) from e
        s.record_file({**rec, "caption": p.caption})
        return {
            "url": files.url(rec["file_id"], rec["name"]),
            "name": rec["name"],
            "size": rec["size"],
            "mime": rec["mime"],
        }

    @r.op("plan.update", PlanUpdate, agent=True)
    async def plan_update(p: PlanUpdate, caller):
        """Keep your plan where the person can see it: on your tab's card and in
        its sidebar. Send the whole list each time."""
        s = own(caller)
        items = [{"text": i.text[:120], "state": i.state} for i in p.items[:30]]
        s.report({"kind": "plan", "items": items})
        done = sum(1 for i in items if i["state"] == "done")
        return f"plan saved: {done} of {len(items)} done"

    @r.op("turn.end", TurnEnd, agent=True)
    async def turn_end(p: TurnEnd, caller):
        """Call this before the final message of every turn you hand back to the
        person, and not when you end a turn to wait on a monitor or a queue task.
        It sets the mark on your tab and the line on your card."""
        s = own(caller)
        s.report(
            {
                "kind": "turn_end",
                "attention": p.attention,
                "line": p.line,
                "replies": p.replies,
            }
        )
        return "noted"

    @r.op("meta", NoArgs, agent=True)
    async def meta(_, caller):
        """How aegis works for you, and its tools."""
        s = reg.sessions.get(caller.log_id or "")
        from .mcp import primer

        tools = ", ".join(op.tool_name for op in r.agent_ops())
        head = primer(s, reg.server_name) if s else "aegis"
        return f"{head}\n\nTools: {tools}."
