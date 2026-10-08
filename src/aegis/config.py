"""``.aegis.yaml``: the one place aegis's configuration lives.

The server holds a parsed copy (``Snapshot``) and nothing else. ``Config.current``
stats the file and re-reads it when its (mtime_ns, size, inode) changed, so every
reader sees an edit made anywhere, by the Settings page or a text editor, with no
restart; ``watch`` calls it once a second so a change is published even when
nobody reads. A file that does not parse, or is empty, leaves the last one that
did in force, with the error in the snapshot: a half-typed edit, or an editor
that truncates before it writes, must not stop every spawn or fail every queued
task. A deleted file is honoured: no agents, no queues.

``write`` is the one writer, for ``aegis init`` and the Settings page.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from ruamel.yaml import YAML, YAMLError

from .agents import EFFORTS, HARNESSES, PERMISSION_ORDER, Agent, agents_from
from .queues import queues_from
from .roots import CONFIG_FILE

log = logging.getLogger("aegis.config")

WATCH_EVERY_S = 1.0
TOP_KEYS = ("agents", "queues", "default_agent")
AGENT_KEYS = ("harness", "provider", "model", "effort", "permission", "priming")
QUEUE_KEYS = ("agent", "max_parallel")

Stamp = tuple[int, int, int]


def stamp_of(path: Path) -> Stamp | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def load(path: Path) -> tuple[dict | None, str | None]:
    """The file's top-level mapping, or why it has none."""
    try:
        text = path.read_text()
        data = YAML(typ="safe").load(text)
    except (YAMLError, OSError, UnicodeDecodeError) as e:
        return None, f"{path}: {e}"
    if not text.strip():
        return None, f"{path}: the file is empty"
    if not isinstance(data, dict):
        return None, f"{path}: the top level is not a mapping"
    return data, None


@dataclass(frozen=True)
class Finding:
    level: Literal["ok", "warn", "error"]
    where: str  # "file", "agents.deepseek.model", "harness.opencode", "state"
    message: str
    # The Settings row it marks: "agents.<name>", "queues.<name>",
    # "default_agent", or None for the file, a harness and the state.
    row: str | None = None

    def wire(self) -> dict:
        return asdict(self)


class AgentDoc(BaseModel):
    model_config = {"extra": "forbid"}
    name: str
    harness: str = ""
    model: str = ""
    effort: str = ""
    permission: str = ""
    priming: str | None = None


class QueueDoc(BaseModel):
    model_config = {"extra": "forbid"}
    name: str
    agent: str = ""
    max_parallel: int | None = None


class ConfigDoc(BaseModel):
    """What the Settings form edits and ``aegis init`` proposes."""

    model_config = {"extra": "forbid"}
    agents: list[AgentDoc] = Field(default_factory=list)
    default_agent: str | None = None
    queues: list[QueueDoc] = Field(default_factory=list)


def doc_from(data: dict) -> ConfigDoc:
    agents = [
        AgentDoc(
            name=a.name,
            harness=a.harness,
            model=a.model,
            effort=a.effort,
            permission=a.permission,
            priming=a.priming,
        )
        for a in agents_from(data)
    ]
    queues = []
    raw = data.get("queues")
    for name, q in (raw if isinstance(raw, dict) else {}).items():
        q = q if isinstance(q, dict) else {}
        limit = q.get("max_parallel")
        ok = isinstance(limit, int) and not isinstance(limit, bool)
        queues.append(
            QueueDoc(
                name=str(name),
                agent=str(q.get("agent") or ""),
                max_parallel=limit if ok else None,
            )
        )
    d = data.get("default_agent")
    return ConfigDoc(agents=agents, default_agent=str(d) if d else None, queues=queues)


@dataclass(frozen=True)
class Snapshot:
    path: Path
    exists: bool = False
    stamp: Stamp | None = None
    agents: tuple[Agent, ...] = ()
    default_agent: str | None = None
    queues: dict[str, dict] = field(default_factory=dict)
    unknown_keys: tuple[str, ...] = ()
    doc: ConfigDoc = field(default_factory=ConfigDoc)
    # Why the file on disk is not the one in force, when it does not parse.
    error: str | None = None

    @classmethod
    def parse(cls, path: Path, stamp: Stamp | None, data: dict) -> Snapshot:
        d = data.get("default_agent")
        return cls(
            path=path,
            exists=True,
            stamp=stamp,
            agents=tuple(agents_from(data)),
            default_agent=str(d) if d else None,
            queues=queues_from(data.get("queues")),
            unknown_keys=tuple(str(k) for k in data if k not in TOP_KEYS),
            doc=doc_from(data),
        )

    def wire(self) -> dict:
        return {
            "path": str(self.path),
            "root": str(self.path.parent),
            "exists": self.exists,
            "stamp": list(self.stamp) if self.stamp else None,
            "error": self.error,
            "unknown_keys": list(self.unknown_keys),
            "doc": self.doc.model_dump(),
            "vocab": {
                "harnesses": list(HARNESSES),
                "efforts": list(EFFORTS),
                "permissions": list(PERMISSION_ORDER),
            },
        }


class Config:
    def __init__(
        self,
        config_root: Path,
        on_change: Callable[[Snapshot], None] = lambda s: None,
    ) -> None:
        self.path = config_root / CONFIG_FILE
        self._on_change = on_change
        self._snap = self._read(stamp_of(self.path), Snapshot(self.path))

    def _read(self, stamp: Stamp | None, last: Snapshot) -> Snapshot:
        if stamp is None:
            return Snapshot(self.path)
        data, error = load(self.path)
        if data is None:
            return replace(last, exists=True, stamp=stamp, error=error)
        return Snapshot.parse(self.path, stamp, data)

    def current(self) -> Snapshot:
        """The config in force: one ``stat``, and a re-read when it moved."""
        stamp = stamp_of(self.path)
        if stamp != self._snap.stamp:
            self._snap = self._read(stamp, self._snap)
            self._on_change(self._snap)
        return self._snap

    async def watch(self, every: float = WATCH_EVERY_S) -> None:
        while True:
            await asyncio.sleep(every)
            try:
                self.current()
            except Exception:
                log.exception("reading %s", self.path)
