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
import contextlib
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
from ruamel.yaml.util import load_yaml_guess_indent

from .agents import EFFORTS, HARNESSES, PERMISSION_ORDER, Agent, agents_from
from .cgroups import DEFAULT_MEMORY_MAX, parse_size
from .net.config import NetConfig, network_from
from .ops import OpError
from .queues import queues_from
from .roots import CONFIG_FILE
from .term_profiles import terminals_from

log = logging.getLogger("aegis.config")

WATCH_EVERY_S = 1.0
TOP_KEYS = (
    "agents", "queues", "default_agent", "recap", "context", "session_memory_max",
    "terminals", "terminal_memory_max", "park", "network", "workflows",
    "harness_versions",
)  # fmt: skip
AGENT_KEYS = (
    "harness", "provider", "model", "models", "effort", "permission", "priming",
    "keep_harness_tools",
)  # fmt: skip
QUEUE_KEYS = ("agent", "max_parallel", "quiet")

Stamp = tuple[int, int, int]

# When turn_end names the context size, and when the client warns that the
# prompt cache has gone cold (cache.py, #354).
CONTEXT_DEFAULTS = {"handoff_at": 300_000, "cold_after": 3600, "cold_min": 100_000}


def context_from(raw: object) -> dict:
    """The ``context:`` block over its defaults. A wrong block keeps every
    default in force and carries ``error`` for aegis doctor, so a typo never
    stops a turn_end."""
    out: dict = dict(CONTEXT_DEFAULTS)
    if raw is None:
        return out
    if not isinstance(raw, dict):
        return out | {
            "error": "context must be a mapping of handoff_at, cold_after, cold_min"
        }
    for k, v in raw.items():
        if k not in CONTEXT_DEFAULTS:
            return dict(CONTEXT_DEFAULTS) | {
                "error": f"context has no key {k!r}; it takes handoff_at, cold_after, cold_min"
            }
        positive = k == "cold_after"
        if isinstance(v, bool) or not isinstance(v, int) or v < (1 if positive else 0):
            kind = "positive" if positive else "non-negative"
            return dict(CONTEXT_DEFAULTS) | {
                "error": f"context.{k} {v!r} is not a {kind} integer"
            }
        out[k] = v
    return out


# How many workflow node sessions may be open at once on this server, every
# run together (workflows.py): ``total`` for all agents, and a number per
# agent alias for that alias alone. Both out-of-memory kills the workflows
# design measured came from too many sessions at once.
WORKFLOWS_DEFAULTS = {"max_agents": {"total": 4}}


def _positive(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def workflows_from(raw: object) -> dict:
    """The ``workflows:`` block over its defaults. ``max_agents`` is
    ``{total: N, <agent>: M, ...}``, or a bare N for ``{total: N}``; a
    mapping without ``total`` keeps the default total. A wrong block keeps
    the default in force and carries ``error`` for aegis doctor, as the
    context block does. Whether each alias names an agent is the doctor's
    to say, since the agents are another block."""
    default: dict = {"max_agents": dict(WORKFLOWS_DEFAULTS["max_agents"])}
    if raw is None:
        return default
    if not isinstance(raw, dict):
        return default | {"error": "workflows must be a mapping with max_agents"}
    m: object = default["max_agents"]
    for k, v in raw.items():
        if k != "max_agents":
            return default | {
                "error": f"workflows has no key {k!r}; it takes max_agents"
            }
        m = v
    if _positive(m):
        return {"max_agents": {"total": m}}
    if not isinstance(m, dict):
        return default | {
            "error": f"workflows.max_agents {m!r} is not {{total: N, <agent>: M}}"
        }
    for k, v in m.items():
        if not _positive(v):
            return default | {
                "error": f"workflows.max_agents.{k} {v!r} is not a positive integer"
            }
    return {"max_agents": default["max_agents"] | {str(k): v for k, v in m.items()}}


def park_from(raw: object) -> dict:
    """The ``park:`` block: each harness named in it, and the seconds one of its
    sessions must be safe to end before aegis ends its process (park.py). A
    harness it does not name never parks. A wrong block parks nothing and
    carries ``error`` for aegis doctor."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        return {"error": "park must be a mapping of harness to seconds"}
    out: dict = {}
    for k, v in raw.items():
        if k not in HARNESSES:
            return {
                "error": f"park has no harness {k!r}; it takes {', '.join(HARNESSES)}"
            }
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            return {"error": f"park.{k} {v!r} is not a positive number of seconds"}
        out[k] = v
    return out


def park_after(park: dict, harness: str) -> int | None:
    """The seconds before a session of ``harness`` parks; None when it never does."""
    return None if "error" in park else park.get(harness)


# What a launch on a harness version outside the tested range does
# (harness_versions.py): who started it picks the policy. ``accepted`` lists,
# per harness, versions a person allowed for good.
HV_DEFAULTS: dict = {
    "person": "ask",
    "agent": "warn",
    "startup": "warn",
    "accepted": {},
}
HV_VALUES = {
    "person": ("ask", "warn", "off"),
    "agent": ("warn", "off"),
    "startup": ("warn", "off"),
}


def _hv_defaults() -> dict:
    return HV_DEFAULTS | {"accepted": {}}


def harness_versions_from(raw: object) -> dict:
    """The ``harness_versions:`` block over its defaults. A wrong block keeps
    every default in force and carries ``error``, as the context block does:
    a typo must not start asking, or stop asking, at every launch."""
    out = _hv_defaults()
    if raw is None:
        return out
    if not isinstance(raw, dict):
        return out | {
            "error": "harness_versions must be a mapping of person, agent, startup, accepted"
        }
    for k, v in raw.items():
        if k == "accepted":
            if v is None:
                continue
            if not isinstance(v, dict):
                return _hv_defaults() | {
                    "error": "harness_versions.accepted must map a harness to a list of versions"
                }
            for h, versions in v.items():
                if h not in HARNESSES:
                    return _hv_defaults() | {
                        "error": f"harness_versions.accepted has no harness {h!r};"
                        f" it takes {', '.join(HARNESSES)}"
                    }
                if not isinstance(versions, list):
                    return _hv_defaults() | {
                        "error": f"harness_versions.accepted.{h} is not a list of versions"
                    }
                out["accepted"][h] = [str(x) for x in versions]
        elif isinstance(k, str) and k in HV_VALUES:
            if k == "agent" and v == "ask":
                return _hv_defaults() | {
                    "error": "harness_versions.agent cannot be ask: nobody is there"
                    " to answer when an agent starts a session; it takes warn, off"
                }
            if v not in HV_VALUES[k]:
                return _hv_defaults() | {
                    "error": f"harness_versions.{k} {v!r} is not one of"
                    f" {', '.join(HV_VALUES[k])}"
                }
            out[k] = v
        else:
            return _hv_defaults() | {
                "error": f"harness_versions has no key {k!r};"
                " it takes person, agent, startup, accepted"
            }
    return out


def session_memory_max_from(
    raw: object, key: str = "session_memory_max"
) -> tuple[object, str | None]:
    """A memory cap for one child's cgroup (cgroups.py) and, for a value that
    is not a size, why; a wrong one keeps the default, as the context block
    does. Sessions and terminals each have one."""
    if raw is None:
        return DEFAULT_MEMORY_MAX, None
    try:
        parse_size(raw, 1)
    except ValueError as e:
        return DEFAULT_MEMORY_MAX, f"{key}: {e}"
    return raw, None


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
    # The cascade, when the agent names two or more models; empty for a
    # one-model agent, which uses ``model``.
    models: list[str] = Field(default_factory=list)
    effort: str = ""
    permission: str = ""
    priming: str | None = None


class QueueDoc(BaseModel):
    model_config = {"extra": "forbid"}
    name: str
    agent: str = ""
    max_parallel: int | None = None


class HarnessVersionsDoc(BaseModel):
    model_config = {"extra": "forbid"}
    person: str = HV_DEFAULTS["person"]
    agent: str = HV_DEFAULTS["agent"]
    startup: str = HV_DEFAULTS["startup"]
    accepted: dict[str, list[str]] = Field(default_factory=dict)


class ConfigDoc(BaseModel):
    """What the Settings form edits and ``aegis init`` proposes."""

    model_config = {"extra": "forbid"}
    agents: list[AgentDoc] = Field(default_factory=list)
    default_agent: str | None = None
    queues: list[QueueDoc] = Field(default_factory=list)
    park: dict[str, int] = Field(default_factory=dict)
    # The workflows: block's max_agents: total and a bound per agent alias;
    # empty when the file has no block, or one the reader refuses.
    max_agents: dict[str, int] = Field(default_factory=dict)
    # The harness_versions: block; the defaults when the file has none, or
    # one the reader refuses.
    harness_versions: HarnessVersionsDoc = Field(default_factory=HarnessVersionsDoc)


def doc_from(data: dict) -> ConfigDoc:
    agents = [
        AgentDoc(
            name=a.name,
            harness=a.harness,
            model=a.model,
            models=list(a.models) if len(a.models) > 1 else [],
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
    w = data.get("workflows")
    pool = workflows_from(w)
    hv = harness_versions_from(data.get("harness_versions"))
    return ConfigDoc(
        agents=agents,
        default_agent=str(d) if d else None,
        queues=queues,
        park={k: v for k, v in park_from(data.get("park")).items() if k != "error"},
        max_agents={} if w is None or "error" in pool else pool["max_agents"],
        harness_versions=HarnessVersionsDoc(
            **{k: v for k, v in hv.items() if k != "error"}
        ),
    )


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
    context: dict = field(default_factory=lambda: dict(CONTEXT_DEFAULTS))
    workflows: dict = field(default_factory=lambda: dict(WORKFLOWS_DEFAULTS))
    park: dict = field(default_factory=dict)
    harness_versions: dict = field(default_factory=_hv_defaults)
    session_memory_max: object = DEFAULT_MEMORY_MAX
    session_memory_error: str | None = None
    terminals: dict[str, dict] = field(default_factory=dict)
    terminals_error: str | None = None
    terminal_memory_max: object = DEFAULT_MEMORY_MAX
    terminal_memory_error: str | None = None
    network: NetConfig = field(default_factory=NetConfig)
    network_error: str | None = None
    # Why the file on disk is not the one in force, when it does not parse.
    error: str | None = None

    @classmethod
    def parse(cls, path: Path, stamp: Stamp | None, data: dict) -> Snapshot:
        d = data.get("default_agent")
        mem, mem_error = session_memory_max_from(data.get("session_memory_max"))
        terms, terms_error = terminals_from(data.get("terminals"))
        tmem, tmem_error = session_memory_max_from(
            data.get("terminal_memory_max"), "terminal_memory_max"
        )
        network, network_error = network_from(data.get("network"))
        return cls(
            path=path,
            exists=True,
            stamp=stamp,
            agents=tuple(agents_from(data)),
            default_agent=str(d) if d else None,
            queues=queues_from(data.get("queues")),
            unknown_keys=tuple(str(k) for k in data if k not in TOP_KEYS),
            doc=doc_from(data),
            context=context_from(data.get("context")),
            workflows=workflows_from(data.get("workflows")),
            park=park_from(data.get("park")),
            harness_versions=harness_versions_from(data.get("harness_versions")),
            session_memory_max=mem,
            session_memory_error=mem_error,
            terminals=terms,
            terminals_error=terms_error,
            terminal_memory_max=tmem,
            terminal_memory_error=tmem_error,
            network=network,
            network_error=network_error,
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
        raw = a.model_dump(exclude={"name", "models" if not a.models else "model"})
        (agent,) = agents_from({"agents": {a.name: raw}})
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
    if "error" in (p := park_from(doc.park or None)):
        out.append(Finding("error", "park", p["error"]))
    hv = harness_versions_from(doc.harness_versions.model_dump())
    if "error" in hv:
        out.append(
            Finding("error", "harness_versions", hv["error"], "harness_versions")
        )
    if doc.max_agents:
        # The rules aegis doctor applies to the block, each on its row.
        pool = workflows_from({"max_agents": doc.max_agents})
        if "error" in pool:
            err("workflows", "workflows", pool["error"])
        else:
            m = pool["max_agents"]
            for k, n in m.items():
                if k == "total":
                    continue
                where = f"workflows.max_agents.{k}"
                if k not in names:
                    err(f"workflows.{k}", where, f"no agent named {k!r}")
                elif n > m["total"]:
                    err(f"workflows.{k}", where, f"{k} {n} is above total {m['total']}")
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
    for f in ("effort", "permission"):
        holder = provider if isinstance(provider, dict) and f in provider else node
        _put(holder, f, getattr(a, f))
    # Two or more models are written as models:, one as model:.
    held = isinstance(provider, dict) and ("model" in provider or "models" in provider)
    holder = provider if held else node
    if len(a.models) > 1:
        holder.pop("model", None)
        _put(holder, "models", list(a.models))
    else:
        holder.pop("models", None)
        _put(holder, "model", a.model)
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
    if doc.park:
        park = _section(data, "park")
        for h in list(park):
            if h not in doc.park:
                del park[h]
        for h, s in doc.park.items():
            _put(park, h, s)
    else:
        data.pop("park", None)
    block = data.get("workflows")
    if doc.max_agents:
        want = workflows_from({"max_agents": doc.max_agents})["max_agents"]
        # An untouched block compares equal and keeps its style and comments.
        if workflows_from(block).get("max_agents") != want:
            _section(data, "workflows")["max_agents"] = dict(doc.max_agents)
    elif block is not None and "error" not in workflows_from(block):
        del data["workflows"]  # every bound back to its default
    _apply_harness_versions(data, doc.harness_versions)


def _apply_harness_versions(data: CommentedMap, hv: HarnessVersionsDoc) -> None:
    block = data.get("harness_versions")
    have = harness_versions_from(block)
    want = hv.model_dump()
    want["accepted"] = {h: v for h, v in want["accepted"].items() if v}
    if "error" in have and want == _hv_defaults():
        return  # a block the reader refuses, untouched by this edit: keep it
    if {k: v for k, v in have.items() if k != "error"} == want:
        return  # untouched: keep its style and comments
    if want == _hv_defaults():
        data.pop("harness_versions", None)
        return
    node = _section(data, "harness_versions")
    for k in ("person", "agent", "startup"):
        if want[k] == HV_DEFAULTS[k]:
            node.pop(k, None)
        else:
            _put(node, k, want[k])
    if want["accepted"]:
        acc = _section(node, "accepted")
        for h in [h for h in acc if h not in want["accepted"]]:
            del acc[h]
        for h, versions in want["accepted"].items():
            if list(acc.get(h) or []) != versions:
                acc[h] = list(versions)
    else:
        node.pop("accepted", None)


def write(path: Path, doc: ConfigDoc, expected: Stamp | None) -> list[Finding]:
    """Write ``doc`` to ``path``. Returns the problems that stopped it; an
    empty list means it was written."""
    if stamp_of(path) != expected:
        raise OpError("stale", f"{path} changed on disk since it was read; reload it")
    if problems := validate(doc):
        return problems
    if expected is not None and (error := load(path)[1]):
        # The writer refuses what the reader refuses: a list at the top, a tag
        # the safe loader rejects, a comment-only file. Writing over one would
        # drop text the person has not seen the server read.
        raise OpError("bad_config", f"{error}; fix it in an editor first")
    rt = YAML()
    rt.preserve_quotes = True
    data = None
    if expected is not None:
        try:
            text = path.read_text()
            data = rt.load(text)
            # A list keeps the file's own indentation, such as a models: list
            # written by hand with its dashes indented.
            _, seq, offset = load_yaml_guess_indent(text)
            if offset is not None:
                rt.indent(mapping=2, sequence=seq, offset=offset)
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


def accept_version(path: Path, harness: str, version: str) -> None:
    """Add ``version`` to ``harness_versions.accepted.<harness>``: what a
    person's "Always" does. Only that block is edited, so a finding elsewhere
    in the file (a broken queue) does not stop it. Raises OpError("stale")
    when the file moves under it, OpError("bad_config") when it cannot be
    written as it stands, and OpError("unwritable") when the disk refuses."""
    stamp = stamp_of(path)
    rt = YAML()
    rt.preserve_quotes = True
    data = None
    if stamp is not None:
        if error := load(path)[1]:
            raise OpError("bad_config", f"{error}; fix it in an editor first")
        try:
            data = rt.load(path.read_text())
        except YAMLError as e:
            raise OpError("bad_config", f"{path} does not parse: {e}") from e
    if not isinstance(data, CommentedMap):
        data = CommentedMap()
        data.yaml_set_start_comment("aegis configuration. `aegis doctor` checks it.")
    hv = harness_versions_from(data.get("harness_versions"))
    if "error" in hv:
        raise OpError("bad_config", hv["error"])
    if version in hv["accepted"].get(harness, []):
        return
    hv["accepted"].setdefault(harness, []).append(version)
    _apply_harness_versions(data, HarnessVersionsDoc(**hv))
    buf = StringIO()
    rt.dump(data, buf)
    if stamp_of(path) != stamp:
        raise OpError("stale", f"{path} changed on disk while it was edited; try again")
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(buf.getvalue())
        os.replace(tmp, path)
    except OSError as e:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise OpError("unwritable", f"{path} cannot be written: {e}") from e
