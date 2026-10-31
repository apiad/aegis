"""A repository's know-how: its ``know-how/*.md`` menu, once per session.

The docs stay in the repository's git tree: they are reviewed there, reach
every contributor and differ by branch, none of which holds for the per-person
store. aegis keeps no copy and ranks nothing. Ranking on a message put the doc
an agent went on to read in the top 3 no more often than 3 random docs of that
repository, since messages are short and the need shows up later in the turn;
so the agent gets the whole menu and chooses.

A session is told about a repository when it first touches it: its working
directory at the first send, and any path a tool call of its own names after
that. The menu is owed like a notice (``pending``), so it rides on a send or a
tool call the same way. ``Session.know_how_sent`` names the main checkouts it
was told about, so a worktree counts with its main checkout, a restart repeats
nothing and a compaction, which can drop the menu, tells it again.

A cloned repository's text reaches the agent with no approval, so each
description is one flattened, cut line behind the doc's path and can never look
like a message header. Files are read as the session's Linux user, off the
event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .. import launch
from ..journal import paths, shell
from ..transcript.entries import NOTICE_HEADER

if TYPE_CHECKING:
    from ..session import Session

log = logging.getLogger("aegis.know_how")

FOLDER = "know-how"
MAX_DOCS = 50
DESCRIPTION_CHARS = 200
HEAD_BYTES = 4096
FIRST_SEND_WAIT_S = 2.0
MAX_WORDS = 20
DOC_NAME = re.compile(r"^[\w.-]+\.md$")
FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*$", re.S | re.M)
WHEN = re.compile(r"^when:[ \t]*(.*)$", re.M)
BOLD_WHEN = re.compile(r"\*\*When to reach for this:\*\*[ \t]*(.*)")
HEADING = re.compile(r"^#+[ \t]+(.*)$", re.M)
PATH_KEYS = ("file_path", "notebook_path", "path", "cwd", "workdir")
PATH_WORD = re.compile(r"[^\s'\"`=:;|&()<>]*/[^\s'\"`=:;|&()<>]*")


def describe(text: str) -> str:
    """When to read a doc: its frontmatter's ``when:``, else a ``**When to
    reach for this:**`` line, else its first heading. One line, cut."""
    front = FRONTMATTER.match(text)
    when = WHEN.search(front.group(1)) if front else None
    found = when or BOLD_WHEN.search(text) or HEADING.search(text)
    line = " ".join(found.group(1).split()) if found else ""
    return line[:DESCRIPTION_CHARS]


def read_docs(top: str, user: str | None) -> tuple[list[tuple[str, str]], int]:
    """(name, description) of the first docs in ``top``'s know-how folder, and
    how many it holds in all. A folder that cannot be read holds none."""
    folder = os.path.join(top, FOLDER)
    try:
        r = launch.run(["ls", "-1", folder], user=user, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return [], 0
    if r.returncode != 0:
        return [], 0
    names = sorted(n for n in r.stdout.splitlines() if DOC_NAME.match(n))
    docs = []
    for name in names[:MAX_DOCS]:
        try:
            r = launch.run(
                ["head", "-c", str(HEAD_BYTES), os.path.join(folder, name)],
                user=user,
                text=True,
                errors="replace",
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            docs.append((name, describe(r.stdout)))
    return docs, len(names)


def block(main: str, top: str, cwd: str, docs: list[tuple[str, str]], n: int) -> str:
    """The menu an agent reads: paths it can open from where it runs."""
    # A list, so the transcript's Markdown keeps one doc to a line.
    head = (
        f"{NOTICE_HEADER}{FOLDER} · {os.path.basename(main)}\n"
        "This repository's procedure docs. Read those that match your task "
        "before you act:\n\n"
    )
    lines = []
    for name, description in docs:
        path = os.path.join(top, FOLDER, name)
        near = os.path.relpath(path, cwd)
        path = path if near.startswith("..") else near
        lines.append(f"- {path}: {description}" if description else f"- {path}")
    if n > len(docs):
        lines.append(f"- … and {n - len(docs)} more in {FOLDER}/")
    return head + "\n".join(lines)


def candidates(tool_input: dict, cwd: str) -> list[str]:
    """The paths a tool call names: its path arguments, and for a shell command
    the directory it runs in and its words holding a ``/``."""
    out = [
        os.path.join(cwd, v)
        for k in PATH_KEYS
        if isinstance(v := tool_input.get(k), str) and v
    ]
    command = tool_input.get("command") or tool_input.get("cmd")
    if isinstance(command, str):
        base = shell.leading_cd(command, cwd) or cwd
        out.append(base)
        out += [os.path.join(base, w) for w in PATH_WORD.findall(command)[:MAX_WORDS]]
    return out


@dataclass
class _Seen:
    """One session's walk: dropped at a compaction and when it closes."""

    cwd: str
    started: bool = False
    dirs: set[str] = field(default_factory=set)
    empty: set[str] = field(default_factory=set)  # main checkouts with no docs
    owed: dict[str, str] = field(default_factory=dict)  # main checkout -> block


class KnowHow:
    def __init__(self, user_of: Callable[[str], str | None]) -> None:
        self._user_of = user_of
        self._seen: dict[str, _Seen] = {}

    def _state(self, s: "Session") -> _Seen:
        if s.log_id not in self._seen:
            self._seen[s.log_id] = _Seen(str(s.spec.cwd))
        return self._seen[s.log_id]

    def touched(self, s: "Session", tool_input: dict) -> None:
        """A tool call of the session's own: look at the directories it names
        for the first time, off the loop. On the loop, so it never blocks."""
        st = self._state(s)
        command = tool_input.get("command")
        new = []
        for p in candidates(tool_input, st.cwd):
            d = os.path.normpath(p)
            if d not in st.dirs:
                st.dirs.add(d)
                new.append(d)
        # Only Claude Code's Bash keeps its directory between calls.
        if isinstance(command, str) and s.harness.src == "claude":
            st.cwd = shell.leading_cd(command, st.cwd) or st.cwd
        if new:
            asyncio.get_running_loop().create_task(self._look_and_steer(s, new))

    async def _look_and_steer(self, s: "Session", found: list[str]) -> None:
        try:
            await self._look(s, found)
        except Exception:
            log.exception("%s: could not read know-how", s.handle)
            return
        if self._state(s).owed:
            s._steer_owed()

    async def ready(self, s: "Session") -> None:
        """Before the session's first send: its working directory's menu, so a
        session started in a repository has it on its first message."""
        st = self._state(s)
        if st.started:
            return
        st.started = True
        d = os.path.normpath(st.cwd)
        st.dirs.add(d)
        try:
            await asyncio.wait_for(self._look(s, [d]), FIRST_SEND_WAIT_S)
        except TimeoutError:
            log.warning("%s: know-how took too long at its first send", s.handle)

    async def _look(self, s: "Session", found: list[str]) -> None:
        st = self._state(s)
        user = self._user_of(s.log_id)
        known = {*s.know_how_sent, *st.owed, *st.empty}

        def read() -> dict[str, tuple[str, list[tuple[str, str]], int]]:
            out: dict[str, tuple[str, list[tuple[str, str]], int]] = {}
            for p in found:
                main, _, top = paths.place(p, user)
                if main is None or top is None or main in known or main in out:
                    continue
                docs, n = read_docs(top, user)
                out[main] = (top, docs, n)
            return out

        for main, (top, docs, n) in (await asyncio.to_thread(read)).items():
            if not docs:
                st.empty.add(main)
            elif main not in s.know_how_sent and main not in st.owed:
                st.owed[main] = block(main, top, str(s.spec.cwd), docs, n)

    def pending(self, s: "Session") -> str:
        """The menus the session is owed, marked told, joined by a blank line;
        "" when there are none."""
        st = self._seen.get(s.log_id)
        if st is None or not st.owed:
            return ""
        owed, st.owed = st.owed, {}
        s.know_how_sent = [*s.know_how_sent, *owed]
        try:
            s._metas.write_soon(s.log_id, s.meta)
        except OSError:  # a full disk keeps the in-memory record (#395)
            log.warning("aegis: could not record know-how for %s", s.handle)
        return "\n\n".join(owed.values())

    def reset(self, s: "Session") -> None:
        """Its context was compacted: the next touch of a repository tells it
        again."""
        self._seen.pop(s.log_id, None)
        s.know_how_sent = []

    def drop(self, log_id: str) -> None:
        self._seen.pop(log_id, None)
