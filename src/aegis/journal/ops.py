"""The journal's operations: journal.search and journal.note for agents, and
journal.rows for the browser's Journal view and sidebar row. Searches run in a
thread on a read-only connection; the writer thread owns writes."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from ..ops import OpError
from . import db, render, when
from .query import build, search

if TYPE_CHECKING:
    from ..app import App

Kind = Literal["turn", "commit", "pr", "plan", "note", "session"]


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class SearchParams(_Strict):
    since: str | None = Field(
        None,
        description="First day: 2026-10-09, today, yesterday, or 7d (the last 7 days).",
    )
    until: str | None = Field(
        None, description="Last day, included, in the same forms."
    )
    pattern: str | None = Field(
        None,
        description='Words or "quoted phrases" to find, OR between alternatives.',
    )
    path: str | None = Field(
        None,
        description="A file or directory: only entries that wrote under it. Relative to the aegis root (the directory holding .aegis.yaml) or absolute.",
    )
    session: str | None = Field(
        None, description="A session's handle, past or present, or its log id."
    )
    repo: str | None = Field(None, description="A repo's directory: only its entries.")
    kind: list[Kind] | None = Field(
        None,
        description=(
            "Only these kinds. turn: a turn that changed files or ended with a "
            "turn_end line; commit; pr; plan: a plan item turning done; note: a "
            "journal_note; session: spawned, renamed, closed."
        ),
    )
    limit: int = Field(50, ge=1, le=500)


class RowsParams(SearchParams):
    offset: int = Field(0, ge=0)
    counts: bool = False


class NoteParams(_Strict):
    text: str = Field(
        min_length=1,
        max_length=500,
        description="The decision, blocker or milestone, in a sentence or two.",
    )
    tag: Literal["decision", "blocker", "milestone"]
    paths: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Files or directories it is about, if any. A relative path is read from the session's working directory.",
    )


def register_journal_ops(app: "App") -> None:
    r = app.registry
    root = str(app.roots.config_root)

    def run(p: SearchParams, offset: int = 0, counts: bool = False):
        con = db.open_read(app.journal.path)
        try:
            try:
                q = build(
                    con,
                    root,
                    since=p.since,
                    until=p.until,
                    pattern=p.pattern,
                    path=p.path,
                    session=p.session,
                    repo=p.repo,
                    kinds=p.kind,
                    limit=p.limit,
                    offset=offset,
                )
            except ValueError as e:
                raise OpError("bad_params", str(e)) from None
            try:
                hits, cut = search(con, q)
                c = db.counts(con, q) if counts else None
            except db.BadPattern as e:
                raise OpError(
                    "bad_pattern", f"{p.pattern!r} is not a search pattern ({e})"
                ) from None
            return hits, cut, c
        finally:
            con.close()

    @r.op("journal.search", SearchParams, agent=True)
    async def journal_search(p: SearchParams, caller):
        """Ask what was done on this server: commits, pull requests, finished plan
        items, turns that changed files, and the decisions, blockers and
        milestones agents noted. Filter by days, a pattern, a path the entries
        wrote under, a session, a repo or kinds. Newest first, grouped by day. Each
        entry names the session's handle when it was made; peer_read reads a
        session while it is open, under its current handle, which an entry
        made before a rename names as "(now ...)"."""
        hits, cut, _ = await asyncio.to_thread(run, p)
        handles = {k: s.handle for k, s in app.sessions.sessions.items()}
        return render.text(hits, cut, root, handles)

    @r.op("journal.rows", RowsParams)
    async def journal_rows(p: RowsParams, caller):
        """The Journal view's rows: what journal.search finds, as fields to draw,
        and the server's day, which the view shows until the person picks one."""
        hits, cut, c = await asyncio.to_thread(run, p, p.offset, p.counts)
        out = {
            "rows": render.rows(hits, root, set(app.sessions.sessions)),
            "more": cut,
            "today": when.today(),
        }
        if c is not None:
            out["counts"] = c
        return out

    @r.op("journal.note", NoteParams, agent=True)
    async def journal_note(p: NoteParams, caller):
        """Record a decision, a blocker or a milestone in the journal. Commits,
        pull requests and finished work are journaled for you; note only what
        aegis cannot see."""
        s = app.sessions.sessions.get(caller.log_id) if caller.is_agent else None
        if s is None:
            raise OpError("agents_only", "only an agent's session can do this")
        s.report(
            {"kind": "journal_note", "text": p.text, "tag": p.tag, "paths": p.paths}
        )
        return "noted"
