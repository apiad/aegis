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
from .ops import OpError
from .session import Session


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
        Command(
            "spawn",
            "<agent>[@server] [prompt]",
            "Start a session, here or on a linked server; flags: --model, "
            "--effort, --permission, --cwd",
        ),
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


SPAWN_FLAGS = ("model", "effort", "permission", "cwd")
SPAWN_USAGE = (
    "usage: /spawn <agent>[@server] [--model m] [--effort e] [--cwd path] [prompt]"
)


@dataclass(frozen=True)
class SpawnLine:
    agent: str
    server: str | None = None
    prompt: str | None = None
    model: str | None = None
    effort: str | None = None
    permission: str | None = None
    cwd: str | None = None


def parse_spawn(arg: str) -> SpawnLine:
    """``/spawn``'s argument: the agent, its ``@server``, leading flags, and the
    rest of the line as the prompt, verbatim (the legacy tree's command, with
    ``@server`` and ``--cwd`` added)."""
    rest = arg.strip()
    head, _, rest = rest.partition(" ")
    agent, at, server = head.partition("@")
    if not agent or (at and not server):
        raise OpError("bad_spawn", SPAWN_USAGE)
    flags: dict[str, str] = {}
    rest = rest.lstrip()
    while rest.startswith("--"):
        word, _, rest = rest.partition(" ")
        name = word[2:]
        if name not in SPAWN_FLAGS:
            raise OpError("bad_spawn", f"no flag --{name}; {SPAWN_USAGE}")
        value, _, rest = rest.lstrip().partition(" ")
        if not value:
            raise OpError("bad_spawn", f"--{name} needs a value; {SPAWN_USAGE}")
        flags[name] = value
        rest = rest.lstrip()
    return SpawnLine(agent, server or None, rest or None, **flags)


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
    """Each (harness, cwd)'s catalog, from a live process or a probe.

    A cwd whose ``claude`` gives no catalog (a CLI without ``initialize``, or
    one that timed out) is remembered as such until a process there answers,
    and ``get`` returns None: callers then pass harness names through, as
    before slash commands existed, rather than refuse every one. Concurrent
    lookups for one cwd share a single probe, so a burst of keystrokes after a
    restart starts one ``claude``, not one per key."""

    def __init__(self, stderr_path: Path) -> None:
        self._stderr = stderr_path
        self._by_key: dict[str, Catalog] = {}
        self._failed: set[str] = set()
        self._probing: dict[str, asyncio.Task[Catalog | None]] = {}

    @staticmethod
    def _key(harness: str, cwd: Path) -> str:
        return f"{harness}\0{cwd}"

    def put(self, harness: str, cwd: Path, catalog: Catalog) -> None:
        key = self._key(harness, cwd)
        self._by_key[key] = catalog
        self._failed.discard(key)

    async def get(self, s: Session) -> Catalog | None:
        key = self._key(s.spec.harness, s.spec.cwd)
        t = s.catalog_task
        if t is not None:
            await asyncio.wait([t])
            done = None if t.cancelled() else t.result()
            if done is not None:
                return done
            if not t.cancelled():
                self._failed.add(key)  # the live process gave none
        if key in self._by_key:
            return self._by_key[key]
        if key in self._failed:
            return None
        probe = self._probing.get(key)
        if probe is None:
            probe = asyncio.create_task(self._probe(s))
            self._probing[key] = probe
            probe.add_done_callback(lambda _: self._probing.pop(key, None))
        return await asyncio.shield(probe)

    async def _probe(self, s: Session) -> Catalog | None:
        key = self._key(s.spec.harness, s.spec.cwd)
        try:
            cat = await s.harness.probe(s.spec, self._stderr)
        except (control.ControlError, TimeoutError, OSError):
            self._failed.add(key)
            return None
        self.put(s.spec.harness, s.spec.cwd, cat)
        return cat
