"""Changelog fragments — one file per change, collated at release.

`CHANGELOG.md` was the only file that conflicted on every PR. Every change
appended to the top of the same list under ``## [Unreleased]``, so two agents
working in parallel wrote adjacent lines in one hunk, which is the one thing
git cannot merge. Three PRs in one afternoon, three conflicts, and nothing
else in the tree collided once.

A fragment is a file, so two PRs never touch the same bytes and git has
nothing to merge. The category lives in the filename, which also removes the
second failure this file had: placing a bullet correctly meant reading the
whole release section, and inserting at "the first matching heading" produced
two ``### Changed`` blocks under one release.

No ``towncrier``. Collation is a group-by and a concatenation, and this repo
already prefers a script it can read over a dependency it cannot.

Spec: ``docs/superpowers/specs/2026-09-27-changelog-fragments-design.md``
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Render order, and the only accepted categories. Keep a Changelog's
#: vocabulary minus the ones this project has never used, plus
#: ``performance``, which it has. A category nothing renders is a fragment
#: that disappears from the release notes, so the set is closed and
#: ``parse_fragment_name`` refuses anything outside it.
CATEGORIES: tuple[str, ...] = (
    "added",
    "changed",
    "deprecated",
    "removed",
    "fixed",
    "performance",
    "security",
)

#: Heading each category renders as, matching what CHANGELOG.md already uses.
_HEADING = {c: f"### {c.capitalize()}" for c in CATEGORIES}

_UNRELEASED = "## [Unreleased]"

# A fragment filename: <slug>.<category>.md. The slug is free-form (an issue
# number and a few words, by convention) and is never rendered — it exists to
# make the file unique and the PR readable.
_NAME = re.compile(r"^(?P<slug>.+)\.(?P<category>[a-z]+)\.md$")


class FragmentError(ValueError):
    """A fragment that would be silently dropped from the release notes.

    Loud rather than skipped, on purpose: a fragment that does not parse is a
    change whose entry vanishes, which is the failure this whole mechanism
    exists to prevent.
    """


@dataclass(frozen=True)
class Fragment:
    slug: str
    category: str
    body: str  # the bullet(s), verbatim, no trailing newline


def parse_fragment_name(name: str) -> tuple[str, str]:
    """``("10-thing", "changed")`` from ``10-thing.changed.md``."""
    m = _NAME.match(name)
    if m is None:
        raise FragmentError(
            f"{name!r} is not a fragment name — use <slug>.<category>.md, "
            f"e.g. 10-changelog-fragments.changed.md"
        )
    category = m.group("category")
    if category not in CATEGORIES:
        raise FragmentError(
            f"{name!r} has unknown category {category!r}; "
            f"use one of: {', '.join(CATEGORIES)}"
        )
    return m.group("slug"), category


def read_fragments(directory: Path) -> list[Fragment]:
    """Every fragment in ``directory``, in a stable order.

    Sorted by filename so two runs over the same directory produce the same
    CHANGELOG — a release diff that reorders itself is a release diff nobody
    reads.
    """
    if not directory.is_dir():
        return []
    out: list[Fragment] = []
    for path in sorted(directory.iterdir()):
        if path.name == "README.md" or not path.is_file():
            continue
        if not path.name.endswith(".md"):
            continue
        slug, category = parse_fragment_name(path.name)
        body = path.read_text().strip()
        if not body:
            raise FragmentError(f"{path.name} is empty — write the bullet or delete it")
        out.append(Fragment(slug=slug, category=category, body=body))
    return out


def _render(fragments: list[Fragment]) -> dict[str, list[str]]:
    by_category: dict[str, list[str]] = {}
    for f in fragments:
        by_category.setdefault(f.category, []).append(f.body)
    return by_category


def _section_bounds(text: str, start: int) -> int:
    """Index of the next ``## `` heading at or after ``start``, or len(text)."""
    m = re.compile(r"^## ", re.M).search(text, start)
    return m.start() if m else len(text)


def collate(
    changelog: str,
    fragments: list[Fragment],
    *,
    version: str | None = None,
    date: str | None = None,
) -> str:
    """Fold ``fragments`` into ``changelog`` and return the new text.

    Without ``version`` they land under ``## [Unreleased]``, joining a
    category section that is already there rather than opening a second one.
    With ``version`` they become a new ``## [x.y.z] - date`` section directly
    below Unreleased, which is where a release wants them.

    Whatever was already written is never rewritten or reordered: fragments
    govern changes from here on, and the entries above them were hand-placed.
    An empty fragment list returns the input byte for byte, so running this
    twice is not a diff.
    """
    if not fragments:
        return changelog
    by_category = _render(fragments)

    if version is not None:
        block = [f"## [{version}] - {date}", ""]
        for category in CATEGORIES:
            if category not in by_category:
                continue
            block.append(_HEADING[category])
            block.append("")
            for body in by_category[category]:
                block.append(body)
                block.append("")
        at = changelog.index(_UNRELEASED)
        at = _section_bounds(changelog, at + len(_UNRELEASED))
        return changelog[:at] + "\n".join(block) + "\n" + changelog[at:]

    text = changelog

    # Two passes, because they want opposite iteration orders.
    #
    # First, categories that already have a section under [Unreleased]: append
    # after that section's last entry, so one release never grows two
    # identical headings — the failure that had to be undone by hand in #9.
    for category in CATEGORIES:
        if category not in by_category:
            continue
        heading = _HEADING[category]
        start = text.index(_UNRELEASED)
        end = _section_bounds(text, start + len(_UNRELEASED))
        existing = text.find(f"\n{heading}\n", start, end)
        if existing == -1:
            continue
        at = _section_bounds_of_h3(text, existing + 1, end)
        text = text[:at] + "\n\n".join(by_category.pop(category)) + "\n\n" + text[at:]

    # Then the new sections, each slotted into CATEGORIES order beside the
    # sections already there. Not simply at the top: `performance` sorts after
    # `changed`, and dropping every new section above the existing ones put the
    # real file out of the order its own header claims to follow. Nothing
    # existing moves — the new heading is placed around it.
    for category in CATEGORIES:
        if category not in by_category:
            continue
        block = (
            f"{_HEADING[category]}\n\n" + "\n\n".join(by_category[category]) + "\n\n"
        )
        start = text.index(_UNRELEASED) + len(_UNRELEASED)
        end = _section_bounds(text, start)
        text = (
            text[: _slot_for(text, category, start, end)]
            + block
            + text[_slot_for(text, category, start, end) :]
        )
    return text


def _slot_for(text: str, category: str, start: int, end: int) -> int:
    """Where a new ``### <category>`` heading belongs within [start, end).

    Before the first existing section whose category sorts after this one; at
    the end of the release's sections when there is none.
    """
    rank = CATEGORIES.index(category)
    for other in CATEGORIES[rank + 1 :]:
        at = text.find(f"\n{_HEADING[other]}\n", start, end)
        if at != -1:
            return at + 1
    # Nothing sorts after it: go last, just inside this release's bounds.
    tail = end
    while tail > start and text[tail - 1] == "\n":
        tail -= 1
    return tail + 2 if tail + 2 <= end else end


def _section_bounds_of_h3(text: str, start: int, limit: int) -> int:
    """End of the ``### `` section beginning at ``start``, bounded by limit."""
    m = re.compile(r"^(### |## )", re.M).search(text, start + 1)
    return min(m.start() if m else limit, limit)
