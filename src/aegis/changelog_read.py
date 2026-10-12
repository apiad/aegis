"""The release notes, read from the installed package, for /whats-new.

A tour must describe the code that runs it, so the notes ship inside the wheel:
pyproject.toml force-includes CHANGELOG.md as ``aegis/CHANGELOG.md``, next to
this file. A source checkout has no copy there, and neither does an editable
install (``uv sync`` leaves a stale one in site-packages, which nothing reads),
so the checkout's own CHANGELOG.md, two directories up, is the fallback. That
is the file the tests and ``bin/aegis-dev`` read.

Releases compare as numbers, so 2.10.0 is newer than 2.9.0. ``[Unreleased]``
has no number and is never picked: it describes code nobody has installed.
"""

from __future__ import annotations

import re
from pathlib import Path

PACKAGED = Path(__file__).parent / "CHANGELOG.md"
SOURCE = Path(__file__).parents[2] / "CHANGELOG.md"

_RELEASE = re.compile(r"^## \[(\d+(?:\.\d+)*)\]", re.M)
_HEADING = re.compile(r"^## ", re.M)
_NUMBER = re.compile(r"\d+(?:\.\d+)*")


def changelog_text() -> str | None:
    """The shipped CHANGELOG.md, else the checkout's; None when neither exists."""
    for path in (PACKAGED, SOURCE):
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return None


def _number(version: str | None) -> tuple[int, ...] | None:
    if version is None or not _NUMBER.fullmatch(version):
        return None
    return tuple(int(x) for x in version.split("."))


def sections_between(text: str, after: str | None, upto: str) -> str:
    """The ``## [x.y.z]`` sections with ``after < x.y.z <= upto``, oldest first,
    each from its heading to the next ``## ``, joined by a blank line. With
    ``after`` None, or not a version, only ``upto``'s own section. Empty when
    nothing matches, including an ``upto`` the notes do not have."""
    top = _number(upto)
    if top is None:
        return ""
    low = _number(after)
    picked: list[str] = []
    for m in _RELEASE.finditer(text):
        v = tuple(int(x) for x in m.group(1).split("."))
        if v > top or (v != top if low is None else v <= low):
            continue
        end = _HEADING.search(text, m.end())
        picked.append(text[m.start() : end.start() if end else len(text)].rstrip())
    return "\n\n".join(reversed(picked))
