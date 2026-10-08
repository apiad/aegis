"""``.aegis.yaml``: the one place aegis's configuration lives.

The server holds a parsed copy (``Snapshot``) and nothing else. ``Config.current``
stats the file and re-reads it when its (mtime_ns, size, inode) changed, so every
reader sees an edit made anywhere, by the Settings page or a text editor, with no
restart; ``watch`` calls it once a second so a change is published even when
nobody reads. A file that does not parse, or is empty, leaves the last one that
did in force, with the error in the snapshot: a half-typed edit, or an editor
that truncates before it writes, must not stop every spawn or fail every queued
task. A deleted file is honoured: no agents, no queues.

``write`` is the one writer, for ``aegis init`` and the Settings page. It
refuses a stale write (the file changed since the caller read it), validates
first and writes nothing on a problem, and edits the document in place in
ruamel's round-trip mode, so comments, key order, each agent's form
(``harness:``, ``provider:`` or a nested ``provider:`` map) and keys aegis does
not read survive.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from io import StringIO
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from ruamel.yaml import YAML, YAMLError
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import LiteralScalarString

from .agents import EFFORTS, HARNESSES, PERMISSION_ORDER, Agent, agents_from
from .ops import OpError
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


def stamp_token(stamp: Stamp | None) -> str | None:
    """The stamp as the browser carries it. A string, because st_mtime_ns is
    about 1.8e18 and a JavaScript number holds integers exactly only up to
    2**53: a numeric stamp came back rounded and every save looked stale."""
    return ":".join(map(str, stamp)) if stamp else None


def stamp_from(token: str | None) -> Stamp | None:
    if token is None:
        return None
    try:
        a, b, c = (int(x) for x in token.split(":"))
    except ValueError as e:
        raise OpError("bad_params", f"stamp {token!r} is not one aegis sent") from e
    return (a, b, c)


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
            "stamp": stamp_token(self.stamp),
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


def validate(doc: ConfigDoc) -> list[Finding]:
    """What stops ``doc`` being written: each a Finding on its Settings row."""
    out: list[Finding] = []

    def err(row: str, where: str, message: str) -> None:
        out.append(Finding("error", where, message, row))

    names = [a.name for a in doc.agents]
    for a in doc.agents:
        row = f"agents.{a.name}"
        if not a.name.strip():
            err(row, row, "an agent needs a name")
        elif names.count(a.name) > 1:
            err(row, row, f"two agents are named {a.name!r}")
        if a.harness and a.harness not in HARNESSES:
            err(
                row,
                f"{row}.harness",
                f"harness {a.harness!r} is not one of {', '.join(HARNESSES)}",
            )
            continue
        (agent,) = agents_from({"agents": {a.name: a.model_dump(exclude={"name"})}})
        if agent.error:
            err(row, row, agent.error)
    queues = [q.name for q in doc.queues]
    for q in doc.queues:
        row = f"queues.{q.name}"
        if not q.name.strip():
            err(row, row, "a queue needs a name")
        elif queues.count(q.name) > 1:
            err(row, row, f"two queues are named {q.name!r}")
        if q.agent not in names:
            err(
                row,
                f"{row}.agent",
                f"no agent named {q.agent!r}" if q.agent else "agent is missing",
            )
        if q.max_parallel is None or q.max_parallel < 1:
            err(row, f"{row}.max_parallel", "max_parallel is not a positive integer")
    if doc.default_agent and doc.default_agent not in names:
        err("default_agent", "default_agent", f"no agent named {doc.default_agent!r}")
    return out


def _put(node: dict, key: str, value: object) -> None:
    # Only a changed value is assigned, so an untouched one keeps its quotes.
    if node.get(key) != value:
        node[key] = value


def _section(data: CommentedMap, key: str) -> CommentedMap:
    node = data.get(key)
    if not isinstance(node, CommentedMap):
        node = data[key] = CommentedMap()
    return node


def _entries(data: CommentedMap, key: str, names: list[str]) -> CommentedMap:
    """The ``key`` map holding exactly ``names``: gone ones deleted, new ones
    appended at the end."""
    section = _section(data, key)
    for name in [k for k in section if k not in names]:
        del section[name]
    for name in names:
        if not isinstance(section.get(name), dict):
            section[name] = CommentedMap()
    return section


def _set_agent(node: CommentedMap, a: AgentDoc) -> None:
    provider = node.get("provider")
    if isinstance(provider, dict):
        _put(provider, "name", a.harness)
    elif isinstance(provider, str):
        _put(node, "provider", a.harness)
    else:
        _put(node, "harness", a.harness)
    for f in ("model", "effort", "permission"):
        holder = provider if isinstance(provider, dict) and f in provider else node
        _put(holder, f, getattr(a, f))
    if a.priming:
        if node.get("priming") != a.priming:
            node["priming"] = (
                LiteralScalarString(a.priming) if "\n" in a.priming else a.priming
            )
    else:
        node.pop("priming", None)


def _apply(data: CommentedMap, doc: ConfigDoc) -> None:
    if doc.default_agent:
        _put(data, "default_agent", doc.default_agent)
    else:
        data.pop("default_agent", None)
    if doc.agents:
        agents = _entries(data, "agents", [a.name for a in doc.agents])
        for a in doc.agents:
            _set_agent(agents[a.name], a)
    else:
        data.pop("agents", None)
    if doc.queues:
        queues = _entries(data, "queues", [q.name for q in doc.queues])
        for q in doc.queues:
            _put(queues[q.name], "agent", q.agent)
            _put(queues[q.name], "max_parallel", q.max_parallel)
    else:
        data.pop("queues", None)


def write(path: Path, doc: ConfigDoc, expected: Stamp | None) -> list[Finding]:
    """Write ``doc`` to ``path``. Returns the problems that stopped it; an
    empty list means it was written."""
    if stamp_of(path) != expected:
        raise OpError("stale", f"{path} changed on disk since it was read; reload it")
    if problems := validate(doc):
        return problems
    rt = YAML()
    rt.preserve_quotes = True
    data = None
    if expected is not None:
        try:
            data = rt.load(path.read_text())
        except YAMLError as e:
            raise OpError(
                "bad_config", f"{path} does not parse; fix it in an editor first: {e}"
            ) from e
    if not isinstance(data, CommentedMap):
        data = CommentedMap()
        data.yaml_set_start_comment("aegis configuration. `aegis doctor` checks it.")
    _apply(data, doc)
    buf = StringIO()
    rt.dump(data, buf)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(buf.getvalue())
    os.replace(tmp, path)
    return []
