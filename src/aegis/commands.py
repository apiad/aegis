"""The composer's slash lines, resolved on the server.

A line that starts with ``/`` names an aegis command, which runs an operation
and sends nothing to ``claude``, or one of the session's harness commands, which
goes to ``claude`` as typed. ``//rest`` is a prompt: it is sent as `` /rest``,
because Claude Code runs a line as a command only when the slash comes first.
Any other ``/`` line is refused before it costs a turn: Claude answers an
unknown command with the model. Without a catalog to check against, harness
names pass through.

The harness part of the list comes from ``initialize`` (``claude/control.py``)
and is kept in memory by cwd, since commands come from the cwd's ``.claude/``
and the user's config. A cwd with no live process is probed once. Nothing goes
to disk, so an upgraded CLI never meets a stale list.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from .claude import control
from .claude.control import Catalog
from .session import Session, SpawnSpec


@dataclass(frozen=True)
class Command:
    name: str
    hint: str
    doc: str
    args: str = ""  # what the menu completes: "models", "efforts" or "permissions"


AEGIS: dict[str, Command] = {
    c.name: c
    for c in (
        Command("model", "<name>", "Switch this session's model", "models"),
        Command(
            "effort", "<level>", "Switch this session's reasoning effort", "efforts"
        ),
        Command(
            "permission",
            "<mode>",
            "Switch what the agent may do without asking",
            "permissions",
        ),
        Command("rename", "<handle>", "Rename this session's handle"),
        Command("title", "<text>", "Set this session's title"),
        Command("stop", "", "Stop the process and keep the session"),
        Command("close", "", "Close the session; it stays in the archive"),
        # The browser answers it by opening the menu; it never reaches claude.
        Command("help", "", "List every command"),
    )
}


def split(text: str) -> tuple[str, str] | None:
    """``/name rest`` as ``(name, rest)``; None for a prompt."""
    if not text.startswith("/") or text.startswith("//"):
        return None
    parts = text[1:].split(maxsplit=1)
    if not parts:
        return None
    return parts[0], parts[1].strip() if len(parts) > 1 else ""


def escape(text: str) -> str:
    return " " + text[1:] if text.startswith("//") else text


def aegis_wire() -> list[dict]:
    return [
        {
            "name": c.name,
            "hint": c.hint,
            "doc": c.doc,
            "source": "aegis",
            "args": c.args,
        }
        for c in AEGIS.values()
    ]


class Catalogs:
    """Each cwd's catalog, from a live process's ``initialize`` or a probe.

    A cwd whose ``claude`` gives no catalog (a CLI without ``initialize``, or
    one that timed out) is remembered as such until a process there answers,
    and ``get`` returns None: callers then pass harness names through, as
    before slash commands existed, rather than refuse every one. Concurrent
    lookups for one cwd share a single probe, so a burst of keystrokes after a
    restart starts one ``claude``, not one per key."""

    def __init__(self, claude_bin: str, stderr_path: Path) -> None:
        self._claude_bin = claude_bin
        self._stderr = stderr_path
        self._by_cwd: dict[str, Catalog] = {}
        self._failed: set[str] = set()
        self._probing: dict[str, asyncio.Task[Catalog | None]] = {}

    def put(self, cwd: Path, catalog: Catalog) -> None:
        self._by_cwd[str(cwd)] = catalog
        self._failed.discard(str(cwd))

    async def get(self, s: Session) -> Catalog | None:
        t = s.catalog_task
        if t is not None:
            await asyncio.wait([t])
            done = None if t.cancelled() else t.result()
            if done is not None:
                return done
            if not t.cancelled():
                self._failed.add(str(s.spec.cwd))  # the live process gave none
        return await self.for_spec(s.spec)

    async def for_spec(self, sp: SpawnSpec) -> Catalog | None:
        """The catalog of ``sp.cwd``, probed with ``sp``'s flags if no process
        there has given one: the new-tab composer asks before any session
        exists."""
        key = str(sp.cwd)
        if key in self._by_cwd:
            return self._by_cwd[key]
        if key in self._failed:
            return None
        probe = self._probing.get(key)
        if probe is None:
            probe = asyncio.create_task(self._probe(sp))
            self._probing[key] = probe
            probe.add_done_callback(lambda _: self._probing.pop(key, None))
        return await asyncio.shield(probe)

    async def _probe(self, sp: SpawnSpec) -> Catalog | None:
        try:
            cat = await control.probe(
                self._claude_bin,
                sp.model,
                sp.effort,
                sp.permission,
                sp.cwd,
                self._stderr,
            )
        except (control.ControlError, TimeoutError, OSError):
            self._failed.add(str(sp.cwd))
            return None
        self.put(sp.cwd, cat)
        return cat
