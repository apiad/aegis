"""The knowledge operations: memory tools for agents, and stats.

Every op works on the caller's own principal (``App.who``): an agent's is its
session's owner, so a member's agent never reaches another member's items, and
a name that belongs to someone else answers ``not_found`` as a name that does
not exist. Reads are open to read-permission sessions, as help_read is; saves
and deletes are not. A save is one commit in the principal's store
(knowledge/store.py). In this slice every save is approved; proposals for
memories an agent saves on its own initiative come with the approval card.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from .knowledge.items import Item, check_name, dump
from .ops import Caller, OpError

if TYPE_CHECKING:
    from .app import App


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class Search(_Strict):
    query: str = Field(
        min_length=1,
        description="What you need to know, in your own words, such as "
        "'which host runs the bot'.",
    )
    limit: int = Field(5, ge=1, le=20, description="How many memories to return.")


class ReadMemories(_Strict):
    names: list[str] = Field(
        min_length=1,
        max_length=10,
        description="Memory names, from the hint or from memory_search.",
    )


class SaveMemory(_Strict):
    name: str = Field(
        description="Lowercase letters, digits, - and _, such as 'deploy-host'. "
        "Saving an existing memory's name replaces it."
    )
    description: str = Field(
        min_length=1,
        max_length=200,
        description="One line that says what the memory is about; the hint and "
        "search show it.",
    )
    body: str = Field(
        min_length=1,
        description="The fact in Markdown. For feedback or a project, add "
        "**Why:** and **How to apply:** lines. Link another item as [[name]].",
    )
    type: Literal["user", "feedback", "project", "reference"] = Field(
        "reference",
        description="user: who the person is; feedback: how they want you to "
        "work; project: ongoing work or a constraint; reference: a pointer.",
    )
    lifetime: Literal["permanent", "temporary"] = Field(
        "permanent", description="temporary needs expires."
    )
    expires: date | None = Field(
        None, description="The day a temporary memory stops being true."
    )
    paths: list[str] = Field(
        default_factory=list,
        description="Directory globs where the memory matters most; its hint "
        "ranks higher in sessions working there.",
    )
    asked: bool = Field(
        False,
        description="true when the person asked you to remember this "
        "(/remember, 'remember that ...').",
    )


class DeleteMemory(_Strict):
    name: str


class Stats(_Strict):
    unused_days: int | None = Field(
        None,
        ge=1,
        description="Only items not read, invoked or run in this many days.",
    )
    kind: Literal["skill", "memory"] | None = None


def remember_turn(text: str, now: str) -> tuple[str, str]:
    """The turn /remember delivers: the agent saves the text as a memory."""
    return (
        f"> from remember · {now}",
        f"The person typed: /remember {text}\n\n"
        "Save this as one memory with memory_save and asked: true. Choose a "
        "short name, a one-line description and the type; make it temporary "
        "with expires when it names a date or an event that will pass. Then "
        "say in one line what you saved.",
    )


def register_knowledge_ops(app: App) -> None:
    r = app.registry
    kn = app.knowledge

    def principal(caller: Caller) -> str:
        return app.who(caller).name

    def author(caller: Caller) -> str:
        if caller.is_agent:
            s = app.sessions.sessions.get(caller.log_id or "")
            return f"session {s.handle if s else caller.log_id}"
        return principal(caller)

    def memory(owner: str, name: str) -> Item:
        check_name(name)
        it = kn.store.get(owner, name)
        if it is None or it.kind != "memory":
            raise OpError("not_found", f"no memory named {name!r}")
        return it

    @r.op("memory.search", Search, agent=True, read=True)
    async def memory_search(p: Search, caller):
        """Search your memories (facts about the person, their projects and how
        they want you to work) with your own words. Returns the best names with
        their description, score, status and flags; read one with memory_read.
        Proposed and expired memories are included and marked."""
        hits = (await kn.index(principal(caller))).rank(p.query, "memory", p.limit)
        return {
            "hits": [
                {
                    "name": h.name,
                    "description": h.description,
                    "score": round(h.score, 3),
                    "status": h.status,
                    "flags": h.flags,
                }
                for h in hits
            ]
        }

    @r.op("memory.read", ReadMemories, agent=True, read=True)
    async def memory_read(p: ReadMemories, caller):
        """Read one or more memories in full, by name: the ones the hint on
        your message names, or memory_search's hits."""
        owner = principal(caller)
        items = [memory(owner, n) for n in p.names]
        session = caller.log_id or ""
        for it in items:
            rank = kn.read_rank(session, it.name)
            kn.usage.add(
                owner,
                session,
                "memory",
                it.name,
                "read",
                f"rank={rank}" if rank else None,
            )
        return {"memories": [{"name": it.name, "text": dump(it)} for it in items]}

    @r.op("memory.save", SaveMemory, agent=True)
    async def memory_save(p: SaveMemory, caller):
        """Save a memory: one fact that later sessions should know, such as
        who the person is, how they want you to work, or where something
        runs. Saving an existing name replaces that memory. Set asked: true
        when the person asked you to remember it."""
        owner = principal(caller)
        check_name(p.name)
        if p.lifetime == "temporary" and p.expires is None:
            raise OpError("bad_params", "a temporary memory needs expires")
        held = kn.store.get(owner, p.name)
        created = (
            held.meta.get("created")
            if held is not None and held.kind == "memory"
            else None
        ) or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta = {
            "type": p.type,
            "lifetime": p.lifetime,
            "expires": p.expires if p.lifetime == "temporary" else None,
            "status": "approved",
            "paths": p.paths,
            "created": created,
            "source": author(caller),
        }
        meta = {k: v for k, v in meta.items() if v is not None}
        it = Item("memory", p.name, p.description.strip(), p.body.rstrip() + "\n", meta)
        await kn.store.save(owner, it, author(caller))
        kn.usage.add(owner, caller.log_id or "", "memory", p.name, "saved")
        return {"saved": p.name, "status": "approved"}

    @r.op("memory.delete", DeleteMemory, agent=True)
    async def memory_delete(p: DeleteMemory, caller):
        """Delete a memory that is no longer true. The deletion is a commit in
        the store's history and can be restored."""
        owner = principal(caller)
        memory(owner, p.name)
        sha = await kn.store.delete(owner, p.name, author(caller))
        kn.usage.add(owner, caller.log_id or "", "memory", p.name, "deleted")
        return {"deleted": p.name, "sha": sha}

    @r.op("knowledge.stats", Stats, agent=True, read=True)
    async def knowledge_stats(p: Stats, caller):
        """How your skills and memories are used: per item, how often it was
        hinted, read, invoked or run, and when it was last used. unused_days
        keeps only the items not used in that many days."""
        owner = principal(caller)
        used = await asyncio.to_thread(kn.usage.summary, owner)
        cutoff = time.time() - p.unused_days * 86400 if p.unused_days else None
        out = []
        for it in await kn.items(owner):
            if p.kind and it.kind != p.kind:
                continue
            u = used.get(it.name, {})
            last = u.get("last_used")
            if cutoff is not None and last is not None and last >= cutoff:
                continue
            out.append(
                {
                    "kind": it.kind,
                    "name": it.name,
                    "description": it.description,
                    "status": it.status,
                    "flags": ["expired"] if it.expired() else [],
                    **{k: u.get(k, 0) for k in ("hinted", "read", "invoked", "ran")},
                    "last_used": last,
                }
            )
        return {"items": out}
