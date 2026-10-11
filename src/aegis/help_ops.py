"""The help pages as agent tools: ``help.search`` and ``help.read``.

The index is built once, when the app registers its operations, from the pages
in the installed package (``helppages.py``), so a server answers with the help
for exactly the code it runs. Both only read, so a session with read
permission has them too. An empty search returns the page list rather than
nothing, so an agent always has somewhere to go next. ``help.pages`` is that
list for the browser's Help tab, and only for a person.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .helppages import Index, load
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
