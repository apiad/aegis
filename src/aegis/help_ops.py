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
so the quota gate and ownership apply to a guide as to any session. The
tutorial counts as taken once the guide of a tutorial page runs: a spawn
that fails marks nothing, and neither does a tour of another page or a
question asked in the Help tab, so the new tab keeps offering the tutorial. A
title that cannot be set leaves the guide untitled rather than failing a call
whose session already exists.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from .changelog_read import changelog_text, sections_between
from .helppages import Index, load
from .helpstate import HelpState, installed, newer
from .names import TITLE_MAX
from .ops import OpError

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger("aegis.help_ops")


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
        "step by step so I learn aegis. Talk to me in my language.\n\n"
        'Open your first turn with a welcome, "Welcome to the aegis tutorial" in '
        "my language, and say in a few lines what the page covers: its levels or "
        "its steps, one line each. Tell me I can ask anything on the way, and that "
        "you will answer and then bring me back to the step we were on. Then start "
        "the first step.\n\n"
        "Go one step per turn and end each turn with turn_end. Ask me before any "
        "step that spawns agents, spends quota or reaches another server. When I "
        "ask about something else, answer it, then return to the step we were on "
        "and say which one it is. When the page ends, offer the tutorials it lists "
        "under Next, or suggest the next tutorial."
    )


def tour_prompt(page_id: str, title: str) -> str:
    """The first message of a guided tour of a page that is not a tutorial:
    the guide turns its sections into steps the person tries."""
    return (
        f"Read the help page `{page_id}` with help_read and give me a guided tour "
        "of it, so I learn to use it by trying it. Talk to me in my language. "
        f'Open with "Welcome to a guided tour of {title}" in my language and say '
        "in a few lines what we will try. Turn the page's sections into steps I "
        "do, one per turn, each saying what to do and what I should see, and end "
        "each turn with turn_end. Ask me before any step that spawns agents, "
        "spends quota or reaches another server. When I ask about something else, "
        "answer it, then return to the step we were on. When the page is done, "
        "suggest the tutorial or page to read next."
    )


def ask_prompt(question: str, page_id: str | None) -> str:
    """The first message of a question asked in the Help tab, with the page
    the person was reading, or none from the list."""
    where = (
        f"while reading the page `{page_id}`"
        if page_id
        else "while on the list of help pages"
    )
    quoted = "\n".join(f"  {line}" for line in question.strip().splitlines())
    return (
        f"I asked this in aegis's Help, {where}:\n\n{quoted}\n\n"
        "Answer it. Find the answer in the help pages with help_search and "
        "help_read before anything else, and say which page it came from. If "
        "showing me would help, offer to, and ask before anything that spawns "
        "agents or spends quota. Talk to me in my language and end each turn "
        "with turn_end."
    )


def ask_title(question: str) -> str:
    """'Help: ' and the question on one line, cut to fit a session title."""
    title = "Help: " + " ".join(question.split())
    return title if len(title) <= TITLE_MAX else title[: TITLE_MAX - 1] + "…"


WHATS_NEW = """\
aegis was upgraded. Below are the release notes since my last tour. Guide me
through them, simplest first, one change per turn, and end each turn with
turn_end. For each entry, read its help page with help_read and show me how to
use the feature, not only that it exists. Ask me before any step that spawns
agents, spends quota or reaches another server."""


def whats_new_prompt(notes: str) -> str:
    """A /whats-new guide's first message: the instructions, then the release
    sections the person has not toured, newest last."""
    return f"{WHATS_NEW}\n\n{notes}"


def whats_new_guide(
    text: str | None, installed: str | None, toured: str | None
) -> tuple[str, str]:
    """The title and prompt of a /whats-new guide.

    The sections after the last tour up to the installed release. When none
    is left, the installed release alone: a person who types /whats-new after
    touring this version wants it again, not a guide with nothing to say. An
    install with no notes for its version, a missing file or a build ahead of
    its changelog, is refused before anything spawns, even when older
    releases have notes the person has not toured."""
    own = sections_between(text, None, installed) if text and installed else ""
    if not own:
        raise OpError(
            "no_release_notes",
            f"this install has no release notes for {installed or 'its version'}, "
            "so there is nothing to tour",
        )
    notes = sections_between(text or "", toured, installed or "") or own
    return f"What's new in {installed}", whats_new_prompt(notes)


class DismissParams(BaseModel):
    model_config = {"extra": "forbid"}
    what: Literal["tutorial", "whats_new"]


class GuideParams(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["tutorial", "whats_new", "ask"]
    page: str | None = None
    # An ask's question, as the person typed it in the Help tab.
    question: str | None = Field(None, min_length=1, max_length=4000)
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
        than their last tour and has notes: a hint whose guide is refused
        would fail on every click."""
        _, rec = record(caller)
        v = installed()
        notes = sections_between(changelog_text() or "", None, v)
        return {
            "tutorial": rec["tutorial"],
            "whats_new": v if notes and newer(v, rec["toured"]) else None,
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
        """Start a guide session: a tutorial or a guided tour of a help page,
        a question asked in the Help tab, or a tour of the release notes since
        the person's last one. Spawn with the prompt built here, record what
        the person did, title it. A page's own kind picks the prompt: a
        tutorial page gets the tutorial's, any other page a tour. Returns the
        new session's log id and handle so the client can open its tab."""
        if p.kind == "whats_new":
            # Decided before the spawn, as for a tutorial; a missing release
            # refuses here, before anything starts or is recorded.
            name, rec = record(caller)
            version = installed()
            title, prompt = whats_new_guide(changelog_text(), version, rec["toured"])
            fields = p.model_dump(
                exclude={"kind", "page", "question"}, exclude_none=True
            )
            started = await r.call("session.spawn", fields | {"prompt": prompt}, caller)
            state.mark_toured(name, version)
            try:
                app.sessions.rename(started["log_id"], None, title)
            except OpError as e:
                log.warning("aegis: guide %s left untitled: %s", started["log_id"], e)
            return {"log_id": started["log_id"], "handle": started["handle"]}
        if p.kind == "ask":
            if not p.question:
                raise OpError("bad_params", "an ask needs a question")
            if p.page:
                try:
                    index.read(p.page)
                except KeyError as e:
                    raise OpError("unknown_page", e.args[0]) from None
            # Before the spawn, as for a tutorial: the asker's own session
            # must not make a new person look like one who was here before.
            record(caller)
            fields = p.model_dump(
                exclude={"kind", "page", "question"}, exclude_none=True
            )
            started = await r.call(
                "session.spawn",
                fields | {"prompt": ask_prompt(p.question, p.page)},
                caller,
            )
            title = ask_title(p.question)
        else:
            page_id = p.page or TUTORIAL_DEFAULT
            try:
                index.read(page_id)
            except KeyError as e:
                raise OpError("unknown_page", e.args[0]) from None
            page = index.pages[page_id]
            tutorial = page.kind == "tutorial"
            name, _ = record(caller)
            fields = p.model_dump(
                exclude={"kind", "page", "question"}, exclude_none=True
            )
            prompt = (
                tutorial_prompt(page_id)
                if tutorial
                else tour_prompt(page_id, page.title)
            )
            started = await r.call("session.spawn", fields | {"prompt": prompt}, caller)
            # Only a tutorial counts as the tutorial taken: a tour of a how-to
            # page leaves the new-tab hint on offer.
            if tutorial:
                state.mark_tutorial(name)
            title = f"{'Tutorial' if tutorial else 'Tour'}: {page.title}"
        try:
            app.sessions.rename(started["log_id"], None, title)
        except OpError as e:
            log.warning("aegis: guide %s left untitled: %s", started["log_id"], e)
        return {"log_id": started["log_id"], "handle": started["handle"]}
