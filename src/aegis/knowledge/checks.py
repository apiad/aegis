"""Expiry and staleness flags on a memory.

``expired``: a temporary memory past its ``expires`` day. Search already
leaves it out of the hint the day it passes; the flag says why.

``stale:<path>``: a path the memory quotes in backticks that is not there.
A token counts as a path only when it starts with ``./``, ``../`` or ``~/``,
or with ``/`` and has a second part or an extension, or holds a ``/`` and
its last part has a file extension; and it holds no space, no URL scheme
and no placeholder (``<x>``, ``{x}``, ``$X``, a glob). So a bare file name,
``origin/main``, ``text/html``, ``and/or`` and a slash command such as
``/remember`` are never looked for: each would flag memories that are fine.
A relative path is looked for under each of the memory's ``paths:`` (a glob
cut at its first wild part), then under its ``cwd:``, the working directory
of the session that saved it, and then under each of the ``roots`` the
caller gives (the principal's Files root and home). A stale
memory stays in the hint: a missing file is not proof the fact is false.
Only the first ``MAX_PATHS`` paths of a memory are looked for.

``flags`` does no I/O itself: ``exists`` is the caller's, which in
multi-user mode runs as the member (as_user.py), so the check sees what the
member sees and nothing more.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from datetime import date

from .items import Item

TICKED = re.compile(r"`([^`\n]+)`")
FENCED = re.compile(r"^(```|~~~).*?^\1", re.S | re.M)
# A last part with a file extension: a name, a dot, letters or digits.
EXTENSION = re.compile(r"[^./]\.[A-Za-z0-9]{1,10}$")
# The most paths one memory has checked: a pasted listing must not turn one
# check into thousands of file reads.
MAX_PATHS = 200
NOT_A_PATH = re.compile(r"\s|://|[<>{}$*?\[\]|;()=,'\"]")


def quoted_paths(body: str) -> list[str]:
    """The tokens in single backticks that look like paths, in order, once
    each. Fenced code blocks are skipped: they hold commands and output."""
    seen: list[str] = []
    for token in TICKED.findall(FENCED.sub("", body)):
        token = token.strip().rstrip(".:")
        if not token or NOT_A_PATH.search(token) or not looks_like_path(token):
            continue
        if token.strip("/.~") and token not in seen:
            seen.append(token)
    return seen


def looks_like_path(token: str) -> bool:
    if token.startswith(("./", "../", "~/")):
        return True
    last = token.rstrip("/").rsplit("/", 1)[-1]
    if token.startswith("/"):
        # ``/remember`` is a command more often than a top-level directory.
        return "/" in token.strip("/") or bool(EXTENSION.search(last))
    return "/" in token and bool(EXTENSION.search(last))


def _base(glob: str) -> str:
    """A ``paths:`` entry cut at its first wild part: ``repos/bot/*`` is
    ``repos/bot``."""
    parts = []
    for part in glob.rstrip("/").split("/"):
        if any(c in part for c in "*?["):
            break
        parts.append(part)
    return "/".join(parts) or ("/" if glob.startswith("/") else "")


def candidates(
    token: str, paths: Iterable[str], roots: list[str], cwd: str | None = None
) -> list[str]:
    """Where ``token`` may be, in the order they are tried."""
    if token.startswith("/"):
        return [token]
    if token.startswith("~/"):
        return [os.path.join(r, token[2:]) for r in roots[-1:]]
    out = []
    for p in paths:
        base = _base(p)
        if not base:
            continue
        if base.startswith("~/"):
            out += [os.path.join(r, base[2:], token) for r in roots[-1:]]
        elif base.startswith("/"):
            out.append(os.path.join(base, token))
        else:
            out += [os.path.join(r, base, token) for r in roots]
    if cwd and os.path.isabs(cwd):
        out.append(os.path.join(cwd, token))
    out += [os.path.join(r, token) for r in roots]
    return list(dict.fromkeys(os.path.normpath(p) for p in out))


def flags(
    item: Item,
    today: date,
    exists: Callable[[str], bool],
    roots: list[str] | None = None,
) -> list[str]:
    """``expired`` and one ``stale:<path>`` per quoted path found nowhere.
    ``roots`` are tried after the memory's own ``paths:`` and ``cwd:``, the
    home last, which a ``~/`` path is relative to. A skill has no flags."""
    if item.kind != "memory":
        return []
    out = ["expired"] if item.expired(today) else []
    cwd = item.meta.get("cwd")
    cwd = cwd if isinstance(cwd, str) else None
    for token in quoted_paths(item.body)[:MAX_PATHS]:
        where = candidates(token, item.paths, roots or [], cwd)
        if not any(exists(c) for c in where):
            out.append(f"stale:{token}")
    return out
