"""The terminals' operations (#362, terminals.py).

Phase 1 gives them to people only: an agent's term_* tools, and the access
levels that gate them, come with phase 2. A member reads every terminal and
types into, resizes, restarts and closes only their own, through ops.py's
``owned=`` check, as with sessions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .app import App


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class TermSpawn(_Strict):
    profile: str
    cwd: str | None = None  # a folder under the root, from Files' Terminal here


class TermRef(_Strict):
    id: str


class TermInput(TermRef):
    data: str = Field(max_length=1_000_000)


class TermResize(TermRef):
    cols: int = Field(ge=2, le=1000)
    rows: int = Field(ge=1, le=500)


def register_term_ops(app: App) -> None:
    r, terms = app.registry, app.terminals

    @r.op("term.profiles")
    async def profiles(_, caller):
        """The terminal profiles the + picker offers, the built-in shell first."""
        keys = ("cmd", "cwd", "restart", "error")
        return [
            {"name": n, **{k: p.get(k) for k in keys}}
            for n, p in terms.profiles().items()
        ]

    @r.op("term.spawn", TermSpawn)
    async def spawn(p: TermSpawn, caller):
        """Start a terminal from a profile, in its folder or in ``cwd``; the
        caller owns it."""
        t = await terms.spawn(p.profile, owner=app.who(caller).name, cwd=p.cwd)
        return terms.card(t)

    @r.op("term.input", TermInput, owned="id")
    async def input_(p: TermInput, caller):
        terms.get(p.id).input(p.data)

    @r.op("term.resize", TermResize, owned="id")
    async def resize(p: TermResize, caller):
        terms.get(p.id).resize(p.cols, p.rows)

    @r.op("term.restart", TermRef, owned="id")
    async def restart(p: TermRef, caller):
        await terms.restart(p.id)

    @r.op("term.close", TermRef, owned="id")
    async def close(p: TermRef, caller):
        await terms.close(p.id)

    @r.op("term.closed")
    async def closed(_, caller):
        """Closed terminals on this server, newest first, for the archive."""
        return terms.closed()
