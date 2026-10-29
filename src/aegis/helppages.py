"""The help pages: Markdown under ``help/``, shipped in the package, and the
index that agents and people search.

A page is one file whose stem is its id. It opens with a frontmatter block of
three keys: ``title``, ``kind`` (concept, howto or tutorial) and ``when``, one
line saying when to read it, and ``hints:``, one to three items of at most
90 characters that the new tab rotates, each optionally ending in ``#slug``,
a section of the same page that a click opens. Every ``## `` heading outside a code fence starts
a section, addressed as ``page#slug``; the text before the first heading is the
page's first section, addressed as the bare page id.

Search is BM25 over the sections, stdlib only, built once from the files. About
fifty pages are a corpus keyword search handles, and an embedding model would
cost a server on a small host memory it does not have. A section's own words,
its heading counted twice, are scored with length normalisation. Its page's
title, counted three times, and ``when`` line, counted twice, are scored
without it, so they decide which page answers and the section's words decide
which part of it. Nothing is written to disk.

This module imports nothing from the app, so the tests read the pages exactly
as the server does.
"""

from __future__ import annotations

import difflib
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

HELP_DIR: Path = Path(__file__).parent / "help"
KINDS = ("concept", "howto", "tutorial")
K1, B = 1.2, 0.75
TITLE_WEIGHT, WHEN_WEIGHT = 3, 2
MAX_HINTS, HINT_CHARS = 3, 90
FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Section:
    page: str
    slug: str
    heading: str
    text: str


@dataclass(frozen=True)
class Page:
    id: str
    title: str
    kind: str
    when: str
    body: str
    sections: tuple[Section, ...]
    # The new tab's hints: (text, ref), ref being ``page`` or ``page#slug``.
    hints: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Hit:
    ref: str
    heading: str
    when: str
    snippet: str


def slug(heading: str) -> str:
    """``Wait on a process`` as ``wait-on-a-process``."""
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


def parse(id: str, text: str) -> Page:
    """One page from its file's text. A missing key, an unknown kind or two
    sections with one slug is an error naming the file, so a bad page fails
    the tests and the server's boot, never a search."""
    m = FRONT.match(text)
    if m is None:
        raise ValueError(f"{id}.md: no frontmatter (--- title, kind, when ---)")
    keys: dict[str, str] = {}
    items: list[str] = []
    for line in m.group(1).splitlines():
        if line.startswith("  - ") and "hints" in keys:
            items.append(line[4:].strip())
            continue
        key, sep, value = line.partition(":")
        if sep:
            keys[key.strip()] = value.strip()
    for key in ("title", "kind", "when"):
        if not keys.get(key):
            raise ValueError(f"{id}.md: the frontmatter has no {key}:")
    if keys["kind"] not in KINDS:
        raise ValueError(
            f"{id}.md: kind {keys['kind']!r} is not one of {', '.join(KINDS)}"
        )
    body = text[m.end() :].strip("\n") + "\n"
    sections = _sections(id, keys["title"], body)
    slugs = [s.slug for s in sections]
    if len(set(slugs)) != len(slugs):
        twice = sorted({s for s in slugs if slugs.count(s) > 1})
        raise ValueError(f"{id}.md: two sections slug to {', '.join(twice)}")
    hints = _hints(id, items, set(slugs))
    return Page(id, keys["title"], keys["kind"], keys["when"], body, sections, hints)


def _hints(id: str, items: list[str], slugs: set[str]) -> tuple[tuple[str, str], ...]:
    if not 1 <= len(items) <= MAX_HINTS:
        raise ValueError(
            f"{id}.md: hints: needs 1 to {MAX_HINTS} items, has {len(items)}"
        )
    out = []
    for item in items:
        text, ref = item, id
        head, sep, tail = item.rpartition(" #")
        if sep:
            if tail not in slugs:
                raise ValueError(
                    f"{id}.md: hint names #{tail}, no section of this page"
                )
            text, ref = head.strip(), f"{id}#{tail}"
        if len(text) > HINT_CHARS:
            raise ValueError(
                f"{id}.md: a hint is {len(text)} characters, at most {HINT_CHARS}: {text}"
            )
        out.append((text, ref))
    return tuple(out)


def _sections(page: str, title: str, body: str) -> tuple[Section, ...]:
    out: list[Section] = []
    heading, key, lines, fenced = title, "", [], False
    for line in body.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            out.append(Section(page, key, heading, "\n".join(lines).strip()))
            heading = line[3:].strip()
            key, lines = slug(heading), []
            continue
        lines.append(line)
    out.append(Section(page, key, heading, "\n".join(lines).strip()))
    return tuple(out)


def load(dir: Path = HELP_DIR) -> list[Page]:
    """Every page in ``dir``, sorted by id."""
    return [
        parse(p.stem, p.read_text(encoding="utf-8")) for p in sorted(dir.glob("*.md"))
    ]


def _stem(word: str) -> str:
    """A crude suffix strip, so ``waiting`` finds ``wait`` and ``queues`` finds
    ``queue``. Applied to pages and queries alike, so it only has to agree
    with itself."""
    for suffix in ("ing", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            if suffix == "s" and word.endswith("ss"):
                break
            return word[: -len(suffix)]
    return word


def terms(text: str) -> list[str]:
    return [_stem(w) for w in WORD.findall(text.lower())]


def snippet(text: str, query: set[str], width: int = 240) -> str:
    """Two lines of a section: the first that holds a query term, and the next."""
    lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.startswith("```")
    ]
    start = next((i for i, ln in enumerate(lines) if query & set(terms(ln))), 0)
    out = " ".join(lines[start : start + 2])
    return out if len(out) <= width else out[: width - 1] + "…"


def _subsection(text: str, want: str) -> str | None:
    """The ``### `` heading of ``text`` whose slug is ``want``, with its lines
    up to the next ``### `` or the end; a heading inside a fence is code."""
    out: list[str] | None = None
    fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            fence = not fence
        elif not fence and line.startswith("### "):
            if out is not None:
                break
            if slug(line[4:]) == want:
                out = []
        if out is not None:
            out.append(line)
    return "\n".join(out).strip() + "\n" if out else None


class Index:
    def __init__(self, pages: list[Page]) -> None:
        self.pages: dict[str, Page] = {p.id: p for p in pages}
        # A section with no text of its own is never a hit: there is nothing
        # in it to read.
        self._sections = [s for p in pages for s in p.sections if s.text]
        # Each section's own words, and its page's title and when line, which
        # every section of the page shares. They are scored apart: the page's
        # words decide which page answers, the section's which part of it.
        self._body: list[Counter[str]] = []
        self._page: list[Counter[str]] = []
        for s in self._sections:
            page = self.pages[s.page]
            # A heading names what its section is about: it counts twice.
            self._body.append(Counter(terms(s.heading) * 2 + terms(s.text)))
            shared: Counter[str] = Counter()
            for t in terms(page.title):
                shared[t] += TITLE_WEIGHT
            for t in terms(page.when):
                shared[t] += WHEN_WEIGHT
            self._page.append(shared)
        self._len = [sum(tf.values()) for tf in self._body]
        self._avg = sum(self._len) / len(self._len) if self._len else 1.0
        df = Counter(
            t for body, page in zip(self._body, self._page) for t in body.keys() | page
        )
        n = len(self._body)
        self._idf = {t: math.log((n - d + 0.5) / (d + 0.5) + 1) for t, d in df.items()}

    def search(self, query: str, limit: int = 5) -> list[Hit]:
        """The best sections for ``query``, best first; none when no word of
        the query is in any page."""
        q = set(terms(query))
        scored: list[tuple[float, int]] = []
        for i, (body, page) in enumerate(zip(self._body, self._page)):
            norm = K1 * (1 - B + B * self._len[i] / self._avg)
            score = 0.0
            for t in q:
                if t in body:
                    score += self._idf[t] * body[t] * (K1 + 1) / (body[t] + norm)
                if t in page:
                    score += self._idf[t] * page[t] * (K1 + 1) / (page[t] + K1)
            if score > 0:
                scored.append((-score, i))
        scored.sort()
        return [self._hit(self._sections[i], q) for _, i in scored[:limit]]

    def _hit(self, s: Section, q: set[str]) -> Hit:
        ref = f"{s.page}#{s.slug}" if s.slug else s.page
        return Hit(ref, s.heading, self.pages[s.page].when, snippet(s.text, q))

    def read(self, page: str, section: str | None = None) -> str:
        """A page's Markdown under its title, or one section under its heading.
        ``page`` may be a hit's ref (``monitors#wait-on-a-process``), and
        ``section`` a slug or the heading itself; given both, ``section``
        wins."""
        if "#" in page:
            page, ref = page.split("#", 1)
            section = section or ref
        p = self.pages.get(page)
        if p is None:
            near = difflib.get_close_matches(page, list(self.pages), n=3, cutoff=0)
            raise KeyError(f"no help page {page!r}; the closest are {', '.join(near)}")
        if not section:
            return f"# {p.title}\n\n{p.body}"
        want = slug(section)
        for s in p.sections[1:]:
            if s.slug == want:
                return f"## {s.heading}\n\n{s.text}\n"
        # A ### heading inside a section (#760): a guide names one as often.
        for s in p.sections:
            if (sub := _subsection(s.text, want)) is not None:
                return sub
        have = ", ".join(s.slug for s in p.sections[1:])
        raise KeyError(f"{page} has no section {section!r}; its sections are {have}")

    def hints(self) -> list[dict]:
        """Every page's hints as ``{text, ref}``, by page id."""
        return [
            {"text": text, "ref": ref}
            for _, p in sorted(self.pages.items())
            for text, ref in p.hints
        ]

    def listing(self) -> list[dict]:
        """Every page's id, title, kind and when, by kind then title."""
        order = {k: i for i, k in enumerate(KINDS)}
        return [
            {"id": p.id, "title": p.title, "kind": p.kind, "when": p.when}
            for p in sorted(self.pages.values(), key=lambda p: (order[p.kind], p.title))
        ]
