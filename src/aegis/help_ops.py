"""The help pages as agent tools: ``help.search`` and ``help.read``.

The index is built once, when the app registers its operations, from the pages
in the installed package (``helppages.py``), so a server answers with the help
for exactly the code it runs. Both only read, so a session with read
permission has them too. An empty search returns the page list rather than
nothing, so an agent always has somewhere to go next. ``help.pages`` is that
list for the browser's Help tab, and only for a person.

help.state, help.dismiss and help.guide are the person's, never agent tools.
They read and write the caller's record in helpstate.py, deciding it before a
guide spawns so the guide's own session cannot make a new person look like a
returning one. help.guide spawns through session.spawn, the path /spawn takes,
so the quota gate and ownership apply to a guide as to any session.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from .helppages import Index, load
from .helpstate import HelpState, installed, newer
from .ops import OpError

if TYPE_CHECKING:
    from .app import App


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class Search(_Strict):
    query: str = Field(
        min_length=1,
        description="What you want to do or know, in a few words, such as "
        "'wait for CI without polling'.",
    )
    limit: int = Field(5, ge=1, le=20, description="How many sections to return.")


class Read(_Strict):
    page: str = Field(
        min_length=1,
        description="A page id, or a ref from help_search such as monitors#wait-on-ci.",
    )
    section: str | None = Field(
        None, description="A section's slug or heading; omitted: the whole page."
    )


TUTORIAL_DEFAULT = "getting-started"


def tutorial_prompt(page_id: str) -> str:
    """The first message of a /tutorial guide. The page carries the content;
    the prompt says only how to walk a person through it (spec, "Guide
    sessions"). Built here so a test can read it."""
    return (
        f"Read the help page `{page_id}` with help_read and guide me through it "
        "step by step so I learn aegis. Talk to me in my language, one step per "
        "turn, and end each turn with turn_end. Ask me before any step that "
        "spawns agents, spends quota or reaches another server. When the page "
        "ends, suggest the next tutorial."
    )


class DismissParams(BaseModel):
    model_config = {"extra": "forbid"}
    what: Literal["tutorial", "whats_new"]


class GuideParams(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["tutorial", "whats_new"]
    page: str | None = None
    # The fields session.spawn takes. None leaves each to the agent, and no
    # agent means default_agent; session.spawn validates them.
    agent: str | None = None
    harness: str | None = None
    model: str | None = None
    effort: str | None = None
    permission: str | None = None
    cwd: str | None = None


def register_help_ops(app: App) -> None:
    r = app.registry
    index = Index(load())

    @r.op("help.search", Search, agent=True, read=True)
    async def help_search(p: Search, caller):
        """Search aegis's help pages: how to use its tools, commands and
        features, written for the version this server runs. Returns the best
        sections as `page#section` refs, each with its heading, the page's
        `when` line and a snippet; read one with help_read. When nothing
        matches, returns every page's id, title and `when` line instead."""
        hits = index.search(p.query, p.limit)
        if not hits:
            return {"hits": [], "pages": index.listing()}
        return {"hits": [asdict(h) for h in hits]}

    @r.op("help.read", Read, agent=True, read=True)
    async def help_read(p: Read, caller):
        """Read a help page, or one section of it, as Markdown. `page` takes a
        page id or a ref from help_search."""
        try:
            return index.read(p.page, p.section)
        except KeyError as e:
            known = p.page.split("#", 1)[0] in index.pages
            raise OpError(
                "unknown_section" if known else "unknown_page", e.args[0]
            ) from None

    @r.op("help.pages")
    async def help_pages(_, caller):
        """Every help page for the Help tab's list: id, title, kind and the
        when: line, in the index's order. A person's operation; an agent
        searches instead."""
        return index.listing()

    state = HelpState(app.roots.state_root / "help")

    def record(caller) -> tuple[str, dict]:
        """The caller's principal and its record, decided on the first read.
        Whether the principal owns a session, live or archived, is asked of
        app.owner_of, which reads both and gives a session from before owners
        to the admin."""
        name = app.who(caller).name
        reg = app.sessions
        owns = any(app.owner_of(i) == name for i in (*reg.sessions, *reg.archived))
        return name, state.get(name, owns_sessions=owns, installed=installed())

    @r.op("help.state")
    async def help_state(_, caller):
        """What the new tab offers this person: the tutorial hint while they
        have started no tutorial, and the release to tour while it is newer
        than their last tour."""
        _, rec = record(caller)
        v = installed()
        return {
            "tutorial": rec["tutorial"],
            "whats_new": v if newer(v, rec["toured"]) else None,
        }

    @r.op("help.dismiss", DismissParams)
    async def help_dismiss(p: DismissParams, caller):
        """The cross on a hint, recorded as if the person had run it."""
        name, _ = record(caller)
        if p.what == "tutorial":
            state.mark_tutorial(name)
        else:
            state.mark_toured(name, installed())
        return "noted"

    @r.op("help.guide", GuideParams)
    async def help_guide(p: GuideParams, caller):
        """Start a guide session: spawn with the prompt built here, title it,
        record what the person did. Any page can be toured; the client offers
        tutorial pages. Returns the new session's log id and handle so the
        client can open its tab."""
        if p.kind == "whats_new":
            raise OpError("not_yet", "/whats-new guides are not in this build")
        page_id = p.page or TUTORIAL_DEFAULT
        try:
            index.read(page_id)
        except KeyError as e:
            raise OpError("unknown_page", e.args[0]) from None
        # Before the spawn: the guide's own session must not make a new person
        # look like one who was here before.
        name, _ = record(caller)
        fields = p.model_dump(exclude={"kind", "page"}, exclude_none=True)
        started = await r.call(
            "session.spawn", fields | {"prompt": tutorial_prompt(page_id)}, caller
        )
        app.sessions.rename(
            started["log_id"], None, f"Tutorial: {index.pages[page_id].title}"
        )
        state.mark_tutorial(name)
        return {"log_id": started["log_id"], "handle": started["handle"]}
